"""Deterministic compliance footer (Agent 3 never writes it). Validated before anything can be sent."""

from __future__ import annotations

import string
from dataclasses import dataclass
from pathlib import Path

import yaml

UNSUB_PLACEHOLDER = "{unsubscribe_url}"


class FooterError(Exception):
    pass


@dataclass(frozen=True)
class FooterTemplate:
    locale: str
    template: str
    required_placeholders: tuple[str, ...]

    @staticmethod
    def load(path: Path) -> FooterTemplate:
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
        return FooterTemplate(d["locale"], d["template"], tuple(d["required_placeholders"]))

    def render(self, values: dict[str, str]) -> str:
        """Render everything except {unsubscribe_url}, which is filled per message at send time."""
        names = {f for _, f, _, _ in string.Formatter().parse(self.template) if f}
        missing = [p for p in self.required_placeholders if p != "unsubscribe_url" and not values.get(p, "").strip()]
        if missing:
            raise FooterError(f"missing required footer values: {sorted(missing)}")
        unknown = names - set(values) - {"unsubscribe_url"}
        if unknown:
            raise FooterError(f"no value for footer placeholders: {sorted(unknown)}")
        return self.template.format(**{**values, "unsubscribe_url": UNSUB_PLACEHOLDER})


def finalise_body(body: str, footer: str, unsubscribe_url: str, privacy_url: str) -> str:
    if UNSUB_PLACEHOLDER not in footer:
        raise FooterError("footer has no unsubscribe placeholder")
    final = f"{body.rstrip()}\n\n{footer.replace(UNSUB_PLACEHOLDER, unsubscribe_url)}"
    if unsubscribe_url not in final or privacy_url not in final:
        raise FooterError("final message lacks unsubscribe or privacy link")
    return final
