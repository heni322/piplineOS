from __future__ import annotations

from app.adapters.verifier.base import Verification


class NoVerifier:
    """Free-only mode: no paid verification service, so every address is reported "unknown" (never guessed valid).
    Addresses then come only from the client or from a reply, never from guessing."""

    name = "verifier.none"

    def verify(self, email: str) -> Verification:
        return "unknown"
