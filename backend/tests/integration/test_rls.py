"""Tenant isolation is enforced by PostgreSQL RLS for the app role (no BYPASSRLS). Cross-tenant access fails."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db.session import DbContext, transaction
from tests.factories import make_lead, make_tenant

pytestmark = pytest.mark.db


@pytest.fixture
def two_tenants(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    a, b = make_tenant(clean_db), make_tenant(clean_db)
    return make_lead(clean_db, settings, cipher, a), make_lead(clean_db, settings, cipher, b)


TABLES = [
    "leads",
    "prospects",
    "companies",
    "campaigns",
    "inboxes",
    "sending_domains",
    "compliance_decisions",
    "audit_log",
    "tenant_keys",
]


def test_tenant_sees_only_its_own_rows(clean_db, two_tenants) -> None:  # type: ignore[no-untyped-def]
    la, lb = two_tenants
    with transaction(DbContext.system(la.tenant_id), clean_db) as conn:
        ids = set(conn.execute(text("SELECT id FROM leads")).scalars())
        assert ids == {la.lead_id}
        assert conn.execute(text("SELECT count(*) FROM prospects WHERE id = :p"), {"p": lb.prospect_id}).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM tenants")).scalar() == 1


def test_cross_tenant_update_and_delete_affect_nothing(clean_db, two_tenants) -> None:  # type: ignore[no-untyped-def]
    la, lb = two_tenants
    with transaction(DbContext.system(la.tenant_id), clean_db) as conn:
        assert (
            conn.execute(text("UPDATE campaigns SET name = 'hijacked' WHERE id = :c"), {"c": lb.campaign_id}).rowcount
            == 0
        )
        assert conn.execute(text("DELETE FROM inboxes WHERE id = :i"), {"i": lb.inbox_id}).rowcount == 0
    with transaction(DbContext.system(lb.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT name FROM campaigns WHERE id = :c"), {"c": lb.campaign_id}).scalar() == "c"


def test_cross_tenant_insert_is_rejected(clean_db, two_tenants) -> None:  # type: ignore[no-untyped-def]
    la, lb = two_tenants
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        transaction(DbContext.system(la.tenant_id), clean_db) as conn,
    ):
        conn.execute(text("INSERT INTO campaigns (tenant_id, name) VALUES (:t, 'x')"), {"t": lb.tenant_id})


def test_moving_a_row_to_another_tenant_is_rejected(clean_db, two_tenants) -> None:  # type: ignore[no-untyped-def]
    la, lb = two_tenants
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        transaction(DbContext.system(la.tenant_id), clean_db) as conn,
    ):
        conn.execute(
            text("UPDATE campaigns SET tenant_id = :b WHERE id = :c"), {"b": lb.tenant_id, "c": la.campaign_id}
        )


@pytest.mark.parametrize(
    "table",
    ["leads", "prospects", "companies", "campaigns", "messages", "compliance_decisions", "audit_log", "tenant_configs"],
)
def test_missing_context_fails_closed(clean_db, two_tenants, table: str) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext(), clean_db) as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_operator_context_sees_all_tenants(clean_db, two_tenants) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM leads")).scalar() == 2


def test_context_does_not_leak_across_pooled_connections(clean_db, two_tenants) -> None:  # type: ignore[no-untyped-def]
    la, _ = two_tenants
    for _ in range(5):
        with transaction(DbContext.system(la.tenant_id), clean_db) as conn:
            assert conn.execute(text("SELECT count(*) FROM leads")).scalar() == 1
        with transaction(DbContext(), clean_db) as conn:
            assert conn.execute(text("SELECT count(*) FROM leads")).scalar() == 0
            assert conn.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar() in ("", None)


def test_app_role_cannot_bypass_rls(clean_db) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext(), clean_db) as conn:
        row = conn.execute(text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user")).one()
        assert row == (False, False)
        with pytest.raises(DBAPIError):
            conn.execute(text("SET row_security = off; SELECT count(*) FROM leads"))


def test_tenant_user_cannot_read_other_tenant_users(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    from tests.factories import make_tenant_user

    a, b = make_tenant(clean_db), make_tenant(clean_db)
    ua, _ = make_tenant_user(clean_db, settings, cipher, a)
    _ub, _ = make_tenant_user(clean_db, settings, cipher, b)
    with transaction(DbContext(tenant_id=a, role="tenant_admin", user_id=ua), clean_db) as conn:
        assert set(conn.execute(text("SELECT id FROM users")).scalars()) == {ua}


def test_suppression_list_is_the_only_cross_tenant_data(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    from app.domain.suppression.service import RecipientHashes, suppress_email

    a, b = make_tenant(clean_db), make_tenant(clean_db)
    h_global = RecipientHashes.for_email(settings.key("suppression_hash_key"), "g@x.example")
    h_tenant = RecipientHashes.for_email(settings.key("suppression_hash_key"), "t@x.example")
    with transaction(DbContext.system(a), clean_db) as conn:
        suppress_email(conn, h_global, "objection")
        suppress_email(conn, h_tenant, "manual", scope="tenant", tenant_id=a)
    with transaction(DbContext.system(b), clean_db) as conn:
        rows = conn.execute(text("SELECT scope FROM suppression_list")).scalars().all()
        assert rows == ["global"]  # tenant A's own list is invisible to B; the global entry is shared
        cols = (
            conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'suppression_list'")
            )
            .scalars()
            .all()
        )
        assert "email" not in cols  # hash only, never plaintext


def test_unknown_tenant_context_sees_nothing(clean_db, two_tenants) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(uuid.uuid4()), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM leads")).scalar() == 0


def test_scheduler_lists_tenants_only_outside_tenant_context(clean_db, two_tenants) -> None:  # type: ignore[no-untyped-def]
    la, _ = two_tenants
    with transaction(DbContext.system(), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM tenants")).scalar() == 2
    with transaction(DbContext(tenant_id=la.tenant_id, role="tenant_admin"), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM tenants")).scalar() == 1
