"""PII masking. Every API serializer masks by default; unmasking requires a logged grant."""

from __future__ import annotations


def _mask_token(token: str) -> str:
    return token[:1] + "***" if token else ""


def mask_name(name: str | None) -> str | None:
    if not name:
        return name
    return " ".join(_mask_token(part) for part in name.split())


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return "***" if email else email
    local, domain = email.rsplit("@", 1)
    labels = domain.split(".")
    # keep the public suffix (last label, or last two for e.g. co.uk / org.uk)
    keep = 2 if len(labels) >= 3 and len(labels[-2]) <= 3 else 1
    head = ".".join(_mask_token(lbl) for lbl in labels[:-keep])
    tail = ".".join(labels[-keep:])
    return f"{_mask_token(local)}@{head}.{tail}" if head else f"{_mask_token(local)}@{tail}"


def mask_url(url: str | None) -> str | None:
    if not url:
        return url
    scheme, _, rest = url.partition("://")
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}/***" if rest else "***"
