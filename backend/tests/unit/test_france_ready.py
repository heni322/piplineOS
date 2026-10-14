"""Stage 3 (France) readiness, all switched off until counsel reviews France: the French company register adapter,
country routing, French fixed texts and dates, French writing checks and the French footer."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.adapters.companies_house.mock import MockCompaniesHouse
from app.adapters.registries.france import FranceRegistry, legal_form, normalise_name
from app.adapters.registries.routing import RoutingRegistry
from app.agents.writer.checks import check_draft
from app.agents.writer.schemas import WriterDraft
from app.core import i18n
from app.domain.outbound.footer import FooterTemplate, finalise_body

ROOT = Path(__file__).resolve().parents[3]


def fr_result(siren: str, name: str, code: str, etat: str = "A", diffusion: str = "O") -> dict[str, object]:
    return {
        "siren": siren,
        "nom_complet": name,
        "nom_raison_sociale": name,
        "nature_juridique": code,
        "etat_administratif": etat,
        "statut_diffusion": diffusion,
        "dirigeants": [{"nom": "Privé", "prenoms": "Jean"}],
    }


def fr_registry(results: list[dict[str, object]]) -> tuple[FranceRegistry, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={"results": results, "total_results": len(results)})

    return FranceRegistry(transport=httpx.MockTransport(handler)), calls


@pytest.mark.parametrize(
    ("code", "form"),
    [
        ("1000", "sole_trader"),
        ("2110", "partnership"),
        ("5499", "ltd"),
        ("5710", "ltd"),
        ("5599", "plc"),
        ("5306", "lp"),
        ("6540", "other_corporate"),
        ("7210", "public_body"),
        ("9220", "other_corporate"),
        ("9300", "charity"),
        ("", "unknown"),
        ("12", "unknown"),
    ],
)
def test_french_legal_forms(code: str, form: str) -> None:
    assert legal_form(code) == form


def test_france_lookup_by_siren_and_by_exact_name() -> None:
    reg, calls = fr_registry([fr_result("552032534", "DANONE", "5599")])
    hit = reg.lookup("Danone SA", company_number="552 032 534")
    assert hit is not None and (hit.company_number, hit.legal_form, hit.registered_country) == (
        "552032534",
        "plc",
        "FR",
    )
    assert calls[0].url.host == "recherche-entreprises.api.gouv.fr" and calls[0].url.params["q"] == "552032534"
    reg, _ = fr_registry([fr_result("111111111", "Lumière Conseil SAS", "5710")])
    assert reg.lookup("LUMIERE CONSEIL").legal_form == "ltd"  # type: ignore[union-attr]
    assert normalise_name("Société Générale SA") == "generale"


def test_france_ambiguous_restricted_or_unknown_returns_none() -> None:
    two, _ = fr_registry([fr_result("1" * 9, "Atlas Conseil", "5710"), fr_result("2" * 9, "ATLAS CONSEIL", "5499")])
    assert two.lookup("Atlas Conseil") is None
    hidden, _ = fr_registry([fr_result("3" * 9, "Dupont Services", "1000", diffusion="P")])
    assert hidden.lookup("Dupont Services") is None
    none, _ = fr_registry([])
    assert none.lookup("Nowhere SARL", company_number="999999999") is None


def test_routing_sends_each_company_to_its_own_register() -> None:
    gb = MockCompaniesHouse({})
    fr, calls = fr_registry([fr_result("552032534", "DANONE", "5599")])
    r = RoutingRegistry({"GB": gb, "FR": fr})
    assert r.lookup("Danone", country="FR") is not None and len(calls) == 1
    assert r.lookup("Danone", country="GB") is None and len(calls) == 1
    assert r.lookup("Somewhere GmbH", country="DE") is None  # no register adapter: unknown -> blocked
    assert r.name_for("FR") == "registry.fr.recherche_entreprises" and r.name_for("DE") == "none"


def test_fixed_texts_and_dates_in_french_and_english_unchanged() -> None:
    at = datetime(2026, 10, 15, 13, 0, tzinfo=UTC).astimezone(__import__("zoneinfo").ZoneInfo("Europe/Paris"))
    assert i18n.date_label(at, "Europe/Paris", "fr-FR") == "jeudi 15 octobre à 15h00 (heure de Paris)"
    london = datetime(2026, 10, 15, 13, 0, tzinfo=UTC).astimezone(__import__("zoneinfo").ZoneInfo("Europe/London"))
    assert i18n.date_label(london, "Europe/London", "en-GB") == "Thursday 15 October at 14:00 (UK time)"
    assert i18n.t("fr-FR", "greeting", name="Claire") == "Bonjour Claire,"
    assert i18n.t("de-DE", "greeting", name="Max") == "Hi Max,"  # unsupported -> English
    assert set(i18n.CATALOGUE["fr-FR"]) == set(i18n.CATALOGUE["en-GB"])  # no missing translation
    assert i18n.from_accept_language("fr-FR,fr;q=0.9,en;q=0.8") == "fr-FR"
    assert i18n.from_accept_language("en-GB,en;q=0.9,fr;q=0.5") == "en-GB"
    assert i18n.from_accept_language("de-DE") == "en-GB" and i18n.from_accept_language(None) == "en-GB"


def _draft(body: str) -> WriterDraft:
    return WriterDraft.model_validate(
        {
            "subject_lines": ["Votre nouveau contrat", "Une question rapide", "Support informatique"],
            "email_body": body,
            "follow_ups": [
                "Je me permets de revenir vers vous. Un résumé vous serait-il utile ?",
                "Dernier message de ma part : souhaitez-vous le résumé ?",
            ],
            "claims": [
                {"text": "Lumière Conseil a remporté le marché de support informatique de la ville", "source_id": "s1"}
            ],
            "language": "fr-FR",
        }
    )


def test_french_drafts_must_use_vous_and_never_write_legal_text() -> None:
    src = {"s1": "Lumière Conseil a remporté le marché de support informatique de la ville de Lyon."}
    ok = _draft(
        "Bonjour Claire,\n\nJ'ai vu que Lumière Conseil a remporté le marché de support informatique de la ville. "
        "Un résumé d'une page sur le renfort de votre centre de services vous serait-il utile ?\n\nJordan"
    )
    r = check_draft(
        ok,
        source_ids={"s1"},
        first_name="Claire",
        language="fr-FR",
        follow_ups_expected=2,
        banned_phrases=[],
        source_texts=src,
    )
    assert r.passed, r.blocking
    tu = _draft(
        "Bonjour Claire,\n\nJ'ai vu que Lumière Conseil a remporté le marché de support informatique de la ville. "
        "Est-ce que ton équipe aurait besoin d'aide ?\n\nJordan"
    )
    r = check_draft(
        tu,
        source_ids={"s1"},
        first_name="Claire",
        language="fr-FR",
        follow_ups_expected=2,
        banned_phrases=[],
        source_texts=src,
    )
    assert any("vous" in b for b in r.blocking)
    legal = _draft(
        "Bonjour Claire,\n\nJ'ai vu que Lumière Conseil a remporté le marché de support informatique de la ville. "
        "Offre gratuite ! Pour vous désinscrire, répondez STOP. Un échange ?\n\nJordan"
    )
    r = check_draft(
        legal,
        source_ids={"s1"},
        first_name="Claire",
        language="fr-FR",
        follow_ups_expected=2,
        banned_phrases=[],
        source_texts=src,
    )
    assert any("footer/legal" in b for b in r.blocking) and any("gratuit" in b for b in r.blocking)


def test_french_footer_renders_every_required_element() -> None:
    tpl = FooterTemplate.load(ROOT / "config" / "footers" / "fr-FR.yaml")
    text = tpl.render(
        {
            "sender_name": "Jordan Lee",
            "sender_title": "Partenariats",
            "company_legal_name": "Northwind SAS",
            "registration_jurisdiction": "France (RCS Lyon)",
            "company_number": "123 456 789",
            "registered_office_address": "1 rue de l'Exemple, 69001 Lyon",
            "data_source_summary": "un fournisseur agréé",
            "purpose": "le renfort de votre centre de services",
            "unsubscribe_url": "https://u.test/u/tok",
            "privacy_url": "https://example.test/privacy/x",
        }
    )
    final = finalise_body(
        "Bonjour Claire,\n\nUn résumé vous serait-il utile ?\n\nJordan",
        text,
        "https://u.test/u/tok",
        "https://example.test/privacy/x",
    )
    for part in ("Northwind SAS", "123 456 789", "https://u.test/u/tok", "Politique de confidentialité", "opposer"):
        assert part in final
