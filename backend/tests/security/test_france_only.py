"""PipelineOS runs from France: with SERVED_COUNTRIES=FR (the production default) the UK can no longer be chosen
anywhere (new client, targeting, Find companies, enabling a country), France and euros are the defaults, and the
French go-live checklist is the one that gates real sending, down to the database send gate."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.domain.served import NotServed, not_served, require_served
from app.settings import Settings, get_settings
from tests.security.test_golive_prep import _login

pytestmark = pytest.mark.db


def test_france_is_the_production_default() -> None:
    s = Settings.model_fields["served_countries"].default
    assert s == "FR"
    fr = get_settings().model_copy(update={"served_countries": "FR"})
    assert fr.served == ("FR",) and fr.home_country == "FR"
    assert not_served(fr, ["FR", "gb", None]) == ["GB"]
    with pytest.raises(NotServed, match="Served: FR"):
        require_served(fr, ["GB"])


@pytest.fixture
def france_only(clean_db, settings, monkeypatch):  # type: ignore[no-untyped-def]
    """The API as production runs it: SERVED_COUNTRIES=FR."""
    import app.api.v1.auth as auth_mod
    from app.api.deps import settings_dep
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    fr = settings.model_copy(update={"served_countries": "FR"})
    monkeypatch.setattr("app.api.v1.governance.get_settings", lambda: fr)
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    app = create_app()
    app.dependency_overrides[settings_dep] = lambda: fr
    op = _login(TestClient(app), "op@pipelineos.example", demo.operator_password or "",
                pyotp.TOTP(demo.operator_totp_secret or "").now())  # fmt: skip
    return op, demo


def test_the_uk_can_no_longer_be_chosen(france_only) -> None:  # type: ignore[no-untyped-def]
    op, demo = france_only
    uk_client = op.post("/api/v1/clients", json={"slug": "acme-uk", "legal_name": "Acme Ltd", "country_code": "GB"})
    assert uk_client.status_code == 422 and "Served: FR" in uk_client.json()["detail"]
    fr_client = op.post("/api/v1/clients", json={"slug": "acme-fr", "legal_name": "Acme SAS"})
    assert fr_client.status_code == 201  # France and euros by default
    url = f"/api/v1/clients/{demo.tenant_id}"
    assert op.post(f"{url}/company-searches", json={"query": "IT support Leeds", "country": "GB"}).status_code == 422
    setup = op.get(f"{url}/company-searches").json()["setup"]
    assert setup["countries"] == ["FR"]
    targeting = op.get(f"{url}/targeting").json()
    assert "GB" not in targeting["available_countries"]
    saved = op.put(f"{url}/targeting", json=targeting | {"countries": ["GB"]})
    assert saved.status_code == 422 and "Served: FR" in saved.json()["detail"]
    enable = op.post("/api/v1/countries/GB/enable", json={"totp_code": "000000"})
    assert enable.status_code == 422


def test_the_french_checklist_gates_real_sending(clean_db) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator"), clean_db) as conn:
        scopes = set(conn.execute(text("SELECT DISTINCT scope FROM golive_checklist_items")).scalars())
        gate = conn.execute(text("SELECT pg_get_functiondef('enforce_send_gate()'::regprocedure)")).scalar_one()
    assert scopes == {"fr"}
    assert "golive_complete('fr')" in gate and "golive_complete('uk')" not in gate
