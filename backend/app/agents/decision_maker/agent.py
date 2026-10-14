"""Agent 2 — Decision-Maker Finder: licensed enrichment only (no scraping), email verification, registry-based
subscriber classification, per-field sources. Deterministic: no LLM needed for this step."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from app.adapters.companies_house.base import CompaniesRegistry
from app.adapters.enrichment.base import EnrichedPerson, EnrichmentProvider
from app.adapters.verifier.base import EmailVerifier, PersonObjected
from app.agents.base import AgentContext
from app.agents.decision_maker.schemas import DecisionMaker
from app.core.crypto import blind_index, email_domain, normalise_domain
from app.db.session import DbContext, transaction
from app.domain.compliance.jurisdiction import parse_location
from app.domain.compliance.subscriber import classify_subscriber
from app.domain.leads.state_machine import State, transition
from app.domain.paid.gate import PaidCallDeferred
from app.domain.suppression.service import RecipientHashes, suppress_email
from app.domain.tenants.config import TenantConfig


def _has_phrase(title: str, phrase: str) -> bool:
    # whole-word match: "cto" must not match inside "director"
    return re.search(rf"\b{re.escape(phrase.lower())}\b", title.lower()) is not None


def role_relevance(title: str, cfg: TenantConfig) -> float:
    for p in cfg.personas:
        if any(_has_phrase(title, x) for x in p.titles):
            return 0.9
    for p in cfg.personas:
        if any(_has_phrase(title, k) for k in p.seniority_keywords):
            return 0.5
    return 0.2


def _pick(people: list[EnrichedPerson], cfg: TenantConfig) -> EnrichedPerson | None:
    ranked = sorted(people, key=lambda p: (role_relevance(p.title, cfg), p.confidence), reverse=True)
    return ranked[0] if ranked and role_relevance(ranked[0].title, cfg) >= 0.5 else None


def _adopt_domain(ctx: AgentContext, company_id: uuid.UUID, lead_id: uuid.UUID, domain: str) -> bool:
    """A company found by name (e.g. in a procurement notice) gets the web domain the provider resolved. If the
    client already has another company record with that domain, this lead is a duplicate and is closed: the same
    organisation is never worked twice."""
    d = normalise_domain(domain)
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        other = conn.execute(
            text("SELECT id FROM companies WHERE domain_normalised = :d AND id <> :c"), {"d": d, "c": company_id}
        ).scalar()
        if other is not None:
            transition(
                conn,
                lead_id,
                State.DISQUALIFIED,
                actor_type="agent",
                actor_id="decision_maker_finder",
                reason="company already known under the same web domain (duplicate)",
            )
            return False
        conn.execute(
            text("UPDATE companies SET domain = :d, domain_normalised = :d WHERE id = :c"), {"d": d, "c": company_id}
        )
    return True


def _objected_at_source(ctx: AgentContext, lead_id: uuid.UUID, email: str, source: str) -> None:
    """The data provider says this person objected to processing: suppress the address everywhere and close the
    lead. Nothing about the person is stored."""
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        suppress_email(
            conn,
            RecipientHashes.for_email(ctx.settings.key("suppression_hash_key"), email),
            "objection",
            source_ref=f"{source}:objected_at_source",
        )
        transition(
            conn,
            lead_id,
            State.DISQUALIFIED,
            actor_type="agent",
            actor_id="decision_maker_finder",
            reason="the contact has objected to processing at the data source",
        )


def find_decision_maker(
    ctx: AgentContext,
    lead_id: uuid.UUID,
    enrichment: EnrichmentProvider,
    verifier: EmailVerifier,
    registry: CompaniesRegistry,
) -> DecisionMaker | None:
    cfg = ctx.config
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        row = (
            conn.execute(
                text(
                    "SELECT l.state, c.id AS company_id, c.name, c.domain_normalised, c.country_code, "
                    "c.company_number "
                    "FROM leads l JOIN companies c ON c.id = l.company_id WHERE l.id = :l"
                ),
                {"l": lead_id},
            )
            .mappings()
            .one()
        )
    if row["state"] != State.DISCOVERED.value:
        return None  # idempotent: already processed
    titles = [t for p in cfg.personas for t in p.titles]
    try:
        # Paid providers defer here, BEFORE any credit is spent, unless all three live keys are on.
        for provider in (enrichment, verifier):
            preflight = getattr(provider, "preflight", None)
            if preflight is not None:
                preflight(lead_id, row["name"] or row["domain_normalised"] or "the company")
        person = _pick(enrichment.find_people(row["domain_normalised"] or "", titles, row["name"]), cfg)
    except PaidCallDeferred:
        return None  # recorded in the dry-run log; the lead stays "discovered" and is retried later
    if person is not None and not row["domain_normalised"] and person.company_domain:
        if not _adopt_domain(ctx, row["company_id"], lead_id, person.company_domain):
            return None
        row = {**dict(row), "domain_normalised": normalise_domain(person.company_domain)}  # type: ignore[assignment]
    if person is None:
        with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
            transition(
                conn,
                lead_id,
                State.DISQUALIFIED,
                actor_type="agent",
                actor_id="decision_maker_finder",
                reason="no contact matching the buyer persona",
            )
        return None

    try:
        verification = verifier.verify(person.professional_email) if person.professional_email else "unknown"
    except PaidCallDeferred:
        return None
    except PersonObjected as exc:
        _objected_at_source(ctx, lead_id, exc.email, verifier.name)
        return None
    reg = registry.lookup(row["name"], row["domain_normalised"], row["company_number"], row["country_code"])
    name_for = getattr(registry, "name_for", None)
    registry_name = name_for(row["country_code"]) if callable(name_for) else registry.name
    legal_form = reg.legal_form if reg else "unknown"
    company_country = reg.registered_country if reg else row["country_code"]
    sub_type, sub_reason = classify_subscriber(
        legal_form, email_domain(person.professional_email) if person.professional_email else None, reg is not None
    )
    if reg is not None and reg.status != "active" and sub_type == "corporate":
        # dissolved, bankrupt or in liquidation per the register: not treated as a live corporate subscriber
        sub_type, sub_reason = "unknown", f"registry_status:{reg.status}"
    loc = parse_location(person.location_text)
    relevance = role_relevance(person.title, cfg)
    now = datetime.now(UTC)
    sources = {
        "name": person.data_source,
        "title": person.data_source,
        "email": person.data_source,
        "email_verification": verifier.name,
        "location": person.data_source,
        "legal_form": registry_name if reg else "none",
        "subscriber_type": f"rule:{sub_reason}",
    }
    if person.profile_url:
        sources["profile_url"] = person.data_source

    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        personal = legal_form in ("sole_trader", "partnership")
        conn.execute(
            text("""UPDATE companies SET legal_form = :lf, legal_form_source = :src,
              company_number = coalesce(:cn, company_number),
              country_code = coalesce(:cc, country_code), name_is_personal = :personal,
              name = CASE WHEN :personal THEN NULL ELSE name END,
              name_enc = CASE WHEN :personal THEN CAST(:ne AS bytea) ELSE name_enc END WHERE id = :id"""),
            {
                "lf": legal_form,
                "src": registry_name if reg else None,
                "cn": reg.company_number if reg else None,
                "cc": company_country,
                "personal": personal,
                "id": row["company_id"],
                "ne": ctx.cipher.encrypt(conn, ctx.tenant_id, "companies.name", row["name"]) if personal else None,
            },
        )
        email = person.professional_email
        bidx = blind_index(ctx.settings.key("blind_index_key"), email, ctx.tenant_id) if email else None
        existing = (
            conn.execute(text("SELECT id FROM prospects WHERE email_bidx = :b"), {"b": bidx}).scalar() if bidx else None
        )
        prospect_id = (
            existing
            or conn.execute(
                text("""INSERT INTO prospects (tenant_id, company_id, name_enc, title,
              email_enc, email_bidx, profile_url_enc, country_code, subdivision_code, subscriber_type,
              subscriber_type_reason, email_verification, email_verified_at, role_relevance, confidence_score,
              collected_at) VALUES (:t, :c, :n, :ti, :e, :b, :pu, :cc, :sd, :st, :sr, :v, :va, :rr, :cs, :at)
              RETURNING id"""),
                {
                    "t": ctx.tenant_id,
                    "c": row["company_id"],
                    "n": ctx.cipher.encrypt(conn, ctx.tenant_id, "prospects.name", person.person_name),
                    "ti": person.title,
                    "e": ctx.cipher.encrypt(conn, ctx.tenant_id, "prospects.email", email) if email else None,
                    "b": bidx,
                    "pu": ctx.cipher.encrypt(conn, ctx.tenant_id, "prospects.profile_url", person.profile_url)
                    if person.profile_url
                    else None,
                    "cc": loc.country,
                    "sd": loc.subdivision,
                    "st": sub_type,
                    "sr": sub_reason,
                    "v": verification,
                    "va": now,
                    "rr": relevance,
                    "cs": person.confidence,
                    "at": now,
                },
            ).scalar_one()
        )
        for fname, src in sources.items():
            conn.execute(
                text("""INSERT INTO prospect_field_sources (tenant_id, prospect_id, field_name, data_source,
                  collected_at, lawful_basis_note) VALUES (:t, :p, :f, :s, :at, 'legitimate interests (tenant LIA)')
                  ON CONFLICT (prospect_id, field_name, data_source) DO NOTHING"""),
                {"t": ctx.tenant_id, "p": prospect_id, "f": fname, "s": src, "at": now},
            )
        dup = conn.execute(
            text("SELECT 1 FROM leads WHERE campaign_id = :cp AND prospect_id = :p AND id <> :l"),
            {"cp": ctx.campaign_id, "p": prospect_id, "l": lead_id},
        ).first()
        if dup:  # same person already in this campaign: never contact twice
            transition(
                conn,
                lead_id,
                State.DISQUALIFIED,
                actor_type="agent",
                actor_id="decision_maker_finder",
                reason="duplicate prospect in campaign",
            )
            return None
        conn.execute(text("UPDATE leads SET prospect_id = :p WHERE id = :l"), {"p": prospect_id, "l": lead_id})
        transition(
            conn,
            lead_id,
            State.ENRICHED,
            actor_type="agent",
            actor_id="decision_maker_finder",
            reason=f"contact found ({person.data_source})",
        )
    return DecisionMaker(
        person_name=person.person_name,
        title=person.title,
        company=row["name"],
        country=loc.country,
        verified_email=email if verification == "valid" else None,
        subscriber_type=sub_type,
        linkedin_url=person.profile_url,
        data_source=person.data_source,
        collected_at=now,
        confidence_score=person.confidence,
    )
