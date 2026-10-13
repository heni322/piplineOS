from __future__ import annotations

from typing import Literal, Protocol

Verification = Literal["valid", "risky", "invalid", "unknown"]


class PersonObjected(Exception):
    """The provider says this person asked for their data not to be processed (Hunter: HTTP 451).
    The caller suppresses the address globally and never contacts it."""

    def __init__(self, email: str) -> None:
        super().__init__("person objected to processing at source")
        self.email = email


class EmailVerifier(Protocol):
    name: str

    def verify(self, email: str) -> Verification: ...
