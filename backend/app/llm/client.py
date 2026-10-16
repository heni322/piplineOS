"""LLM port (ADR-0002/0006): validated structured output, retry-with-feedback (max 2), escalation,
token/cost accounting per tenant and lead, untrusted-content wrapping. Providers sit behind LLMBackend."""

from __future__ import annotations

import html
import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.adapters.base import RetryableProviderError
from app.core.resilience import CircuitBreaker
from app.llm.prompts import PromptFile

T = TypeVar("T", bound=BaseModel)
MAX_REPAIRS = 2


class LLMEscalation(Exception):
    """Output could not be obtained safely (invalid after retries, refusal, budget). A human must review."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


class LLMUnavailable(LLMEscalation, RetryableProviderError):
    """The model could not be reached (down, overloaded, rate-limited, or its circuit is open). Nothing is wrong
    with the work itself, so callers that can wait DEFER it (ADR-0019). It is still an LLMEscalation, so a caller
    that cannot wait (e.g. reply classification) falls back exactly as before: rules first, then a human."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("provider_unavailable", detail)


# One breaker per backend type, shared by every client in the process: when the model is down, the first few
# calls fail and the rest fail fast (no timeout each) until a trial call succeeds after the cooldown.
_BREAKERS: dict[str, CircuitBreaker] = {}


def breaker_for(backend: object) -> CircuitBreaker:
    name = type(backend).__name__
    if name not in _BREAKERS:
        _BREAKERS[name] = CircuitBreaker(f"llm:{name}", threshold=3, cooldown=120.0)
    return _BREAKERS[name]


@dataclass(frozen=True)
class UntrustedSource:
    id: str
    content: str
    url: str | None = None


def wrap_untrusted(sources: Sequence[UntrustedSource]) -> str:
    """Delimit untrusted text. Content is escaped so it cannot close the tag or forge new ones."""
    parts = []
    for s in sources:
        url = f' url="{html.escape(s.url, quote=True)}"' if s.url else ""
        parts.append(
            f'<untrusted_source id="{html.escape(s.id, quote=True)}"{url}>\n'
            f"{html.escape(s.content, quote=False)}\n</untrusted_source>"
        )
    return "\n".join(parts)


@dataclass(frozen=True)
class BackendResponse:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    model: str


class LLMBackend(Protocol):
    def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        json_schema: dict[str, Any],
        timeout: float,
    ) -> BackendResponse: ...


@dataclass(frozen=True)
class CallRecord:
    tenant_id: uuid.UUID
    lead_id: uuid.UUID | None
    agent: str
    prompt: PromptFile
    model: str
    attempt: int
    input_tokens: int
    output_tokens: int
    cost_micros_gbp: int | None
    latency_ms: int
    outcome: Literal["ok", "invalid_retry", "refusal", "timeout", "error", "escalated", "budget_exceeded"]
    validation_errors: list[dict[str, Any]] | None
    request_text: str
    response_text: str


Recorder = Callable[[CallRecord], None]
Pricer = Callable[[str, int, int], int | None]


def no_price(_model: str, _in: int, _out: int) -> int | None:
    return None


@dataclass
class LLMClient:
    backend: LLMBackend
    models: dict[str, str]  # tier -> model id
    recorder: Recorder
    pricer: Pricer = no_price
    timeout: float = 60.0
    sleep: Callable[[float], None] = field(default=time.sleep)

    def run_structured(
        self,
        *,
        prompt: PromptFile,
        schema: type[T],
        user_content: str,
        tenant_id: uuid.UUID,
        agent: str,
        lead_id: uuid.UUID | None = None,
        policy: str = "",
    ) -> T:
        model = self.models[prompt.model_tier]
        system = (policy + "\n\n" + prompt.body).strip()
        messages: list[dict[str, str]] = [{"role": "user", "content": user_content}]
        spent = 0
        breaker = breaker_for(self.backend)
        for attempt in range(1, MAX_REPAIRS + 2):
            started = time.monotonic()
            if not breaker.allow():
                raise LLMUnavailable("circuit open: the model failed repeatedly; retried after the cooldown")
            try:
                resp = self.backend.complete(
                    model=model,
                    system=system,
                    messages=messages,
                    max_tokens=prompt.max_tokens,
                    json_schema=schema.model_json_schema(),
                    timeout=self.timeout,
                )
                breaker.success()
            except TimeoutError as exc:
                breaker.failure()
                self._rec(
                    tenant_id,
                    lead_id,
                    agent,
                    prompt,
                    model,
                    attempt,
                    0,
                    0,
                    None,
                    started,
                    "timeout",
                    None,
                    user_content,
                    "",
                )
                raise LLMEscalation("timeout", str(exc)) from exc
            except AssertionError:  # a test double ran out of scripted answers: a bug, never an outage
                raise
            except Exception as exc:  # unreachable, 5xx, overloaded, rate-limited: the model, not the work
                breaker.failure()
                self._rec(
                    tenant_id,
                    lead_id,
                    agent,
                    prompt,
                    model,
                    attempt,
                    0,
                    0,
                    None,
                    started,
                    "error",
                    None,
                    user_content,
                    "",
                )
                raise LLMUnavailable(type(exc).__name__) from exc
            used_model = resp.model or model  # e.g. 'simulated' when the simulator answered
            cost = self.pricer(used_model, resp.input_tokens, resp.output_tokens)
            spent += cost or 0
            if resp.stop_reason == "refusal":
                self._rec(
                    tenant_id,
                    lead_id,
                    agent,
                    prompt,
                    used_model,
                    attempt,
                    resp.input_tokens,
                    resp.output_tokens,
                    cost,
                    started,
                    "refusal",
                    None,
                    user_content,
                    resp.text,
                )
                raise LLMEscalation("refusal", "model declined")
            try:
                parsed = schema.model_validate(json.loads(resp.text))
            except (json.JSONDecodeError, ValidationError) as exc:
                errors: list[dict[str, Any]] = (
                    [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
                    if isinstance(exc, ValidationError)
                    else [{"loc": [], "msg": str(exc), "type": "json"}]
                )
                last = attempt == MAX_REPAIRS + 1
                self._rec(
                    tenant_id,
                    lead_id,
                    agent,
                    prompt,
                    used_model,
                    attempt,
                    resp.input_tokens,
                    resp.output_tokens,
                    cost,
                    started,
                    "escalated" if last else "invalid_retry",
                    errors,
                    user_content,
                    resp.text,
                )
                if last:
                    raise LLMEscalation("invalid_output", json.dumps(errors)[:500]) from exc
                messages += [
                    {"role": "assistant", "content": resp.text},
                    {
                        "role": "user",
                        "content": "Your previous output was invalid. Fix these errors and "
                        "return ONLY valid JSON matching the schema:\n" + json.dumps(errors),
                    },
                ]
                self.sleep(min(2**attempt, 8) * 0.1)
                continue
            if prompt.budget_micros_gbp is not None and spent > prompt.budget_micros_gbp:
                self._rec(
                    tenant_id,
                    lead_id,
                    agent,
                    prompt,
                    used_model,
                    attempt,
                    resp.input_tokens,
                    resp.output_tokens,
                    cost,
                    started,
                    "budget_exceeded",
                    None,
                    user_content,
                    resp.text,
                )
                raise LLMEscalation("budget_exceeded", f"{spent} > {prompt.budget_micros_gbp}")
            self._rec(
                tenant_id,
                lead_id,
                agent,
                prompt,
                used_model,
                attempt,
                resp.input_tokens,
                resp.output_tokens,
                cost,
                started,
                "ok",
                None,
                user_content,
                resp.text,
            )
            return parsed
        raise LLMEscalation("invalid_output", "exhausted")  # pragma: no cover

    def _rec(
        self,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID | None,
        agent: str,
        prompt: PromptFile,
        model: str,
        attempt: int,
        tin: int,
        tout: int,
        cost: int | None,
        started: float,
        outcome: Literal["ok", "invalid_retry", "refusal", "timeout", "error", "escalated", "budget_exceeded"],
        errors: list[dict[str, Any]] | None,
        req: str,
        resp: str,
    ) -> None:
        self.recorder(
            CallRecord(
                tenant_id,
                lead_id,
                agent,
                prompt,
                model,
                attempt,
                tin,
                tout,
                cost,
                int((time.monotonic() - started) * 1000),
                outcome,
                errors,
                req,
                resp,
            )
        )
