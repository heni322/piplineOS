"""PipelineOS works only in Europe: leads, companies, clients, targeting and country configs outside Europe are
refused with a clear reason (COUNTRY_OUTSIDE_EUROPE), never treated as merely unknown."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.compliance.engine import CountryRule, Mode, Reason, evaluate
from app.domain.compliance.jurisdiction import (
    EEA,
    EUROPE,
    KNOWN_CODES,
    OUTSIDE_EUROPE_CODES,
    is_in_europe,
    normalise_country_code,
    outside_europe,
    parse_location,
)
from app.domain.tenants.config import Icp, WatchedCompany
from tests.unit.test_compliance_engine import CORP_GB, RULES, ctx

# A permissive rule for a non-European country, enabled: Europe-only must still win.
US_OPEN = CountryRule("r-us", "US", 1, Mode.PERMITTED, frozenset({"email", "alternative"}))


# ---------------------------------------------------------------- the lists
def test_europe_is_the_eea_plus_uk_and_switzerland() -> None:
    assert EEA | {"GB", "CH"} == EUROPE
    assert not (OUTSIDE_EUROPE_CODES & EUROPE)
    assert (KNOWN_CODES - OUTSIDE_EUROPE_CODES) <= EUROPE


@pytest.mark.parametrize("code", ["GB", "gb", "FR", "NO", "CH", "IS", "PL", " ie "])
def test_european_countries_are_in_europe(code: str) -> None:
    assert is_in_europe(code)


@pytest.mark.parametrize("code", ["US", "CA", "AU", "TN", "GE", "XX", "", None])
def test_other_and_unknown_codes_are_not_in_europe(code: str | None) -> None:
    assert not is_in_europe(code)


def test_outside_europe_lists_known_non_european_codes_and_ignores_unknown() -> None:
    assert outside_europe(["GB", "us", None, "TN"]) == ["TN", "US"]
    assert outside_europe(["FR", None]) == []


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize(
    ("text", "country"),
    [("Toronto, Canada", "CA"), ("Austin, USA", "US"), ("United States", "US"), ("Tunis", "TN"), ("AU", "AU")],
)
def test_non_european_locations_are_recognised_so_they_can_be_refused(text: str, country: str) -> None:
    loc = parse_location(text)
    assert (loc.country, loc.status) == (country, "ok")
    assert not is_in_europe(loc.country)


def test_codes_still_normalise() -> None:
    assert normalise_country_code("us") == ("US", None)
    assert normalise_country_code("uk") == ("GB", None)


# ---------------------------------------------------------------- the compliance engine
def test_prospect_outside_europe_is_blocked_even_with_an_open_enabled_rule() -> None:
    d = evaluate(
        ctx(
            replace(CORP_GB, location_country="US", company_country="US"),
            enabled={"GB", "US"},
            rules=RULES | {"US": US_OPEN},
        )
    )
    assert d.outcome == "block"
    assert d.reason_codes == (Reason.COUNTRY_OUTSIDE_EUROPE,)
    assert d.allowed_channels == ()


def test_company_outside_europe_blocks_a_european_prospect() -> None:
    d = evaluate(ctx(replace(CORP_GB, company_country="CA")))
    assert d.outcome == "block" and Reason.COUNTRY_OUTSIDE_EUROPE in d.reason_codes


def test_outside_europe_is_never_routed_to_an_alternative_channel() -> None:
    d = evaluate(ctx(replace(CORP_GB, location_country="TN", subscriber_type="individual"), channel="alternative"))
    assert d.outcome == "block" and Reason.COUNTRY_OUTSIDE_EUROPE in d.reason_codes


def test_european_corporate_is_still_allowed() -> None:
    assert evaluate(ctx()).outcome == "allow"


def test_unknown_country_keeps_its_own_reason() -> None:
    d = evaluate(ctx(replace(CORP_GB, location_country=None)))
    assert d.reason_codes == (Reason.COUNTRY_UNKNOWN,)


# ---------------------------------------------------------------- client configuration
def test_targeting_countries_must_be_european() -> None:
    assert Icp(description="x", industry_keywords=["it"], countries=["GB", "fr"]).countries == ["GB", "fr"]
    with pytest.raises(ValidationError, match="only in Europe"):
        Icp(description="x", industry_keywords=["it"], countries=["GB", "US"])


def test_watched_company_outside_europe_is_refused_free_text_inside_is_kept() -> None:
    assert WatchedCompany(name="Acme", country="United Kingdom").country == "United Kingdom"
    assert WatchedCompany(name="Acme", country="somewhere").country == "somewhere"  # unknown: not refused here
    with pytest.raises(ValidationError, match="outside Europe"):
        WatchedCompany(name="Acme", country="Toronto, Canada")


def test_client_established_outside_europe_is_refused_before_any_write() -> None:
    import uuid

    from app.domain.tenants import onboarding

    with pytest.raises(onboarding.OnboardingError) as exc:
        onboarding.create_client(
            None,  # type: ignore[arg-type]  # refused before the database is touched
            operator_id=uuid.uuid4(),
            slug="acme",
            legal_name="Acme",
            company_number=None,
            registered_address=None,
            country_code="US",
            billing_currency="GBP",
            vat_number=None,
        )
    assert exc.value.code == "CLIENT_OUTSIDE_EUROPE" and exc.value.missing == ["US"]


def test_client_api_model_refuses_a_non_european_country() -> None:
    from app.api.v1.clients import ClientIn

    assert ClientIn(slug="acme", legal_name="Acme", country_code="FR").country_code == "FR"
    with pytest.raises(ValidationError, match="only in Europe"):
        ClientIn(slug="acme", legal_name="Acme", country_code="TN")


# ---------------------------------------------------------------- country configuration
def test_a_country_file_outside_europe_is_refused_on_sync(tmp_path: Path) -> None:
    from app.config_sync import SyncReport, sync_countries

    src = Path(__file__).resolve().parents[3] / "config" / "countries" / "FR.yaml"
    (tmp_path / "countries").mkdir()
    (tmp_path / "countries" / "US.yaml").write_text(
        src.read_text(encoding="utf-8").replace('country_code: "FR"', 'country_code: "US"'), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="outside Europe"):
        sync_countries(None, tmp_path, SyncReport())  # type: ignore[arg-type]  # refused before the database


def test_enabling_a_country_outside_europe_is_refused() -> None:
    import uuid

    from app.domain.compliance.repository import enable_country

    with pytest.raises(ValueError, match="outside Europe"):
        enable_country(None, "US", uuid.uuid4())  # type: ignore[arg-type]  # refused before the database
