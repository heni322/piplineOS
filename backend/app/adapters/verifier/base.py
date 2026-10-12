from __future__ import annotations

from typing import Literal, Protocol

Verification = Literal["valid", "risky", "invalid", "unknown"]


class EmailVerifier(Protocol):
    name: str

    def verify(self, email: str) -> Verification: ...
