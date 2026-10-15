"""Customer proof: a client's own words about the results, with explicit consent for how they may be used.

Written by the client's admin in the portal, never by us. Consent has a scope (anonymous quote, quote with name,
quote with name and logo) and can be withdrawn at any time; a withdrawn testimonial is never shown again. Results
quoted next to it must come from the client's measured report, not from us.

Revision ID: 0021
Revises: 0020
"""

from alembic import op

from app.db.ddl import APP_ROLE, grant_crud, tenant_rls

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE client_testimonials (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  quote text NOT NULL CHECK (char_length(quote) BETWEEN 20 AND 1000),
  attribution text CHECK (char_length(attribution) <= 200),
  scope text NOT NULL CHECK (scope IN ('anonymous', 'named', 'named_with_logo')),
  consented_by uuid NOT NULL REFERENCES users(id),
  consented_at timestamptz NOT NULL DEFAULT now(),
  withdrawn_at timestamptz,
  CONSTRAINT named_has_attribution CHECK (scope = 'anonymous' OR length(trim(coalesce(attribution, ''))) >= 2)
);
CREATE INDEX client_testimonials_tenant ON client_testimonials (tenant_id);
{tenant_rls("client_testimonials")}
{grant_crud("client_testimonials")}
REVOKE DELETE ON client_testimonials FROM {APP_ROLE};
""")


def downgrade() -> None:
    op.execute("DROP TABLE client_testimonials;")
