from __future__ import annotations

import pytest

from app.agents.decision_maker.agent import role_relevance
from app.agents.writer.agent import short_name
from app.demo.dataset import DEMO_CONFIG


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Head of Service Delivery", 0.9),
        ("Chief Technology Officer", 0.9),
        ("Service Desk Manager", 0.9),
        ("Managing Director", 0.5),
        ("Head of Marketing", 0.5),
        ("Receptionist", 0.2),
    ],
)
def test_role_relevance(title: str, expected: float) -> None:
    assert role_relevance(title, DEMO_CONFIG) == expected


@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("Contoso Managed IT Ltd", "Contoso Managed IT"),
        ("Woodgrove IT plc", "Woodgrove IT"),
        ("Fabrikam LLP", "Fabrikam"),
        ("Sam Patel IT Consulting", "Sam Patel IT Consulting"),
    ],
)
def test_short_company_name(name: str, short: str) -> None:
    assert short_name(name) == short
