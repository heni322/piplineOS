from __future__ import annotations

from app.adapters.verifier.base import Verification


class MockVerifier:
    """Deterministic: local part starting 'risky' -> risky, 'invalid'/'bounce' -> invalid, else valid."""

    name = "verifier.mock"

    def verify(self, email: str) -> Verification:
        local = email.split("@", 1)[0].lower()
        if local.startswith(("invalid", "bounce")):
            return "invalid"
        if local.startswith("risky"):
            return "risky"
        return "valid"
