"""Failed sends are shown to the operator with the reason in plain words, and "Run all checks" says what works and
what to fix. Real gateway failures (provider error, crash mid-send, expired mailbox connection); no network."""

from __future__ import annotations

import httpx
import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.base import RateLimited
from app.adapters.email.mock import MockEmailSender
from app.db.session import DbContext, transaction
from app.domain import system_checks
from app.domain.outbound import problems
from app.domain.outbound.gateway import OutboundGateway, reconcile_stuck_sends
from tests.factories import enable, make_lead, make_operator, make_tenant
from tests.integration.test_gateway import IN_HOURS, approved_message

pytestmark = pytest.mark.db


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    op = make_operator(clean_db, settings, cipher)
    enable(clean_db, op, "GB")
    return op


def _sandbox(settings):  # type: ignore[no-untyped-def]
    return settings.model_copy(update={"email_adapter_mode": "sandbox"})


def _problems(engine):  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator"), engine) as conn:
        return problems.sending_problems(conn), problems.error_count(conn)


def test_a_provider_error_and_a_crash_are_listed_with_their_reason(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant(clean_db)
    lead1 = make_lead(clean_db, settings, cipher, tenant)
    lead2 = make_lead(clean_db, settings, cipher, tenant)
    failing = MockEmailSender(fail_with=RateLimited("429"))
    g = OutboundGateway(_sandbox(settings), clean_db, cipher, sandbox_sender=failing, clock=lambda: IN_HOURS)
    assert g.send_message(tenant, approved_message(clean_db, settings, cipher, lead1)).status == "retry_later"

    mid2 = approved_message(clean_db, settings, cipher, lead2)
    crashing = MockEmailSender(fail_with=ConnectionResetError("worker died"))
    g2 = OutboundGateway(_sandbox(settings), clean_db, cipher, sandbox_sender=crashing, clock=lambda: IN_HOURS)
    with pytest.raises(ConnectionResetError):
        g2.send_message(tenant, mid2)
    with transaction(DbContext.system(tenant), clean_db) as conn:
        conn.execute(
            text("UPDATE messages SET sending_started_at = now() - interval '11 minutes' WHERE id = :m"), {"m": mid2}
        )
    assert reconcile_stuck_sends(clean_db, tenant, 10) == 1

    rows, errors = _problems(clean_db)
    assert errors == 2
    by_status = {r["status"]: r for r in rows}
    retry = by_status["failed_retryable"]
    assert retry["severity"] == "error" and "tried again" in retry["what"]
    assert retry["reasons"][0]["code"] == "RateLimited" and "limiting" in retry["reasons"][0]["text"]
    stuck = by_status["needs_reconciliation"]
    assert "NOT resent" in stuck["what"] and "Sent folder" in stuck["fix"]
    assert all("@" not in str(r) for r in rows)  # never the recipient's address


def test_an_expired_mailbox_connection_is_an_error_to_fix(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant(clean_db)
    lead = make_lead(clean_db, settings, cipher, tenant)
    mid = approved_message(clean_db, settings, cipher, lead)
    with transaction(DbContext.system(tenant), clean_db) as conn:
        conn.execute(text("UPDATE inboxes SET oauth_status = 'expired' WHERE id = :i"), {"i": lead.inbox_id})
    g = OutboundGateway(_sandbox(settings), clean_db, cipher, sandbox_sender=MockEmailSender(), clock=lambda: IN_HOURS)
    g.send_message(tenant, mid)
    rows, errors = _problems(clean_db)
    (row,) = rows
    assert errors == 1 and row["severity"] == "error" and row["reasons"][0]["code"] == "INBOX_OAUTH_INVALID"
    assert "Reconnect" in row["reasons"][0]["fix"]
    with transaction(DbContext(role="operator"), clean_db) as conn:
        checks = system_checks.check_mailboxes(conn)
    assert any(c.status == "fail" and "Reconnect" in c.fix for c in checks)


def test_explanations_for_known_and_unknown_codes() -> None:
    sev, what, _, reasons = problems.explain("blocked_at_send", "SUPPRESSED_GLOBAL,SOMETHING_NEW")
    assert sev == "warning" and "final check" in what
    assert reasons[0]["text"].startswith("The address is on the do-not-contact list")
    assert reasons[1] == {"code": "SOMETHING_NEW", "text": "Something new", "fix": ""}
    assert problems.explain("queued", "OUTSIDE_BUSINESS_HOURS")[0] == "warning"
    assert problems.explain("queued", "INBOX_UNAVAILABLE")[0] == "error"


def _gemini(status: int, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status, json={})

    return httpx.MockTransport(handler)


def test_ai_check_proves_the_gemini_key_and_model_without_spending_tokens(settings) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    s = settings.model_copy(
        update={
            "llm_adapter_mode": "sandbox",
            "llm_sandbox_provider": "gemini",
            "gemini_api_key": SecretStr("test-key-123"),
            "gemini_model": "gemini-flash-latest",
        }
    )
    seen: list[httpx.Request] = []
    ok = system_checks.check_ai(s, _gemini(200, seen))
    assert ok.status == "ok" and seen[0].method == "GET" and seen[0].url.path.endswith("/models/gemini-flash-latest")
    key_sent = seen[0].headers["x-goog-api-key"]
    assert key_sent == "test-key-123" and "test-key-123" not in str(seen[0].url)  # in a header, never in the URL
    assert system_checks.check_ai(s, _gemini(404, [])).status == "fail"
    bad_key = system_checks.check_ai(s, _gemini(403, []))
    assert bad_key.status == "fail" and "aistudio" in bad_key.fix
    assert system_checks.check_ai(s.model_copy(update={"gemini_api_key": None}), _gemini(200, [])).status == "fail"
    assert system_checks.check_ai(settings.model_copy(update={"llm_adapter_mode": "mock"})).status == "info"


def test_worker_and_schema_checks(clean_db) -> None:  # type: ignore[no-untyped-def]
    assert system_checks.check_worker(lambda: [{"w1": {"ok": "pong"}}]).status == "ok"
    down = system_checks.check_worker(lambda: [])
    assert down.status == "fail" and "celery" in down.fix

    def broken() -> list[object]:
        raise ConnectionError("broker down")

    assert system_checks.check_worker(broken).status == "fail"
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert system_checks.check_schema(conn).status == "ok"  # the test database is migrated to head


def test_api_runs_all_checks_and_lists_problems_for_operators_only(clean_db, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.demo.seed import seed_demo
    from app.main import create_app
    from tests.security.test_golive_prep import _login

    auth_mod._ip_buckets.clear()
    monkeypatch.setattr(system_checks, "_worker_ping", lambda: [])
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    app = create_app()
    op = _login(TestClient(app), "op@pipelineos.example", demo.operator_password or "",
                pyotp.TOTP(demo.operator_totp_secret or "").now())  # fmt: skip
    r = op.post("/api/v1/system-checks")
    assert r.status_code == 200
    keys = {c["key"]: c for c in r.json()["checks"]}
    assert {"schema", "valkey", "worker", "ai", "places", "sending_mode", "sending_problems"} <= set(keys)
    assert keys["schema"]["status"] == "ok" and keys["worker"]["status"] == "fail" and r.json()["ok"] is False
    assert op.get("/api/v1/sending-problems").json()["errors_last_7_days"] == 0
    client = _login(TestClient(app), demo.client_email, demo.client_password or "")
    assert client.post("/api/v1/system-checks").status_code == 403
    assert client.get("/api/v1/sending-problems").status_code == 403
