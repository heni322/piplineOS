"""The Go-live page's per-country build status: what exists, what is still needed and who acts. Read-only."""

from __future__ import annotations

from dataclasses import asdict

import pytest
from sqlalchemy import text

from app.api.v1.governance import _COUNTRIES_SQL
from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.domain.golive.countries import country_build

pytestmark = pytest.mark.db


def _builds(engine, settings) -> dict[str, dict]:  # type: ignore[no-untyped-def,type-arg]
    with transaction(DbContext(role="operator"), engine) as conn:
        rows = conn.execute(text(_COUNTRIES_SQL)).mappings().all()
        return {r["country_code"]: asdict(country_build(conn, settings, dict(r))) for r in rows}


def _needs(build: dict) -> list[tuple[str, str]]:  # type: ignore[type-arg]
    return [(n["who"], n["what"]) for n in build["still_needed"]]


def test_each_country_says_what_is_built_and_what_is_still_needed(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    seed_demo(clean_db, settings)
    b = _builds(clean_db, settings)

    fr = b["FR"]
    assert fr["register_kind"] == "free" and not fr["register_ready"] and fr["languages_with_texts"] == ["fr-FR"]
    assert "French" in fr["safety_tested"]
    assert ("lawyer", "review the country's email rules and the footer") in _needs(fr)
    assert ("native speaker", "check the fixed texts and footer wording") in _needs(fr)
    assert ("each client", "outside the EU/EEA: record an EU representative") in _needs(fr)

    es = b["ES"]
    assert es["register_kind"] == "none" and "paid register" in " ".join(w for _, w in _needs(es))
    se = b["SE"]
    assert se["register_detail"] == "credentials not set" and se["languages_in_english"] == ["sv-SE"]
    assert "Nordic" in se["safety_tested"]
    assert b["DE"]["note"] and not any("register" in w for _, w in _needs(b["DE"]))  # consent-only: never emailed

    be = b["BE"]
    assert be["register_detail"].startswith("file not imported")
    assert not any("enable the country" in w for _, w in _needs(b["GB"]))  # GB is enabled in the demo


def test_switched_on_registers_and_imports_are_recognised(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import get_engine

    seed_demo(clean_db, settings)
    with get_engine(settings.database_owner_url).begin() as conn:
        conn.execute(
            text("""INSERT INTO register_companies (country_code, company_number, legal_form, status)
                             VALUES ('IE', '612345', 'ltd', 'active')""")
        )
    on = settings.model_copy(
        update={"fr_registry_adapter_mode": "live", "ie_registry_adapter_mode": "live", "cvr_username": "u"}
    )
    b = _builds(clean_db, on)
    assert b["FR"]["register_ready"] and b["IE"]["register_ready"]
    assert b["IE"]["register_detail"].startswith("1 companies imported")
    assert b["DK"]["register_detail"] == "credentials not set"  # a username alone is not enough
