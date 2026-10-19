"""Source health: one failing signal source never stops the others, every run is recorded (error type and HTTP
status only), the operator sees healthy / degraded / failing per source, and monitoring gets a metric to alert on."""

from __future__ import annotations

from datetime import UTC, datetime

import pyotp
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.adapters.base import RateLimited
from app.adapters.http import ProviderError
from app.adapters.signals.base import RawSignal
from app.agents.trigger_hunter.agent import SourceBinding
from app.db.session import DbContext, transaction
from app.demo.seed import providers, seed_demo
from app.domain.sources.health import record_run, summary
from app.workflows.pipeline import agent_context, hunt_active_campaigns, run_campaign

pytestmark = pytest.mark.db
SINCE = datetime(2026, 10, 1, tzinfo=UTC)


class Broken:
    name = "broken.test"

    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        raise ProviderError(400, "field 'winner-name' unknown; secret body text")


class Limited:
    name = "limited.test"

    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        raise RateLimited("60")


class Partial:
    name = "partial.test"
    failed = ["greenhouse:gone"]

    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        return [
            RawSignal("Fine Ltd", None, "GB", "job_posting", "Hiring", "IT support service desk",
                      "https://jobs.example/1", SINCE, "p1")
        ]  # fmt: skip


def runs(engine):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(), engine) as conn:
        return {
            r.source: r
            for r in conn.execute(text("SELECT source, outcome, fetched, error, detail FROM source_runs")).all()
        }


def test_a_failing_source_is_recorded_and_the_others_still_run(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    p = providers()
    p.sources = [SourceBinding(Broken(), ""), SourceBinding(Limited(), ""), SourceBinding(Partial(), "")]
    run = run_campaign(ctx, p, since=SINCE)
    assert run.hunt.source_failures == 1 and run.hunt.rate_limited == 1 and run.hunt.fetched == 1
    r = runs(clean_db)
    assert r["broken.test"].outcome == "failed" and r["broken.test"].error == "ProviderError: HTTP 400"
    assert "secret" not in str(r["broken.test"])  # never the response body
    assert r["limited.test"].outcome == "rate_limited"
    assert r["partial.test"].outcome == "partial" and r["partial.test"].fetched == 1
    assert r["partial.test"].detail == {"failed": ["greenhouse:gone"]}


def test_one_clients_failure_does_not_stop_the_scheduled_hunt(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    seed_demo(clean_db, settings)
    calls: list[str] = []

    def factory(s, ctx):  # type: ignore[no-untyped-def]
        calls.append(str(ctx.tenant_id))
        raise RuntimeError("misconfigured")

    totals = hunt_active_campaigns(clean_db, settings, cipher, providers_factory=factory)
    assert calls and totals["failed"] == len(calls) and totals["campaigns"] == 0


def test_health_levels(clean_db) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    record_run(clean_db, "a.ok", started_at=now, outcome="ok", fetched=5)
    record_run(clean_db, "b.flaky", started_at=now, outcome="ok", fetched=2)
    record_run(clean_db, "b.flaky", started_at=now, outcome="failed", error="ProviderError: HTTP 503")
    record_run(clean_db, "c.never", started_at=now, outcome="failed", error="ConnectError")
    with transaction(DbContext.system(), clean_db) as conn:
        h = {x.source: x for x in summary(conn)}
    assert h["a.ok"].status == "healthy" and h["a.ok"].fetched_24h == 5
    assert h["b.flaky"].status == "degraded" and h["b.flaky"].last_success_at is not None
    assert h["c.never"].status == "failing" and h["c.never"].last_error == "ConnectError"


def test_operator_api_and_metrics(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.api.deps import settings_dep
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    record_run(clean_db, "ted.live", started_at=datetime.now(UTC), outcome="failed", error="ProviderError: HTTP 400")
    app = create_app()
    app.dependency_overrides[settings_dep] = lambda: settings.model_copy(update={"metrics_token": SecretStr("m" * 32)})
    c = TestClient(app)
    assert c.get("/api/v1/sources/health").status_code == 401
    r = c.post(
        "/api/v1/auth/login",
        json={
            "email": "op@pipelineos.example",
            "password": demo.operator_password,
            "totp_code": pyotp.TOTP(demo.operator_totp_secret or "").now(),
        },
    )
    assert r.status_code == 200
    [h] = c.get("/api/v1/sources/health").json()
    assert h["source"] == "ted.live" and h["status"] == "failing" and h["last_error"] == "ProviderError: HTTP 400"
    m = TestClient(app).get("/metrics", headers={"Authorization": "Bearer " + "m" * 32}).text
    assert 'pipelineos_source_failing{source="ted.live"} 1.0' in m
    assert 'pipelineos_source_failures_24h{source="ted.live"} 1.0' in m


def test_a_source_that_answers_but_never_finds_anything_is_reported_silent(clean_db) -> None:  # type: ignore[no-untyped-def]
    """Seen live (2026-10-19): Contracts Finder's OCDS search answered 200 with no releases for every query."""
    from app.domain.sources.health import SILENT_MIN_RUNS

    now = datetime.now(UTC)
    for _ in range(SILENT_MIN_RUNS):
        record_run(clean_db, "contracts_finder.live", started_at=now, outcome="ok", fetched=0)
    for _ in range(SILENT_MIN_RUNS - 1):
        record_run(clean_db, "young.source", started_at=now, outcome="ok", fetched=0)  # too few runs to judge
    record_run(clean_db, "busy.source", started_at=now, outcome="ok", fetched=3)
    with transaction(DbContext.system(), clean_db) as conn:
        h = {x.source: x for x in summary(conn)}
    assert h["contracts_finder.live"].status == "silent" and h["contracts_finder.live"].runs_7d == SILENT_MIN_RUNS
    assert h["young.source"].status == "healthy" and h["busy.source"].status == "healthy"
