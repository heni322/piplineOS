"""Agent 4 deterministic rules, slot parsing, DP-request deadlines/regimes and signed-link domain separation."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from app.agents.reply_handler.agent import check_reply
from app.agents.reply_handler.rules import apply_rules, detect_language, extract_return_date, parse_slot_choice
from app.demo.dataset import DEMO_CONFIG
from app.domain.dpr.service import add_one_calendar_month, days_remaining, regime_for
from app.domain.suppression import unsubscribe

R = date(2026, 10, 13)
LABELS = [
    "Wednesday 14 October at 10:00 (UK time)",
    "Thursday 15 October at 14:00 (UK time)",
    "Friday 16 October at 10:00 (UK time)",
]


@pytest.mark.parametrize(
    "text",
    [
        "Please remove me from your mailing list.",
        "STOP",
        "Take me off your list",
        "Désinscrivez-moi s'il vous plaît",
        "Bitte entfernen Sie mich aus Ihrem Verteiler",  # D13: German phrasing was missed
        "Graag uitschrijven",
        "Por favor, darme de baja",
    ],
)
def test_unsubscribe_is_detected_in_several_languages(text: str) -> None:
    assert apply_rules(text, R).safety == "unsubscribe"


@pytest.mark.parametrize(
    ("text", "dp_type"),
    [
        ("Please delete all the data you hold about me.", "erasure"),  # D14: was missed
        ("Where did you get my details?", "access"),
        ("Under GDPR, what personal data do you hold about me?", "access"),
        ("I object to the processing of my data.", "objection"),
        ("Please rectify my details.", "rectification"),
        ("Ich verlange Auskunft nach DSGVO.", "access"),
        ("Wipe any records you have about me.", "erasure"),
    ],
)
def test_data_protection_requests_and_their_type(text: str, dp_type: str) -> None:
    r = apply_rules(text, R)
    assert r.safety == "data_protection_request" and r.dp_type == dp_type


def test_dp_request_outranks_unsubscribe_and_objection() -> None:
    r = apply_rules("Unsubscribe me, stop emailing me and delete my data.", R)
    assert r.safety == "data_protection_request" and r.dp_type == "erasure"


def test_objection_is_not_read_as_interest() -> None:
    r = apply_rules("Please don't email me again", R)  # D15: 'please do' matched inside "please don't"
    assert r.safety == "objection" and not r.interested_hint


def test_out_of_office_return_dates() -> None:
    assert extract_return_date("I am away until 2 November 2026", R) == date(2026, 11, 2)
    assert extract_return_date("Out of office until 03/11/2026", R) == date(2026, 11, 3)  # UK day-first
    assert extract_return_date("back on Monday 19th October", R) == date(2026, 10, 19)
    r = apply_rules("Automatic reply: out of the office, limited access to email.", R)
    assert r.out_of_office and r.ooo_return_date is None


def test_referral_extracts_name_and_email_but_not_the_sender() -> None:
    text = "Not the right person. Please speak to Sam Okafor (sam.okafor@woodgrove-it.example). Me: a@b.example"
    r = apply_rules(text, R, sender_email="a@b.example")
    assert r.referral_hint and r.referral_email == "sam.okafor@woodgrove-it.example" and r.referral_name == "Sam Okafor"


def test_escalation_flags_and_ai_question() -> None:
    r = apply_rules("This is harassment, I'm reporting you to the ICO. Are you a bot? How much is it?", R)
    assert set(r.flags) >= {"anger", "legal", "pricing"} and r.asks_if_ai


def test_language_detection() -> None:
    assert detect_language("Thanks, this is not for us and we are fine with our provider") == "en"
    assert detect_language("Oui, ça m'intéresse, parlons-en la semaine prochaine.") == "fr"  # D16
    assert detect_language("Ja, das klingt gut, bitte schicken Sie mir mehr") == "de"


@pytest.mark.parametrize(
    ("text", "idx"),
    [
        ("Option 2 works for me", 1),
        ("The Thursday one please", 1),
        ("Friday 16 October is good", 2),
        ("2nd please", 1),
        ("Let's do the first", 0),
        ("the third one", 2),
        ("Any of those is fine", None),
        ("First of all thanks, Thursday works", 1),
        ("Option 7", None),
    ],
)
def test_slot_choice(text: str, idx: int | None) -> None:
    assert parse_slot_choice(text, LABELS) == idx


def test_one_calendar_month_deadline() -> None:
    assert add_one_calendar_month(datetime(2026, 10, 13, tzinfo=UTC)) == datetime(2026, 11, 13, tzinfo=UTC)
    assert add_one_calendar_month(datetime(2027, 1, 31, tzinfo=UTC)) == datetime(2027, 2, 28, tzinfo=UTC)
    assert add_one_calendar_month(datetime(2026, 12, 15, tzinfo=UTC)) == datetime(2027, 1, 15, tzinfo=UTC)
    assert days_remaining(datetime(2026, 11, 13, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC)) == 31


def test_regime() -> None:
    assert regime_for("GB", "GB") == "uk_gdpr"
    assert regime_for("IE", "IE") == "eu_gdpr"
    assert regime_for("GB", "IE") == "both"
    assert regime_for(None, None) == "both"


def test_reschedule_and_unsubscribe_tokens_are_domain_separated() -> None:
    key, tid = b"k" * 32, uuid.uuid4()
    unsub = unsubscribe.sign(key, tid)
    resched = unsubscribe.sign(key, tid, unsubscribe.RESCHEDULE)
    assert unsub != resched
    assert unsubscribe.verify(key, resched, unsubscribe.RESCHEDULE) == tid
    with pytest.raises(unsubscribe.InvalidToken):
        unsubscribe.verify(key, unsub, unsubscribe.RESCHEDULE)
    with pytest.raises(unsubscribe.InvalidToken):
        unsubscribe.verify(key, resched)


@pytest.mark.parametrize(
    ("body", "issue"),
    [
        ("Hi Alex,\n\nSee https://x.example for details. Would that help?\n\nJordan", "links"),
        ("Hi Alex,\n\nIt is only £99 per month, guaranteed. Shall we talk?\n\nJordan", "prices"),
        ("Hi Alex,\n\nI'm a real person, honestly. Shall we talk?\n\nJordan", "human"),
        ("Hi Alex,\n\nWe can optimize your service desk. Shall we talk?\n\nJordan", "spelling"),
    ],
)
def test_ai_reply_gate(body: str, issue: str) -> None:
    issues = check_reply(body, DEMO_CONFIG, "en", need_disclosure=False)
    assert any(issue in i.lower() or (issue == "spelling" and "optimize" in i.lower()) for i in issues), issues


def test_ai_reply_gate_passes_a_clean_reply() -> None:
    body = "Hi Alex,\n\nThanks for letting me know. I will get back in touch in the new year.\n\nJordan"
    assert check_reply(body, DEMO_CONFIG, "en", need_disclosure=False) == []


def test_qualification_ignores_quotes_the_prospect_never_wrote() -> None:
    """Evidence must be verbatim from the reply; a model inventing a quote cannot mark a criterion met."""
    import json
    from types import SimpleNamespace
    from typing import Any

    from app.agents.reply_handler.agent import qualify
    from app.llm.backends import MockBackend
    from app.llm.client import LLMClient

    answer = {
        "criteria": [
            {"id": "need", "met": True, "evidence_quote": "Our service desk is overloaded", "confidence": 0.95},
            {"id": "timing", "met": True, "evidence_quote": "We want this live by January", "confidence": 0.95},
        ],
        "summary": "x",
    }
    llm = LLMClient(
        MockBackend([json.dumps(answer)]), {"volume": "m", "reasoning": "m"}, lambda _r: None, sleep=lambda _s: None
    )
    ctx: Any = SimpleNamespace(llm=llm, config=DEMO_CONFIG, tenant_id=uuid.UUID(int=1))
    overall, evidence, _ = qualify(
        ctx, uuid.UUID(int=2), ["Our service desk is overloaded at weekends."], 0.9, "Head of Service Delivery"
    )
    assert evidence["need"]["met"] is True
    assert evidence["timing"]["met"] is None and evidence["timing"]["rejected_quote"] is True
    assert overall is None  # incomplete: ask a qualifying question rather than book
