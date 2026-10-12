"""Compliance engine: every mode, unknown country, stricter-rule selection, subscriber types, tenant gates."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.compliance.engine import (
    ComplianceContext,
    CountryRule,
    Mode,
    ProspectFacts,
    Reason,
    SuppressionResult,
    TenantGates,
    evaluate,
)

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
GB = CountryRule(
    "r-gb",
    "GB",
    1,
    Mode.PERMITTED,
    frozenset({"email", "alternative"}),
    {"corporate": Mode.PERMITTED, "individual": Mode.CONSENT, "unknown": Mode.RESTRICTED},
)
IE = CountryRule("r-ie", "IE", 1, Mode.RESTRICTED, frozenset({"email", "alternative"}))
DE = CountryRule("r-de", "DE", 1, Mode.CONSENT, frozenset({"alternative"}))
FR_OPEN = CountryRule("r-fr", "FR", 1, Mode.PERMITTED, frozenset({"email", "alternative"}))
RULES = {"GB": GB, "IE": IE, "DE": DE, "FR": FR_OPEN}
TENANT_OK = TenantGates(True, True, False, "active", "active")
CORP_GB = ProspectFacts("GB", "GB", "corporate", "valid", 0.9)


def ctx(
    prospect: ProspectFacts = CORP_GB,
    tenant: TenantGates = TENANT_OK,
    enabled: set[str] | None = None,
    suppression: SuppressionResult | None = None,
    rules: dict[str, CountryRule] | None = None,
    channel: str = "email",
) -> ComplianceContext:
    return ComplianceContext(
        stage="queue",
        channel=channel,
        tenant=tenant,
        prospect=prospect,
        rules=RULES if rules is None else rules,
        enabled_countries=frozenset({"GB"} if enabled is None else enabled),
        suppression=suppression or SuppressionResult(False, False, NOW),
    )


def test_uk_corporate_is_allowed() -> None:
    d = evaluate(ctx())
    assert d.outcome == "allow"
    assert d.reason_codes == (Reason.OK,)
    assert d.rules_applied == {"GB": "r-gb@v1"}
    assert "email" in d.allowed_channels


def test_uk_sole_trader_is_routed_to_alternative_channel_never_email() -> None:
    d = evaluate(ctx(replace(CORP_GB, subscriber_type="individual")))
    assert d.outcome == "alternative_channel"
    assert Reason.INDIVIDUAL_SUBSCRIBER in d.reason_codes
    assert "email" not in d.allowed_channels


def test_unknown_subscriber_type_blocks() -> None:
    d = evaluate(ctx(replace(CORP_GB, subscriber_type="unknown")))
    assert d.outcome == "block"
    assert d.reason_codes == (Reason.SUBSCRIBER_TYPE_UNKNOWN,)


def test_german_prospect_at_uk_company_takes_stricter_rule() -> None:
    d = evaluate(ctx(replace(CORP_GB, location_country="DE")))
    assert d.outcome == "alternative_channel"
    assert d.effective_country == "DE"
    assert Reason.CONSENT_REQUIRED in d.reason_codes
    assert d.allowed_channels == ("alternative",)


def test_uk_prospect_at_german_company_also_takes_stricter_rule() -> None:
    d = evaluate(ctx(replace(CORP_GB, company_country="DE")))
    assert d.outcome == "alternative_channel"
    assert d.effective_country == "DE"


def test_restricted_review_beats_consent_required() -> None:
    d = evaluate(ctx(replace(CORP_GB, location_country="IE", company_country="DE"), enabled={"GB", "IE"}))
    assert d.outcome == "block"
    assert Reason.COUNTRY_RESTRICTED_REVIEW in d.reason_codes


@pytest.mark.parametrize("field", ["location_country", "company_country"])
def test_unknown_country_blocks(field: str) -> None:
    d = evaluate(ctx(replace(CORP_GB, **{field: None})))
    assert d.outcome == "block"
    assert Reason.COUNTRY_UNKNOWN in d.reason_codes


def test_country_without_rule_blocks() -> None:
    d = evaluate(ctx(replace(CORP_GB, location_country="PL"), enabled={"GB", "PL"}))
    assert d.outcome == "block"
    assert Reason.RULE_MISSING in d.reason_codes


def test_permitted_country_that_is_not_enabled_blocks() -> None:
    d = evaluate(ctx(replace(CORP_GB, location_country="FR")))  # FR rule permitted, but not enabled
    assert d.outcome == "block"
    assert Reason.COUNTRY_NOT_ENABLED in d.reason_codes


def test_gb_not_enabled_blocks_even_uk_corporates() -> None:
    d = evaluate(ctx(enabled=set()))
    assert d.outcome == "block"
    assert d.reason_codes == (Reason.COUNTRY_NOT_ENABLED,)


def test_consent_country_routes_to_alternative_even_when_not_enabled() -> None:
    d = evaluate(ctx(replace(CORP_GB, location_country="DE", company_country="DE"), enabled={"GB"}))
    assert d.outcome == "alternative_channel"


def test_ireland_restricted_and_not_enabled_blocks() -> None:
    d = evaluate(ctx(replace(CORP_GB, location_country="IE")))
    assert d.outcome == "block"
    assert {Reason.COUNTRY_NOT_ENABLED, Reason.COUNTRY_RESTRICTED_REVIEW} <= set(d.reason_codes)


@pytest.mark.parametrize(
    ("hit", "reason"), [((True, False), Reason.SUPPRESSED_GLOBAL), ((False, True), Reason.SUPPRESSED_TENANT)]
)
def test_suppression_blocks_before_everything(hit: tuple[bool, bool], reason: Reason) -> None:
    d = evaluate(ctx(replace(CORP_GB, location_country="DE"), suppression=SuppressionResult(*hit, NOW)))
    assert d.outcome == "block"
    assert d.reason_codes == (reason,)
    assert d.suppressed


def test_missing_lia_blocks() -> None:
    d = evaluate(ctx(tenant=replace(TENANT_OK, lia_approved=False)))
    assert d.outcome == "block" and Reason.TENANT_NO_LIA in d.reason_codes


def test_unsigned_dpa_blocks_client_tenant() -> None:
    d = evaluate(ctx(tenant=replace(TENANT_OK, dpa_signed=False)))
    assert d.outcome == "block" and Reason.TENANT_NO_DPA in d.reason_codes


def test_operator_own_tenant_needs_no_dpa_but_still_needs_lia() -> None:
    own = TenantGates(True, False, True, "active", "active")
    assert evaluate(ctx(tenant=own)).outcome == "allow"
    d = evaluate(ctx(tenant=replace(own, lia_approved=False)))
    assert d.outcome == "block" and d.reason_codes == (Reason.TENANT_NO_LIA,)


def test_missing_lia_also_blocks_alternative_channel() -> None:
    d = evaluate(ctx(replace(CORP_GB, location_country="DE"), tenant=replace(TENANT_OK, lia_approved=False)))
    assert d.outcome == "block"


def test_paused_tenant_and_campaign_block_with_all_reasons() -> None:
    d = evaluate(ctx(tenant=replace(TENANT_OK, tenant_status="paused", campaign_status="auto_paused")))
    assert d.outcome == "block"
    assert {Reason.TENANT_PAUSED, Reason.CAMPAIGN_PAUSED} <= set(d.reason_codes)


@pytest.mark.parametrize("verification", ["risky", "invalid", "unknown"])
def test_unverified_email_blocks(verification: str) -> None:
    d = evaluate(ctx(replace(CORP_GB, email_verification=verification)))
    assert d.outcome == "block" and Reason.EMAIL_NOT_VERIFIED in d.reason_codes


@pytest.mark.parametrize("relevance", [None, 0.0, 0.59])
def test_irrelevant_role_blocks(relevance: float | None) -> None:
    d = evaluate(ctx(replace(CORP_GB, role_relevance=relevance)))
    assert d.outcome == "block" and Reason.ROLE_NOT_RELEVANT in d.reason_codes


def test_channel_not_allowed_blocks() -> None:
    d = evaluate(ctx(channel="sms"))
    assert d.outcome == "block" and Reason.CHANNEL_NOT_ALLOWED in d.reason_codes


def test_rule_without_subscriber_rules_defaults_safely() -> None:
    bare = {"GB": CountryRule("r", "GB", 2, Mode.PERMITTED, frozenset({"email"}))}
    assert evaluate(ctx(replace(CORP_GB, subscriber_type="individual"), rules=bare)).outcome == "alternative_channel"
    assert evaluate(ctx(replace(CORP_GB, subscriber_type="unknown"), rules=bare)).outcome == "block"
    assert evaluate(ctx(rules=bare)).outcome == "allow"


def test_decision_carries_engine_version_and_suppression_time() -> None:
    d = evaluate(ctx())
    assert d.engine_version and d.suppression_checked_at == NOW


def test_corporate_subscriber_rule_is_applied_when_stricter_than_country_mode() -> None:
    strict_corp = {
        "GB": CountryRule(
            "r", "GB", 3, Mode.PERMITTED, frozenset({"email", "alternative"}), {"corporate": Mode.CONSENT}
        )
    }
    d = evaluate(ctx(rules=strict_corp))
    assert d.outcome == "alternative_channel" and Reason.CONSENT_REQUIRED in d.reason_codes
    restricted_corp = {
        "GB": CountryRule("r", "GB", 4, Mode.PERMITTED, frozenset({"email"}), {"corporate": Mode.RESTRICTED})
    }
    assert evaluate(ctx(rules=restricted_corp)).outcome == "block"
