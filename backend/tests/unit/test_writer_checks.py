"""The deterministic gate on writer output: every flaw class is caught, good drafts pass."""

from __future__ import annotations

import pytest

from app.agents.writer.checks import check_draft
from app.agents.writer.schemas import Claim, WriterDraft

CLAIM = "Contoso Managed IT is hiring a Head of Service Delivery to lead a team of 25 engineers"
GOOD = WriterDraft(
    language="en-GB",
    subject_lines=["Contoso service desk capacity", "Question about the new role", "Out-of-hours cover for Contoso"],
    email_body=f"Hi Alex,\n\nI saw that {CLAIM}.\n\nGrowth like that often stretches the service desk out of hours. We "
    "provide overflow cover staffed by UK-based engineers so your team can prioritise project work.\n\n"
    "Would a one-page summary be useful?\n\nJordan",
    follow_ups=[
        "Hi Alex, bringing this back up in case extra capacity is on the radar. Would a summary help?",
        "Hi Alex, last note from me. If the timing is wrong, no follow-ups from me. Shall I close the loop?",
    ],
    claims=[Claim(text=CLAIM, source_id="s1")],
)


def run(d: WriterDraft, **kw):  # type: ignore[no-untyped-def]
    args = {
        "source_ids": {"s1"},
        "first_name": "Alex",
        "language": "en-GB",
        "follow_ups_expected": 2,
        "banned_phrases": ["game-changer"],
    } | kw
    return check_draft(d, **args)


def test_good_draft_passes() -> None:
    r = run(GOOD)
    assert r.passed, r.blocking


def body(new: str) -> WriterDraft:
    return GOOD.model_copy(update={"email_body": new})


BAD = {
    "unsourced claim id": GOOD.model_copy(update={"claims": [Claim(text=CLAIM, source_id="s9")]}),
    "claim not verbatim": GOOD.model_copy(update={"claims": [Claim(text="Contoso doubled revenue", source_id="s1")]}),
    "no claims at all": GOOD.model_copy(update={"claims": []}),
    "too long": body(GOOD.email_body.replace("Would", "word " * 60 + "Would")),
    "us spelling": body(GOOD.email_body.replace("prioritise", "prioritize")),
    "link": body(GOOD.email_body.replace("Jordan", "See https://northwind.example/offer\n\nJordan")),
    "bare domain": body(GOOD.email_body.replace("Jordan", "More at northwind-demo.example\n\nJordan")),
    "email address": body(GOOD.email_body.replace("Jordan", "jordan@northwind.example")),
    "phone": body(GOOD.email_body.replace("Jordan", "Call 0113 496 0000\n\nJordan")),
    "spam": body(GOOD.email_body.replace("Would", "This is a FREE, guaranteed offer. Would")),
    "high friction": body(
        GOOD.email_body.replace("Would a one-page summary be useful?", "Can we book a call for 30 minutes on Tuesday?")
    ),
    "sensitive": body(GOOD.email_body.replace("Growth like", "Congrats on your wife's news. Growth like")),
    "no question": body(GOOD.email_body.replace("Would a one-page summary be useful?", "Let me know.")),
    "footer text": body(GOOD.email_body.replace("Jordan", "Jordan\nReply unsubscribe to opt out.")),
    "masked token": body(GOOD.email_body.replace("Hi Alex", "Hi A***")),
    "unsourced figure": body(GOOD.email_body.replace("Growth like that", "With 40% growth that")),
    "wrong language": GOOD.model_copy(update={"language": "en-US"}),
    "follow-up count": GOOD.model_copy(update={"follow_ups": GOOD.follow_ups[:1]}),
    "banned phrase": body(GOOD.email_body.replace("Growth like that", "This game-changer")),
    "subject too long": GOOD.model_copy(update={"subject_lines": ["x" * 70, "a", "b"]}),
    "long follow-up": GOOD.model_copy(update={"follow_ups": ["word " * 70, GOOD.follow_ups[1]]}),
    "follow-up link": GOOD.model_copy(update={"follow_ups": ["Hi Alex, see www.evil.example?", GOOD.follow_ups[1]]}),
    "follow-up unsourced number": GOOD.model_copy(
        update={"follow_ups": ["Hi Alex, 300 MSPs use us. Interested?", GOOD.follow_ups[1]]}
    ),
}


@pytest.mark.parametrize("case", list(BAD))
def test_each_flaw_is_blocked(case: str) -> None:
    assert not run(BAD[case]).passed, case


def test_missing_first_name_is_only_a_warning() -> None:
    r = run(body(GOOD.email_body.replace("Hi Alex", "Hello")))
    assert r.passed and any("first name" in w for w in r.warnings)


def test_cited_claim_must_be_supported_by_the_source_text() -> None:
    src = {"s1": "Contoso Managed IT is hiring a Head of Service Delivery to lead a team of 25 engineers."}
    assert run(GOOD, source_texts=src).passed
    invented = GOOD.model_copy(
        update={
            "email_body": GOOD.email_body.replace(
                "Growth like that", "Contoso doubled revenue last year. Growth like that"
            ),
            "claims": [*GOOD.claims, Claim(text="Contoso doubled revenue last year", source_id="s1")],
        }
    )
    r = run(invented, source_texts=src)
    assert any("not supported by its source" in b for b in r.blocking)
    wrong_number = GOOD.model_copy(
        update={
            "email_body": GOOD.email_body.replace("25 engineers", "45 engineers"),
            "claims": [Claim(text=CLAIM.replace("25", "45"), source_id="s1")],
        }
    )
    assert not run(wrong_number, source_texts=src).passed
