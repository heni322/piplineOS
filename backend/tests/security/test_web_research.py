"""Web research (Gemini + Google Search): nothing reaches Google unless the gate is live (three keys), never with
FREE_ONLY or without a key; each request is counted under the monthly cap first; the answer goes back to the operator
and is never stored (only the fact that a question was asked is audited); client accounts cannot use it.
In-process transports only."""

from __future__ import annotations

import json

import httpx
import pyotp
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.adapters.base import AdapterNotConfigured
from app.adapters.gemini_search import GeminiSearch, parse
from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.domain.paid.gate import PaidGate
from app.domain.paid.places import usage
from app.domain.paid.research import PROVIDER, GatedResearch, research_for
from tests.security.test_golive_prep import _login

pytestmark = pytest.mark.db

ANSWER = {
    "candidates": [{
        "content": {"parts": [{"text": "thinking...", "thought": True},
                              {"text": "Exemple Infogérance SAS is an IT support company in Lyon; its website is "
                                       "exemple-infogerance.example."}]},
        "groundingMetadata": {
            "webSearchQueries": ["Exemple Infogérance SAS Lyon site officiel"],
            "groundingChunks": [
                {"web": {"uri": "https://exemple-infogerance.example/", "title": "exemple-infogerance.example"}},
                {"web": {"uri": "https://exemple-infogerance.example/", "title": "duplicate"}},
                {"web": {"uri": "javascript:alert(1)", "title": "bad"}},
            ],
            "searchEntryPoint": {"renderedContent": "<div class=\"chips\"><a href=\"https://www.google.com/search?q=x\">x</a></div>"},
        },
    }]
}  # fmt: skip
QUESTION = "What is the official website of Exemple Infogérance SAS, Lyon?"


def api(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=ANSWER)

    return httpx.MockTransport(handler)


def gated(clean_db, settings, cipher, mode: str, seen: list[httpx.Request], cap: int = 2) -> GatedResearch:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    gate = PaidGate(clean_db, cipher, settings.key("blind_index_key"), demo.tenant_id, mode)  # type: ignore[arg-type]
    return GatedResearch(GeminiSearch("real-key", "gemini-3.8-flash", transport=api(seen)), gate, clean_db, cap=cap)


def _operator(engine, tenant_id):  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator", tenant_id=tenant_id), engine) as conn:
        return conn.execute(text("SELECT id FROM users ORDER BY role = 'operator' DESC LIMIT 1")).scalar()


def test_parse_keeps_the_answer_sources_and_suggestions() -> None:
    a = parse(ANSWER)
    assert a.text.startswith("Exemple Infogérance SAS is an IT support company") and "thinking" not in a.text
    assert [s.url for s in a.sources] == ["https://exemple-infogerance.example/"]  # deduplicated, no javascript:
    assert a.queries == ["Exemple Infogérance SAS Lyon site officiel"] and a.suggestions_html.startswith("<div")
    assert parse({}).text == "" and parse({}).sources == []


def test_live_request_shape_counted_under_the_cap_and_nothing_stored(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []
    r = gated(clean_db, settings, cipher, "live", seen, cap=1)
    op = _operator(clean_db, r.gate.tenant_id)
    first = r.ask(QUESTION, op)
    assert first.called and first.answer is not None and first.answer.sources
    req = seen[0]
    assert req.url.path == "/v1beta/models/gemini-3.8-flash:generateContent" and "real-key" not in str(req.url)
    assert req.headers["x-goog-api-key"] == "real-key"
    assert json.loads(req.content)["tools"] == [{"google_search": {}}]
    second = r.ask(QUESTION, op)
    assert not second.called and second.reason == "cap_reached" and len(seen) == 1
    assert usage(clean_db, PROVIDER) == 1
    with transaction(DbContext(role="operator"), clean_db) as conn:
        audits = conn.execute(text("SELECT after::text FROM audit_log WHERE action = 'research.web_question'")).all()
        assert len(audits) == 2 and all("Exemple" not in a[0] for a in audits)  # never the question or the answer
        assert conn.execute(text("SELECT count(*) FROM dry_run_log")).scalar() == 0


def test_before_go_live_nothing_reaches_google(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    for mode in ("dry_run", "sandbox"):
        seen: list[httpx.Request] = []
        r = gated(clean_db, settings, cipher, mode, seen)
        out = r.ask(QUESTION, _operator(clean_db, r.gate.tenant_id))
        assert not out.called and out.reason == "dry_run" and seen == []
    with transaction(DbContext.system(r.gate.tenant_id), clean_db) as conn:
        assert set(conn.execute(text("SELECT adapter FROM dry_run_log")).scalars()) == {PROVIDER}
    assert usage(clean_db, PROVIDER) == 0


def test_never_built_free_only_or_without_a_key(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    with pytest.raises(AdapterNotConfigured, match="FREE_ONLY"):
        research_for(settings.model_copy(update={"free_only": True}), clean_db, cipher, demo.tenant_id)
    with pytest.raises(AdapterNotConfigured, match="GEMINI_API_KEY"):
        research_for(settings.model_copy(update={"free_only": False}), clean_db, cipher, demo.tenant_id)
    s = settings.model_copy(update={"free_only": False, "gemini_api_key": SecretStr("k"), "gemini_search_mode": "live"})
    assert not research_for(s, clean_db, cipher, demo.tenant_id).allowed  # client not live, go-live not signed


def test_api_is_for_operators_and_says_why_google_was_not_called(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    app = create_app()
    op = _login(
        TestClient(app),
        "op@pipelineos.example",
        demo.operator_password or "",
        pyotp.TOTP(demo.operator_totp_secret or "").now(),
    )
    url = f"/api/v1/clients/{demo.tenant_id}/web-research"
    r = op.post(url, json={"question": QUESTION})
    assert r.status_code == 200 and r.json()["called"] is False and r.json()["reason"]
    assert op.post(url, json={"question": "hi"}).status_code == 422
    setup = op.get(f"/api/v1/clients/{demo.tenant_id}/company-searches").json()["setup"]
    assert setup["research_monthly_cap"] == settings.gemini_search_monthly_cap and "research_key_set" in setup
    client = _login(TestClient(app), demo.client_email, demo.client_password or "")
    assert client.post(url, json={"question": QUESTION}).status_code == 403
