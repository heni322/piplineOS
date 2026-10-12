from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config_sync import sync_all
from app.core.audit import write_audit
from app.db.session import DbContext, transaction
from tests.conftest import CONFIG
from tests.factories import enable, make_operator, make_tenant

pytestmark = pytest.mark.db


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET action = 'x'",
        "DELETE FROM audit_log",
        "UPDATE compliance_rules SET mode = 'permitted_with_opt_out'",
        "DELETE FROM compliance_rules",
        "DELETE FROM suppression_list",
        "UPDATE suppression_list SET reason = 'manual'",
        "UPDATE country_rollout_status SET enabled = true",
    ],
)
def test_append_only_tables_reject_mutation_by_app(clean_db, sql: str) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator"), clean_db) as conn:
        write_audit(conn, action="x", actor_type="system", actor_id="t", tenant_id=None)
    with (
        pytest.raises(DBAPIError, match="permission denied|append-only"),
        transaction(DbContext(role="operator"), clean_db) as conn,
    ):
        conn.execute(text(sql))


def test_even_the_owner_cannot_rewrite_the_audit_log(clean_db, owner_engine) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator"), clean_db) as conn:
        write_audit(conn, action="x", actor_type="system", actor_id="t", tenant_id=None)
    with pytest.raises(DBAPIError, match="append-only"), owner_engine.begin() as c:
        c.execute(text("SELECT set_config('app.role', 'operator', true)"))
        assert c.execute(text("SELECT count(*) FROM audit_log")).scalar() >= 1  # rows are visible...
        c.execute(text("UPDATE audit_log SET action = 'tampered'"))  # ...but cannot be changed


def test_config_sync_is_idempotent_and_versions_changes(clean_db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import shutil

    with transaction(DbContext.system(), clean_db) as conn:
        again = sync_all(conn, CONFIG)
        assert again.created == []
        assert conn.execute(text("SELECT count(*) FROM current_compliance_rules")).scalar() == 14
        assert conn.execute(text("SELECT count(*) FROM current_country_rollout WHERE enabled")).scalar() == 0
        assert conn.execute(text("SELECT bool_or(verified_by_counsel) FROM compliance_rules")).scalar() is False
        assert (
            conn.execute(text("SELECT count(*) FROM golive_checklist_items WHERE signed_off_at IS NOT NULL")).scalar()
            == 0
        )
    changed = tmp_path / "config"
    shutil.copytree(CONFIG, changed)
    f = changed / "countries" / "FR.yaml"
    f.write_text(f.read_text().replace("Stage 3:", "Stage 3 (updated):"))
    with transaction(DbContext.system(), clean_db) as conn:
        rep = sync_all(conn, changed)
        assert rep.created == ["compliance_rules:FR"]
        versions = conn.execute(
            text("SELECT version FROM compliance_rules WHERE country_code = 'FR' ORDER BY 1")
        ).scalars()
        assert list(versions) == [1, 2]


def test_enablement_survives_config_resync(clean_db, settings, cipher, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import shutil

    op = make_operator(clean_db, settings, cipher)
    enable(clean_db, op, "GB")
    changed = tmp_path / "config"
    shutil.copytree(CONFIG, changed)
    f = changed / "countries" / "GB.yaml"
    f.write_text(f.read_text().replace("languages: [en-GB]", "languages: [en-GB, cy-GB]"))
    with transaction(DbContext.system(), clean_db) as conn:
        sync_all(conn, changed)
        row = conn.execute(
            text("SELECT enabled, enabled_by, version FROM current_country_rollout WHERE country_code = 'GB'")
        ).one()
        assert row.enabled and row.enabled_by == op and row.version == 3


def test_country_cannot_be_enabled_without_attribution(clean_db) -> None:  # type: ignore[no-untyped-def]
    with (
        pytest.raises(DBAPIError, match="enabling_is_attributed"),
        transaction(DbContext(role="operator"), clean_db) as conn,
    ):
        conn.execute(
            text("""INSERT INTO country_rollout_status (country_code, version, content_hash, name, stage, enabled)
              VALUES ('GB', 99, 'x', 'UK', '1', true)""")
        )


def test_approval_mode_locked_for_first_two_weeks(clean_db) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    with (
        pytest.raises(DBAPIError, match="approval sampling not allowed"),
        transaction(DbContext(role="operator"), clean_db) as conn,
    ):
        conn.execute(
            text("UPDATE tenants SET approval_mode = 'sampling', approval_sample_pct = 20 WHERE id = :t"), {"t": t}
        )
    with (
        pytest.raises(DBAPIError, match="cannot be shortened"),
        transaction(DbContext(role="operator"), clean_db) as conn,
    ):
        conn.execute(text("UPDATE tenants SET approval_mode_locked_until = current_date WHERE id = :t"), {"t": t})
