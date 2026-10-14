"""Stage 4 (Spain, Italy, Portugal, Belgium) readiness, all switched off until counsel reviews each country: fixed
texts and dates in Spanish, Italian, Portuguese and Dutch, footers, writing checks, places, consumer mail, and the
Belgian register's number check and legal-form mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.adapters.registries import belgium
from app.agents.writer.checks import check_draft
from app.agents.writer.schemas import WriterDraft
from app.core import i18n
from app.domain.compliance.jurisdiction import parse_location
from app.domain.compliance.subscriber import classify_subscriber
from app.domain.outbound.footer import FooterTemplate, finalise_body

ROOT = Path(__file__).resolve().parents[3]
LANGS = ["es-ES", "it-IT", "pt-PT", "nl-BE"]


def test_every_language_has_every_text_and_a_valid_footer() -> None:
    for lg in LANGS:
        assert set(i18n.CATALOGUE[lg]) == set(i18n.CATALOGUE["en-GB"]), lg
        tpl = FooterTemplate.load(ROOT / "config" / "footers" / f"{lg}.yaml")
        values = dict.fromkeys(
            [
                "sender_name",
                "sender_title",
                "company_legal_name",
                "registration_jurisdiction",
                "company_number",
                "registered_office_address",
                "data_source_summary",
                "purpose",
            ],
            "x",
        ) | {"privacy_url": "https://p.example/n"}
        final = finalise_body("Body", tpl.render(values), "https://u.example/1", "https://p.example/n")
        assert "https://u.example/1" in final and tpl.locale == lg


def test_dates_in_each_language() -> None:
    at = datetime(2026, 10, 15, 13, 0, tzinfo=UTC)
    cases = {
        ("es-ES", "Europe/Madrid"): "jueves 15 de octubre a las 15:00 (hora de Madrid)",
        ("it-IT", "Europe/Rome"): "giovedì 15 ottobre alle 15:00 (ora italiana)",
        ("pt-PT", "Europe/Lisbon"): "quinta-feira, 15 de outubro às 14:00 (hora de Lisboa)",
        ("nl-BE", "Europe/Brussels"): "donderdag 15 oktober om 15:00 (Belgische tijd)",
    }
    for (lg, tz), want in cases.items():
        assert i18n.date_label(at.astimezone(ZoneInfo(tz)), tz, lg) == want
    assert i18n.lang("nl-NL") == "nl-BE" and i18n.lang("es-MX") == "es-ES" and i18n.lang("de-DE") == "en-GB"
    assert i18n.from_accept_language("it-IT,it;q=0.9,en;q=0.8") == "it-IT"
    assert i18n.from_accept_language("de-DE,nl;q=0.5") == "nl-BE"


def _draft(body: str, language: str) -> WriterDraft:
    return WriterDraft.model_validate(
        {
            "subject_lines": ["Una pregunta", "Su nuevo contrato", "Soporte"],
            "email_body": body,
            "follow_ups": ["Vuelvo a escribirle. ¿Le sería útil un resumen?", "Último mensaje: ¿quiere el resumen?"],
            "claims": [{"text": "Atlas Soluciones ganó el contrato de soporte informático", "source_id": "s1"}],
            "language": language,
        }
    )


SRC = {"s1": "Atlas Soluciones ganó el contrato de soporte informático del ayuntamiento."}


def _check(body: str, language: str = "es-ES") -> list[str]:
    return check_draft(
        _draft(body, language),
        source_ids={"s1"},
        first_name="Lucía",
        language=language,
        follow_ups_expected=2,
        banned_phrases=[],
        source_texts=SRC,
    ).blocking


def test_writing_checks_formal_address_spam_and_legal_text() -> None:
    ok = (
        "Hola, Lucía:\n\nHe visto que Atlas Soluciones ganó el contrato de soporte informático. "
        "¿Le sería útil un resumen de una página sobre cómo reforzar su equipo de TI?\n\nJordan"
    )
    assert _check(ok) == []  # "TI" (IT) is not the informal "ti"
    informal = ok.replace("¿Le sería útil", "¿Te sería útil").replace("su equipo", "tu equipo")
    assert any("usted" in b for b in _check(informal))
    spam = ok.replace("¿Le sería", "Es gratis y sin compromiso. Para darse de baja responda NO. ¿Le sería")
    blocking = _check(spam)
    assert any("gratis" in b for b in blocking) and any("legal text" in b for b in blocking)
    pushy = ok.replace("¿Le sería útil un resumen", "¿Podemos reservar una llamada")
    assert any("high-friction" in b for b in _check(pushy))


@pytest.mark.parametrize(
    ("language", "text", "formal"),
    [("it-IT", "Il tuo team ha vinto il contratto?", "Lei"), ("pt-PT", "A tua equipa ganhou o contrato?", "senhor"),
     ("nl-BE", "Heeft jouw team het contract gewonnen?", "'u'")],
)  # fmt: skip
def test_informal_address_in_italian_portuguese_dutch(language: str, text: str, formal: str) -> None:
    assert any(formal in b for b in _check(f"Buongiorno Lucía,\n\n{text}\n\nJordan", language))


def test_places_mail_and_belgian_numbers() -> None:
    assert parse_location("Gent, België").country == "BE"
    assert parse_location("Milano").country == "IT"
    assert parse_location("Lisboa").country == "PT"
    assert parse_location("Sevilla, España").country == "ES"
    for domain in ("libero.it", "sapo.pt", "telefonica.net", "skynet.be"):
        assert classify_subscriber("ltd", domain, True)[0] == "individual"
    assert belgium.valid_enterprise_number("0403.170.701") == "0403170701"
    assert belgium.valid_enterprise_number("403170701") == "0403170701"
    assert belgium.valid_enterprise_number("0403.170.702") is None


@pytest.mark.parametrize(
    ("description", "form"),
    [
        ("Naamloze vennootschap", "plc"),
        ("Besloten vennootschap", "ltd"),
        ("Société à responsabilité limitée", "ltd"),
        ("Coöperatieve vennootschap met beperkte aansprakelijkheid", "other_corporate"),
        ("Commanditaire vennootschap op aandelen", "lp"),
        ("Vennootschap onder firma", "partnership"),
        ("Vereniging zonder winstoogmerk", "other_corporate"),
        ("Private stichting", "charity"),
        ("Publiekrechtelijke rechtspersoon", "public_body"),
        ("Onbekende vorm", "unknown"),
    ],
)
def test_belgian_legal_forms_come_from_the_official_descriptions(description: str, form: str) -> None:
    assert belgium.legal_form_from_description(description) == form


def test_belgium_is_routed_to_the_local_copy_only_when_switched_on() -> None:
    from app.adapters.companies_house.mock import MockCompaniesHouse
    from app.adapters.registry import providers_for
    from app.settings import Settings

    assert isinstance(providers_for(Settings()).registry.by_country["BE"], MockCompaniesHouse)  # type: ignore[attr-defined]
    live = providers_for(Settings(be_registry_adapter_mode="live"))
    assert live.registry.name_for("BE") == "registry.be.kbo_open_data"  # type: ignore[attr-defined]
    live_ie = providers_for(Settings(ie_registry_adapter_mode="live"))
    assert live_ie.registry.name_for("IE") == "registry.ie.cro_open_data"  # type: ignore[attr-defined]
    for cc in ("ES", "IT", "PT"):  # no register adapter (paid registers: owner decision) -> unknown -> blocked
        assert live.registry.lookup("Atlas Soluciones SL", country=cc) is None  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("company_type", "form"),
    [
        ("LTD - Private Company Limited by Shares", "ltd"),
        ("DAC - Designated Activity Company", "ltd"),
        ("PLC - Public Limited Company", "plc"),
        ("CLG - Company Limited by Guarantee", "other_corporate"),
        ("UC - Private Unlimited Company", "other_corporate"),
        ("Limited Partnership", "lp"),
        ("External Company", "other_corporate"),
        ("Something New", "unknown"),
    ],
)
def test_irish_company_types(company_type: str, form: str) -> None:
    from app.adapters.registries.ireland import legal_form_from_type

    assert legal_form_from_type(company_type) == form
