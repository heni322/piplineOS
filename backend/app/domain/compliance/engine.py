"""The compliance engine — a PURE function (no I/O). See docs/05-compliance-engine.md.

Not legal advice: rules come from versioned configuration with verified_by_counsel=false.
Anything unknown, missing or ambiguous resolves to the SAFEST outcome (block).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

ENGINE_VERSION = "1.0.0"


class Mode(StrEnum):
    PERMITTED = "permitted_with_opt_out"
    CONSENT = "consent_required"
    RESTRICTED = "restricted_review"


STRICTNESS: dict[Mode, int] = {Mode.PERMITTED: 0, Mode.CONSENT: 1, Mode.RESTRICTED: 2}

Outcome = Literal["allow", "block", "alternative_channel"]
Stage = Literal["queue", "pre_approval", "send", "referral", "recheck"]

DEFAULT_SUBSCRIBER_RULES: dict[str, Mode] = {
    "individual": Mode.CONSENT,
    "unknown": Mode.RESTRICTED,
}


class Reason(StrEnum):
    OK = "OK"
    SUPPRESSED_GLOBAL = "SUPPRESSED_GLOBAL"
    SUPPRESSED_TENANT = "SUPPRESSED_TENANT"
    TENANT_NO_LIA = "TENANT_NO_LIA"
    TENANT_NO_DPA = "TENANT_NO_DPA"
    TENANT_PAUSED = "TENANT_PAUSED"
    CAMPAIGN_PAUSED = "CAMPAIGN_PAUSED"
    COUNTRY_UNKNOWN = "COUNTRY_UNKNOWN"
    COUNTRY_NOT_ENABLED = "COUNTRY_NOT_ENABLED"
    RULE_MISSING = "RULE_MISSING"
    COUNTRY_RESTRICTED_REVIEW = "COUNTRY_RESTRICTED_REVIEW"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    INDIVIDUAL_SUBSCRIBER = "INDIVIDUAL_SUBSCRIBER"
    SUBSCRIBER_TYPE_UNKNOWN = "SUBSCRIBER_TYPE_UNKNOWN"
    CHANNEL_NOT_ALLOWED = "CHANNEL_NOT_ALLOWED"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    ROLE_NOT_RELEVANT = "ROLE_NOT_RELEVANT"


@dataclass(frozen=True)
class CountryRule:
    rule_id: str
    country_code: str
    version: int
    mode: Mode
    allowed_channels: frozenset[str]
    subscriber_rules: Mapping[str, Mode] = field(default_factory=dict)

    def mode_for_subscriber(self, subscriber_type: str) -> Mode:
        if subscriber_type == "corporate":
            return Mode(self.subscriber_rules.get("corporate", self.mode))
        fallback = DEFAULT_SUBSCRIBER_RULES.get(subscriber_type, Mode.RESTRICTED)
        return Mode(self.subscriber_rules.get(subscriber_type, fallback))


@dataclass(frozen=True)
class TenantGates:
    lia_approved: bool
    dpa_signed: bool
    is_controller: bool  # operator's own (dogfooding) tenant: controller, no DPA needed — LIA still required
    tenant_status: str
    campaign_status: str


@dataclass(frozen=True)
class ProspectFacts:
    location_country: str | None
    company_country: str | None
    subscriber_type: str
    email_verification: str
    role_relevance: float | None


@dataclass(frozen=True)
class SuppressionResult:
    global_hit: bool
    tenant_hit: bool
    checked_at: datetime


@dataclass(frozen=True)
class ComplianceContext:
    stage: Stage
    channel: str
    tenant: TenantGates
    prospect: ProspectFacts
    rules: Mapping[str, CountryRule]
    enabled_countries: frozenset[str]
    suppression: SuppressionResult
    role_relevance_threshold: float = 0.6


@dataclass(frozen=True)
class ComplianceDecision:
    outcome: Outcome
    reason_codes: tuple[Reason, ...]
    effective_country: str | None
    rules_applied: Mapping[str, str]
    allowed_channels: tuple[str, ...]
    suppression_checked_at: datetime
    engine_version: str = ENGINE_VERSION

    @property
    def suppressed(self) -> bool:
        return bool({Reason.SUPPRESSED_GLOBAL, Reason.SUPPRESSED_TENANT} & set(self.reason_codes))


def _decision(
    ctx: ComplianceContext,
    outcome: Outcome,
    reasons: Sequence[Reason],
    effective: str | None = None,
    applied: Mapping[str, str] | None = None,
    channels: tuple[str, ...] = (),
) -> ComplianceDecision:
    return ComplianceDecision(
        outcome, tuple(reasons), effective, dict(applied or {}), channels, ctx.suppression.checked_at
    )


def evaluate(ctx: ComplianceContext) -> ComplianceDecision:
    # ---- Tier 1: hard blocks (suppression, tenant gates, jurisdiction known/enabled/ruled)
    reasons: list[Reason] = []
    if ctx.suppression.global_hit:
        reasons.append(Reason.SUPPRESSED_GLOBAL)
    if ctx.suppression.tenant_hit:
        reasons.append(Reason.SUPPRESSED_TENANT)
    if reasons:  # suppression short-circuits everything, including alternative channels
        return _decision(ctx, "block", reasons)

    t = ctx.tenant
    if not t.lia_approved:
        reasons.append(Reason.TENANT_NO_LIA)
    if not t.is_controller and not t.dpa_signed:
        reasons.append(Reason.TENANT_NO_DPA)
    if t.tenant_status != "active":
        reasons.append(Reason.TENANT_PAUSED)
    if t.campaign_status != "active":
        reasons.append(Reason.CAMPAIGN_PAUSED)

    p = ctx.prospect
    countries = [p.location_country, p.company_country]
    if any(c is None for c in countries):
        reasons.append(Reason.COUNTRY_UNKNOWN)
    if reasons:
        return _decision(ctx, "block", reasons)

    # ---- Tier 2: mode resolution — the STRICTER rule across prospect location and company country,
    # and across the subscriber-type rule of each country.
    # A country that is not enabled, or has no rule, counts as restricted_review — except a country whose
    # rule is consent_required: email is blocked there anyway and the brief routes it to alternative channels.
    relevant = sorted({c for c in countries if c})
    applied = {c: f"{ctx.rules[c].rule_id}@v{ctx.rules[c].version}" for c in relevant if c in ctx.rules}
    candidates: list[tuple[Mode, Reason, str]] = []
    for c in relevant:
        rule = ctx.rules.get(c)
        if rule is None:
            candidates.append((Mode.RESTRICTED, Reason.RULE_MISSING, c))
            continue
        if c not in ctx.enabled_countries and rule.mode != Mode.CONSENT:
            candidates.append((Mode.RESTRICTED, Reason.COUNTRY_NOT_ENABLED, c))
        country_reason = {
            Mode.PERMITTED: Reason.OK,
            Mode.CONSENT: Reason.CONSENT_REQUIRED,
            Mode.RESTRICTED: Reason.COUNTRY_RESTRICTED_REVIEW,
        }[rule.mode]
        candidates.append((rule.mode, country_reason, c))
        # The subscriber-type rule is ALWAYS applied, including for corporates (a country may restrict them too).
        sub_mode = rule.mode_for_subscriber(p.subscriber_type)
        if p.subscriber_type == "individual":
            sub_reason = Reason.INDIVIDUAL_SUBSCRIBER
        elif p.subscriber_type == "corporate":
            sub_reason = {
                Mode.PERMITTED: Reason.OK,
                Mode.CONSENT: Reason.CONSENT_REQUIRED,
                Mode.RESTRICTED: Reason.COUNTRY_RESTRICTED_REVIEW,
            }[sub_mode]
        else:
            sub_reason = Reason.SUBSCRIBER_TYPE_UNKNOWN
        candidates.append((sub_mode, sub_reason, c))
    worst = max(STRICTNESS[m] for m, _, _ in candidates)
    at_worst = [(m, r, c) for m, r, c in candidates if STRICTNESS[m] == worst]
    effective = at_worst[0][2]
    mode_reasons = list(dict.fromkeys(r for _, r, _ in at_worst if r != Reason.OK))

    if worst == STRICTNESS[Mode.RESTRICTED]:
        return _decision(ctx, "block", mode_reasons, effective, applied)
    if worst == STRICTNESS[Mode.CONSENT]:
        alt = tuple(sorted(set.intersection(*(set(ctx.rules[c].allowed_channels) for c in relevant)) - {"email"}))
        return _decision(ctx, "alternative_channel", mode_reasons, effective, applied, alt or ("alternative",))

    # ---- Tier 3: channel-specific checks for permitted_with_opt_out
    if any(ctx.channel not in ctx.rules[c].allowed_channels for c in relevant):
        reasons.append(Reason.CHANNEL_NOT_ALLOWED)
    if ctx.channel == "email" and p.email_verification != "valid":
        reasons.append(Reason.EMAIL_NOT_VERIFIED)
    if p.role_relevance is None or p.role_relevance < ctx.role_relevance_threshold:
        reasons.append(Reason.ROLE_NOT_RELEVANT)
    if reasons:
        return _decision(ctx, "block", reasons, effective, applied)

    channels = tuple(sorted(set.intersection(*(set(ctx.rules[c].allowed_channels) for c in relevant))))
    return _decision(ctx, "allow", [Reason.OK], effective, applied, channels)
