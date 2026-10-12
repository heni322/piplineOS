"""Idempotency keys, transactional outbox, LLM call recording, migrations up/down on a fresh database."""

from __future__ import annotations

import subprocess
import sys
import uuid

import pytest
from sqlalchemy import text

from app.core.errors import ProblemError
from app.core.idempotency import run_idempotent
from app.db.session import DbContext, transaction
from app.workflows.outbox import relay_batch
from tests.conftest import ADMIN_URL, BACKEND
from tests.factories import make_tenant

pytestmark = pytest.mark.db


def test_idempotent_replay_and_conflict(clean_db) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    calls = []

    def effect() -> tuple[int, dict[str, str]]:
        calls.append(1)
        return 201, {"booking": "b-1"}

    for _ in range(3):
        with transaction(DbContext.system(t), clean_db) as conn:
            assert run_idempotent(conn, tenant_id=t, scope="book", key="k1", request={"slot": 1}, fn=effect) == (
                201,
                {"booking": "b-1"},
            )
    assert len(calls) == 1
    with pytest.raises(ProblemError) as e, transaction(DbContext.system(t), clean_db) as conn:
        run_idempotent(conn, tenant_id=t, scope="book", key="k1", request={"slot": 2}, fn=effect)
    assert e.value.status == 422
    with pytest.raises(ProblemError), transaction(DbContext.system(t), clean_db) as conn:
        run_idempotent(conn, tenant_id=t, scope="book", key=None, request={}, fn=effect)


def test_outbox_relays_once_and_retries_failures(clean_db) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    with transaction(DbContext.system(t), clean_db) as conn:
        for i in range(3):
            conn.execute(
                text("INSERT INTO outbox (tenant_id, topic, payload) VALUES (:t, 'x', CAST(:p AS jsonb))"),
                {"t": t, "p": f'{{"i": {i}}}'},
            )
    seen: list[int] = []
    failed_once: list[bool] = []

    def flaky(topic: str, payload: dict) -> None:  # type: ignore[type-arg]
        if payload["i"] == 1 and not failed_once:
            failed_once.append(True)
            raise RuntimeError("broker down")
        seen.append(payload["i"])

    assert relay_batch(clean_db, flaky) == 2
    assert relay_batch(clean_db, flaky) == 1
    assert relay_batch(clean_db, flaky) == 0
    assert sorted(seen) == [0, 1, 2]


def test_llm_calls_are_recorded_with_prompt_version(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    import json

    from pydantic import BaseModel

    from app.llm.backends import MockBackend
    from app.llm.client import LLMClient
    from app.llm.prompts import load_prompt
    from app.llm.recorder import db_recorder

    class Out(BaseModel):
        ok: bool

    t = make_tenant(clean_db)
    prompt = load_prompt("common/untrusted_data_policy.v1.md")
    c = LLMClient(
        MockBackend(["{}", json.dumps({"ok": True})]),
        {"volume": "claude-sonnet-5-5"},
        db_recorder(clean_db, cipher, log_bodies=True),
        sleep=lambda s: None,
    )
    assert c.run_structured(prompt=prompt, schema=Out, user_content="hello", tenant_id=t, agent="test").ok
    with transaction(DbContext.system(t), clean_db) as conn:
        rows = conn.execute(
            text(
                "SELECT outcome, prompt_id, prompt_version, prompt_hash, cost_micros_gbp, request_enc "
                "FROM llm_calls ORDER BY attempt"
            )
        ).all()
        assert [r.outcome for r in rows] == ["invalid_retry", "ok"]
        assert rows[1].prompt_id == "common.untrusted_data_policy" and rows[1].prompt_version == 1
        assert len(rows[1].prompt_hash) == 64 and rows[1].cost_micros_gbp is None  # no verified price configured
        assert cipher.decrypt(conn, t, "llm_calls.request", rows[1].request_enc) == "hello"


def test_migrations_apply_and_roll_back_on_a_fresh_database(database) -> None:  # type: ignore[no-untyped-def]
    import psycopg

    db = f"pipelineos_mig_{uuid.uuid4().hex[:6]}"
    host = ADMIN_URL.split("@", 1)[1].rsplit("/", 1)[0]
    url = f"postgresql+psycopg://pipelineos_owner:owner_dev_only@{host}/{db}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{db}" OWNER pipelineos_owner')
    try:
        with psycopg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{db}", autocommit=True) as c:
            c.execute("CREATE EXTENSION vector; CREATE EXTENSION pgcrypto;")
        for args in (
            ["upgrade", "head"],
            ["downgrade", "base"],
            ["upgrade", "head"],
            ["downgrade", "0002"],
            ["upgrade", "head"],
        ):
            subprocess.run(
                [sys.executable, "-m", "alembic", "-x", f"url={url}", *args],
                cwd=BACKEND,
                check=True,
                capture_output=True,
            )
        with psycopg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{db}") as c:
            n = c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()
            assert n is not None and n[0] >= 46
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
