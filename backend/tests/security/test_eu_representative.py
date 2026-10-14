"""Stage 2 (Norway, Finland): a client outside the EU/EEA cannot email prospects in a country whose rules require an
EU/EEA representative (GDPR Article 27) until an operator records one. Only operators can record it; it is audited
and shown on the onboarding checklist when the client targets such a country."""

from __future__ import annotations

import json

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.domain.compliance.repository import load_rules, load_tenant_gates
from app.domain.tenants import onboarding
from tests.security.test_golive_prep import _login

pytestmark = pytest.mark.db


def test_representative_rules_recording_and_checklist(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    tid = demo.tenant_id
    with transaction(DbContext.system(tid), clean_db) as conn:
        rules = load_rules(conn)
        assert rules["NO"].eu_representative_required and rules["FI"].eu_representative_required
        assert not rules["GB"].eu_representative_required
        assert load_tenant_gates(conn, tid, demo.campaign_id).eu_representative is False  # UK client, none yet

    # the client starts targeting Norway: the checklist asks for a representative
    with transaction(DbContext(role="operator", tenant_id=tid), clean_db) as conn:
        cfg = conn.execute(
            text("SELECT config FROM tenant_configs WHERE tenant_id = :t ORDER BY version DESC LIMIT 1"), {"t": tid}
        ).scalar_one()
        cfg["icp"]["countries"] = ["GB", "NO"]
        op_id = conn.execute(text("SELECT id FROM users WHERE role = 'operator' LIMIT 1")).scalar_one()
        onboarding.set_config(conn, tenant_id=tid, operator_id=op_id, config=cfg)
        item = next(i for i in onboarding.checklist(conn, tid) if i.key == "eu_representative")
        assert not item.done and "NO" in item.detail

    app = create_app()
    client = _login(TestClient(app), demo.client_email, demo.client_password or "")
    body = {"name": "Fjordrep Representation AS", "contact_email": "privacy@fjordrep.example", "country_code": "NO"}
    assert client.post(f"/api/v1/clients/{tid}/eu-representative", json=body).status_code == 403

    op = _login(
        TestClient(app),
        "op@pipelineos.example",
        demo.operator_password or "",
        pyotp.TOTP(demo.operator_totp_secret or "").now(),
    )
    outside = op.post(f"/api/v1/clients/{tid}/eu-representative", json=body | {"country_code": "CH"})
    assert outside.status_code == 422  # Switzerland is not in the EU/EEA
    assert op.post(f"/api/v1/clients/{tid}/eu-representative", json=body).status_code == 204

    with transaction(DbContext(role="operator", tenant_id=tid), clean_db) as conn:
        assert load_tenant_gates(conn, tid, demo.campaign_id).eu_representative is True
        assert next(i for i in onboarding.checklist(conn, tid) if i.key == "eu_representative").done
        audit = conn.execute(
            text("SELECT after FROM audit_log WHERE action = 'tenant.eu_representative_recorded'")
        ).scalar_one()
        assert (audit if isinstance(audit, dict) else json.loads(audit))["country_code"] == "NO"


def test_a_client_established_in_the_eea_needs_no_representative(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    with transaction(DbContext(role="operator", tenant_id=demo.tenant_id), clean_db) as conn:
        conn.execute(text("UPDATE tenants SET country_code = 'IE' WHERE id = :t"), {"t": demo.tenant_id})
        assert load_tenant_gates(conn, demo.tenant_id, demo.campaign_id).eu_representative is True
        assert onboarding.eu_representative_needed(conn, "IE", ["NO", "FI"]) == []
        assert onboarding.eu_representative_needed(conn, "GB", ["GB", "FI", "NO"]) == ["FI", "NO"]
