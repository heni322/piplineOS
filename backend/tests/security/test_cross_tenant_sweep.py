"""Cross-tenant sweep: every API route that takes an id in its path is called by ANOTHER client's admin with the
real ids of the demo client's leads, messages, meetings, invoices and so on. None may succeed, and nothing of the
demo client may appear in any response. This is the automated part of a tenant-isolation pen test: it covers routes
added later without anyone remembering to write an isolation test for them."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.conversations import run_conversations
from app.demo.seed import providers, seed_demo
from app.workflows.pipeline import agent_context, run_campaign
from tests.factories import make_tenant, make_tenant_user
from tests.security.test_api import login

pytestmark = pytest.mark.db

# path parameter -> the tenant-scoped table its ids come from
TABLES = {
    "lead_id": "leads",
    "message_id": "messages",
    "meeting_id": "meetings",
    "invoice_id": "invoices",
    "campaign_id": "campaigns",
    "dispute_id": "disputes",
    "testimonial_id": "client_testimonials",
    "task_id": "alternative_channel_tasks",
    "escalation_id": "escalations",
    "inbox_id": "inboxes",
    "domain_id": "sending_domains",
    "dpr_id": "data_protection_requests",
    "request_id": "data_protection_requests",
    "reply_id": "replies",
    "tenant_id": "tenants",
    "approval_id": "messages",
    "conversation_id": "leads",
}
# Non-id parameters get REAL values, so the request reaches the authorisation check instead of failing validation.
VALUES = {
    "kind": ["pricing", "qualification_criteria"],
    "code": ["GB"],
    "item_id": [str(uuid.UUID(int=1))],
    "token": ["not-a-real-token"],
}
SAMPLE = 3  # ids per table per route (the route's check is the same for every id)


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, providers())
    run_conversations(ctx, providers(), demo.operator_id)
    other = make_tenant(clean_db)
    make_tenant_user(clean_db, settings, cipher, other, email="intruder@other.example")
    ids: dict[str, list[str]] = {}
    with transaction(DbContext(role="operator"), clean_db) as conn:
        for table in set(TABLES.values()):
            col = "id"
            where = "id = :t" if table == "tenants" else "tenant_id = :t"
            ids[table] = [
                str(x)
                for x in conn.execute(
                    text(f"SELECT {col} FROM {table} WHERE {where} ORDER BY id LIMIT {SAMPLE}"),
                    {"t": demo.tenant_id},
                ).scalars()
            ]
        name = conn.execute(text("SELECT legal_name FROM tenants WHERE id = :t"), {"t": demo.tenant_id}).scalar_one()
    app = create_app()
    intruder = login(TestClient(app), "intruder@other.example", "tenant-password-123")
    return app, intruder, demo, ids, name


def _routes(app):  # type: ignore[no-untyped-def]
    """Every operation with a path parameter, from the OpenAPI schema (includes routes from nested routers)."""
    for path, ops in app.openapi()["paths"].items():
        if "{" in path:
            yield path, {m.upper() for m in ops if m in ("get", "post", "put", "patch", "delete")}


def test_no_route_lets_one_client_reach_anothers_data(world) -> None:  # type: ignore[no-untyped-def]
    app, intruder, demo, ids, name = world
    secrets = {str(demo.tenant_id), name, *[i for v in ids.values() for i in v]}
    tried, problems = 0, []
    for route_path, methods in _routes(app):
        params = re.findall(r"{(\w+)}", route_path)
        pools = [VALUES.get(p) or ids.get(TABLES.get(p, ""), []) or [str(uuid.uuid4())] for p in params]
        unknown = [p for p in params if p not in TABLES and p not in VALUES]
        assert not unknown, f"{route_path}: map {unknown} in TABLES or VALUES so the sweep tests it properly"
        for k in range(max(len(pool) for pool in pools)):
            path = route_path
            for p, pool in zip(params, pools, strict=True):
                path = path.replace("{" + p + "}", pool[min(k, len(pool) - 1)])
            for method in methods:
                tried += 1
                r = intruder.request(method, path, json={} if method in ("POST", "PUT", "PATCH") else None)
                body = r.text
                leaked = [s for s in secrets if s and s in body]
                if r.status_code < 300 and (method != "GET" or leaked):
                    problems.append(f"{method} {route_path} -> {r.status_code} leaked={leaked[:2]}")
                elif r.status_code >= 500:
                    problems.append(f"{method} {route_path} -> {r.status_code} (server error)")
    assert tried > 50, tried
    assert problems == [], "\n".join(problems)


def test_no_list_or_report_shows_another_clients_data(world) -> None:  # type: ignore[no-untyped-def]
    app, intruder, demo, ids, name = world
    secrets = {str(demo.tenant_id), name, *[i for v in ids.values() for i in v]}
    problems, tried = [], 0
    for path, ops in app.openapi()["paths"].items():
        if "{" in path or "get" not in ops:
            continue
        tried += 1
        r = intruder.get(path, params={"tenant_id": str(demo.tenant_id)})  # even when asking for it by id
        leaked = [s for s in secrets if s and s in r.text]
        if r.status_code >= 500 or (r.status_code < 300 and leaked):
            problems.append(f"GET {path} -> {r.status_code} leaked={leaked[:2]}")
    assert tried > 20, tried
    assert problems == [], "\n".join(problems)
