"""Phase 6.1: legal, tax and supplier configuration can only be written by the operator or the system (config sync).

Before this, the runtime role could insert a new version of a compliance rule, a country's rollout status or a tax
rule from any database context (a client's included). No endpoint allowed it; this closes it in the database too.
Everyone may still read these tables. The table owner (migrations) is unaffected: RLS is enabled, not forced.

Also makes login authenticator codes single-use.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

from app.db.ddl import APP_ROLE

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

CONFIG_TABLES = (
    "compliance_rules",
    "country_rollout_status",
    "tax_rules",
    "tax_settings",
    "transfer_mechanisms",
    "sub_processors",
    "supplier_profiles",
)
TRUSTED = "app_role() IN ('operator', 'system')"


def upgrade() -> None:
    sql = []
    for t in CONFIG_TABLES:
        sql.append(f"""
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY read_all ON {t} FOR SELECT USING (true);
CREATE POLICY trusted_insert ON {t} FOR INSERT WITH CHECK ({TRUSTED});""")
    # Invoice numbers: only the operator or the system may reserve one (a client must not be able to burn numbers).
    sql.append(f"""
ALTER TABLE invoice_counters ENABLE ROW LEVEL SECURITY;
CREATE POLICY trusted_only ON invoice_counters USING ({TRUSTED}) WITH CHECK ({TRUSTED});""")
    # Login codes are single-use: a code seen over someone's shoulder can't be replayed within its 90-second window.
    sql.append(f"""
ALTER TABLE users ADD COLUMN totp_last_step bigint;
CREATE FUNCTION auth_consume_totp_step(p_user uuid, p_step bigint) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  WITH u AS (UPDATE users SET totp_last_step = p_step
             WHERE id = p_user AND (totp_last_step IS NULL OR totp_last_step < p_step) RETURNING 1)
  SELECT EXISTS (SELECT 1 FROM u)
$$;
REVOKE ALL ON FUNCTION auth_consume_totp_step(uuid, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_consume_totp_step(uuid, bigint) TO {APP_ROLE};""")
    op.execute("\n".join(sql))


def downgrade() -> None:
    sql = [
        "DROP FUNCTION IF EXISTS auth_consume_totp_step(uuid, bigint);",
        "ALTER TABLE users DROP COLUMN IF EXISTS totp_last_step;",
        "DROP POLICY IF EXISTS trusted_only ON invoice_counters;",
        "ALTER TABLE invoice_counters DISABLE ROW LEVEL SECURITY;",
    ]
    for t in CONFIG_TABLES:
        sql.append(f"""DROP POLICY IF EXISTS trusted_insert ON {t};
DROP POLICY IF EXISTS read_all ON {t};
ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;""")
    op.execute("\n".join(sql))
