from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.domain.outbound.footer import FooterError, FooterTemplate, finalise_body
from app.domain.outbound.modes import resolve_mode
from app.domain.suppression import unsubscribe

KEY = b"k" * 32
CONFIG = Path(__file__).resolve().parents[3] / "config"


def test_unsubscribe_token_roundtrip() -> None:
    tid = uuid.uuid4()
    assert unsubscribe.verify(KEY, unsubscribe.sign(KEY, tid)) == tid


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"),  # flipped signature character
        lambda t: ("A" if t[0] != "A" else "B") + t[1:],  # flipped token id character
        lambda t: t[:10],  # truncated
        lambda t: "not-base64-!!",
        lambda t: "",
    ],
)
def test_tampered_unsubscribe_token_is_rejected(mutate) -> None:  # type: ignore[no-untyped-def]
    token = unsubscribe.sign(KEY, uuid.uuid4())
    with pytest.raises(unsubscribe.InvalidToken):
        unsubscribe.verify(KEY, mutate(token))


def test_token_signed_with_other_key_is_rejected() -> None:
    with pytest.raises(unsubscribe.InvalidToken):
        unsubscribe.verify(KEY, unsubscribe.sign(b"x" * 32, uuid.uuid4()))


def test_token_contains_no_pii() -> None:
    token = unsubscribe.sign(KEY, uuid.uuid4())
    assert "@" not in token and len(token) == 43


VALUES = {
    "sender_name": "Jordan Lee",
    "sender_title": "Partnerships",
    "company_legal_name": "Northwind Ltd",
    "registration_jurisdiction": "England & Wales",
    "company_number": "01234567",
    "registered_office_address": "1 Example St, Leeds",
    "data_source_summary": "a licensed data provider",
    "purpose": "IT support",
    "privacy_url": "https://example.test/privacy",
}


def test_footer_renders_all_mandatory_elements() -> None:
    tpl = FooterTemplate.load(CONFIG / "footers" / "en-GB.yaml")
    footer = tpl.render(VALUES)
    final = finalise_body("Hi Alex,\n\nQuick question?", footer, "https://u.test/u/abc", VALUES["privacy_url"])
    for v in (
        "Jordan Lee",
        "Northwind Ltd",
        "01234567",
        "1 Example St, Leeds",
        "https://u.test/u/abc",
        "https://example.test/privacy",
        "unsubscribe",
    ):
        assert v in final


@pytest.mark.parametrize("missing", ["company_number", "registered_office_address", "privacy_url", "sender_name"])
def test_footer_refuses_missing_mandatory_value(missing: str) -> None:
    tpl = FooterTemplate.load(CONFIG / "footers" / "en-GB.yaml")
    with pytest.raises(FooterError):
        tpl.render({**VALUES, missing: ""})


def test_finalise_refuses_footer_without_unsubscribe() -> None:
    with pytest.raises(FooterError):
        finalise_body("Hi", "no link here", "https://u.test/u/abc", "https://p")


@pytest.mark.parametrize(
    ("adapter", "tenant", "glob", "checklist", "expected"),
    [
        ("live", True, True, True, "live"),
        ("live", False, True, True, "dry_run"),
        ("live", True, False, True, "dry_run"),
        ("live", True, True, False, "dry_run"),
        ("mock", True, True, True, "dry_run"),
        ("sandbox", True, True, True, "sandbox"),
        ("sandbox", False, False, False, "sandbox"),
    ],
)
def test_three_key_switch(adapter: str, tenant: bool, glob: bool, checklist: bool, expected: str) -> None:
    assert (
        resolve_mode(adapter_mode=adapter, tenant_live=tenant, global_go_live=glob, golive_complete=checklist)
        == expected
    )
