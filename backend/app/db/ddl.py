"""SQL helpers shared by migrations. Migrations are hand-written SQL (single source of truth: no ORM
autogenerate, so no schema/ORM drift). Never edit a migration that has been applied anywhere."""

from __future__ import annotations

APP_ROLE = "pipelineos_app"


def tenant_rls(table: str, *, force: bool = True) -> str:
    """Tenant isolation: rows visible/writable only in the matching tenant context (or operator)."""
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
{"ALTER TABLE " + table + " FORCE ROW LEVEL SECURITY;" if force else ""}
CREATE POLICY tenant_isolation ON {table}
  USING (tenant_id = app_tenant_id() OR app_is_operator())
  WITH CHECK (tenant_id = app_tenant_id() OR app_is_operator());
"""


def grant_crud(table: str) -> str:
    return f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE};"


def grant_append_only(table: str) -> str:
    """App may read and insert; never update/delete. A trigger also blocks owner-level mutation."""
    return f"""
GRANT SELECT, INSERT ON {table} TO {APP_ROLE};
CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
"""


def updated_at_trigger(table: str) -> str:
    return f"""CREATE TRIGGER {table}_updated_at BEFORE UPDATE ON {table}
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();"""


TS = "created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()"
TID = "tenant_id uuid NOT NULL REFERENCES tenants(id)"
PK = "id uuid PRIMARY KEY DEFAULT gen_random_uuid()"
