"""Structured JSON logging with correlation IDs and a PII/secret scrubber.

Rule: emails, names, tokens and secrets never reach logs in clear text.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from contextvars import ContextVar
from typing import Any, TextIO

import structlog

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BEARER_RE = re.compile(r"(?i)\b(bearer|token|api[_-]?key|sk-ant-[a-z0-9\-]*)[=: ]+\S+")
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "authorization",
        "cookie",
        "set-cookie",
        "email",
        "name",
        "first_name",
        "last_name",
        "person_name",
        "full_name",
        "body",
        "subject",
        "mfa_secret",
        "totp",
        "session",
        "session_id",
        "csrf",
        "raw",
        "phone",
    }
)


def scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        value = EMAIL_RE.sub("[EMAIL]", value)
        return BEARER_RE.sub(r"\1=[REDACTED]", value)
    if isinstance(value, Mapping):
        return {k: ("[REDACTED]" if str(k).lower() in SENSITIVE_KEYS else scrub_value(v)) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return (
            type(value)(scrub_value(v) for v in value)
            if not isinstance(value, set)
            else [scrub_value(v) for v in value]
        )
    return value


def scrub_processor(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = "[REDACTED]"
        else:
            event_dict[key] = scrub_value(event_dict[key])
    return event_dict


def add_correlation_id(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    cid = correlation_id_var.get()
    if cid and "correlation_id" not in event_dict:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    out = stream or sys.stdout
    logging.basicConfig(format="%(message)s", stream=out, level=level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            scrub_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=out),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "pipelineos") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
