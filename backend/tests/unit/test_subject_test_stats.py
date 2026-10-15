"""The A/B significance test: matches a known textbook value and never claims a winner on tiny or equal samples."""

from __future__ import annotations

import pytest

from app.domain.reporting.metrics import two_proportion_p


def test_known_value() -> None:
    # 40/200 vs 20/200, pooled: p = 0.15, SE = sqrt(0.15 * 0.85 * (2/200)) = 0.0357, z = 2.80, two-sided p = 0.0051
    assert two_proportion_p(40, 200, 20, 200) == pytest.approx(0.0051, abs=0.0002)


def test_equal_rates_and_empty_groups() -> None:
    assert two_proportion_p(10, 100, 10, 100) == 1.0
    assert two_proportion_p(0, 100, 0, 100) == 1.0
    assert two_proportion_p(1, 0, 1, 10) is None
