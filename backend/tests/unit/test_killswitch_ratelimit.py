"""Deliverability kill-switch thresholds, bounce detection and the shared rate limiter (pure parts)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.adapters.inbound.base import InboundEmail
from app.config_paths import config_root
from app.core import ratelimit
from app.domain.deliverability.killswitch import Thresholds, assess, is_bounce

TH = Thresholds()


def test_thresholds_load_from_config() -> None:
    th = Thresholds.load(config_root())
    assert (th.window_days, th.min_sent, th.max_hard_bounce_rate, th.max_complaint_rate) == (7, 50, 0.03, 0.003)


@pytest.mark.parametrize(
    ("sent", "hard", "complaints", "pause", "warn", "reasons"),
    [
        (49, 40, 10, False, False, ()),  # too little volume to judge
        (100, 2, 0, False, False, ()),
        (100, 3, 0, True, False, ("HARD_BOUNCE_RATE",)),
        (1000, 0, 3, True, False, ("COMPLAINT_RATE",)),
        (1000, 0, 1, False, True, ()),  # early warning at 0.1%
        (1000, 40, 5, True, False, ("HARD_BOUNCE_RATE", "COMPLAINT_RATE")),
    ],
)
def test_assessment(sent: int, hard: int, complaints: int, pause: bool, warn: bool, reasons: tuple[str, ...]) -> None:
    a = assess(sent, hard, complaints, TH)
    assert (a.pause, a.warn, a.reasons) == (pause, warn, reasons)


def _msg(frm: str, subject: str) -> InboundEmail:
    return InboundEmail("<x@y>", frm, subject, "", datetime(2026, 10, 13, tzinfo=UTC))


def test_bounce_detection() -> None:
    assert is_bounce(_msg("MAILER-DAEMON@mx.contoso.example", "failure"))
    assert is_bounce(_msg("postmaster@contoso.example", "Hello"))
    assert is_bounce(_msg("someone@contoso.example", "Undeliverable: Service desk cover"))
    assert not is_bounce(_msg("alex@contoso.example", "Re: Service desk cover"))


def test_rate_limiter_falls_back_to_a_local_bucket_when_valkey_is_down() -> None:
    ratelimit.reset_local()
    results = [
        ratelimit.allow("unit:fallback", rate_per_sec=0.001, capacity=3, redis_url="redis://127.0.0.1:1/0")
        for _ in range(5)
    ]
    assert results == [True, True, True, False, False]  # degraded, but still limited
