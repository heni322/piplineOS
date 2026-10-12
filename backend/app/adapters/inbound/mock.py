from __future__ import annotations

from app.adapters.inbound.base import InboundEmail


class MockInbox:
    """Queue of synthetic inbound emails (demo and tests). Each message is returned once."""

    name = "inbox.mock"

    def __init__(self, messages: list[InboundEmail] | None = None) -> None:
        self.queue = list(messages or [])

    def push(self, msg: InboundEmail) -> None:
        self.queue.append(msg)

    def fetch_new(self) -> list[InboundEmail]:
        out, self.queue = self.queue, []
        return out
