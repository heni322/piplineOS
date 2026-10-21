"""`pipelineos clients create-own <template>`: your own outreach, created on the server already filled in from a
template (the Vayz preset), as the controller (no DPA, client login or billing), and only once."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from app.cli import app
from app.db.session import DbContext, transaction
from tests.factories import make_operator
from tests.security.test_api import SECRET

pytestmark = pytest.mark.db

ARGS = [
    "clients",
    "create-own",
    "vayz-bailleurs",
    "--operator-email",
    "op@pipelineos.example",
    "--legal-name",
    "LYLIUM",
    "--company-number",
    "103 579 322",
    "--address",
    "9 Allée Simone Gébelin, 13001 Marseille",
    "--country",
    "FR",
    "--currency",
    "EUR",
]


def test_create_own_from_the_vayz_template(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    make_operator(clean_db, settings, cipher, email="op@pipelineos.example", totp_secret=SECRET)
    runner = CliRunner()
    r = runner.invoke(app, ARGS)
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["template"] == "vayz-bailleurs" and out["config_version"] >= 1
    with transaction(DbContext(role="operator"), clean_db) as conn:
        t = conn.execute(
            text("SELECT slug, is_operator_own, role, country_code, billing_currency FROM tenants WHERE id = :t"),
            {"t": out["tenant_id"]},
        ).one()
    assert t.slug == "vayz" and t.is_operator_own and t.role == "controller"
    assert (t.country_code, t.billing_currency) == ("FR", "EUR")

    again = runner.invoke(app, ARGS)  # only one own account, ever
    assert again.exit_code != 0 and "already exists" in again.output


def test_create_own_refuses_unknown_template_and_operator(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    make_operator(clean_db, settings, cipher, email="op@pipelineos.example", totp_secret=SECRET)
    runner = CliRunner()
    bad = ARGS.copy()
    bad[2] = "no-such-template"
    r = runner.invoke(app, bad)
    assert r.exit_code != 0 and "vayz-bailleurs" in r.output  # lists what exists
    bad = ARGS.copy()
    bad[4] = "nobody@example.com"
    r = runner.invoke(app, bad)
    assert r.exit_code != 0 and "no operator" in r.output
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM tenants WHERE is_operator_own")).scalar() == 0
