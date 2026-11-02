"""PipelineOS runs from France: the go-live checklist that gates real sending moves from the UK scope ('uk') to the
French one ('fr'). The French items are created by `config sync` (config/golive_checklist.yaml). The old UK items stay
in the table (their sign-offs are history) but no longer gate anything.

The database send gate (enforce_send_gate) is rewritten in place: only its golive_complete('uk') call changes.

Revision ID: 0029
Revises: 0028
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _swap(old: str, new: str) -> str:
    return f"""
DO $$
DECLARE def text;
BEGIN
  SELECT pg_get_functiondef('enforce_send_gate()'::regprocedure) INTO def;
  IF position($q$golive_complete('{old}')$q$ IN def) = 0 THEN
    RAISE EXCEPTION 'enforce_send_gate does not check the {old} go-live scope: refusing to rewrite it';
  END IF;
  EXECUTE replace(def, $q$golive_complete('{old}')$q$, $q$golive_complete('{new}')$q$);
END $$;
"""


def upgrade() -> None:
    op.execute(_swap("uk", "fr"))


def downgrade() -> None:
    op.execute(_swap("fr", "uk"))
