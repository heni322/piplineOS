from __future__ import annotations

import pytest

from app.domain.compliance.subscriber import classify_subscriber


@pytest.mark.parametrize(
    ("form", "domain", "matched", "expected"),
    [
        ("ltd", "acme.example", True, "corporate"),
        ("llp", "acme.example", True, "corporate"),
        ("plc", "acme.example", True, "corporate"),
        ("public_body", "council.example", True, "corporate"),
        ("sole_trader", "sam.example", True, "individual"),
        ("partnership", "partners.example", True, "individual"),
        ("ltd", "gmail.com", True, "individual"),  # free-mail address -> treat as individual
        ("ltd", "acme.example", False, "unknown"),  # not confirmed by registry -> unknown -> blocked
        ("unknown", "acme.example", True, "unknown"),
        (None, None, False, "unknown"),
    ],
)
def test_classification(form: str | None, domain: str | None, matched: bool, expected: str) -> None:
    assert classify_subscriber(form, domain, matched)[0] == expected
