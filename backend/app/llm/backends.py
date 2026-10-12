"""LLM backends: Mock (scripted), Recorded (golden replay) and Anthropic (real; never used without a key)."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

from app.llm.client import BackendResponse


class MockBackend:
    """Returns scripted responses in order. Each item: str (text) or BackendResponse."""

    def __init__(self, responses: list[str | BackendResponse]) -> None:
        self.queue: deque[str | BackendResponse] = deque(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        json_schema: dict[str, Any],
        timeout: float,
    ) -> BackendResponse:
        self.calls.append({"model": model, "system": system, "messages": list(messages)})
        if not self.queue:
            raise AssertionError("MockBackend: no scripted response left")
        item = self.queue.popleft()
        if isinstance(item, BackendResponse):
            return item
        return BackendResponse(text=item, input_tokens=100, output_tokens=50, stop_reason="end_turn", model=model)


class RecordedBackend:
    """Replays responses keyed by a test-case id from a JSONL file of {"case": ..., "response": ...}."""

    def __init__(self, path: Path, case: str) -> None:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.responses = deque(r["response"] for r in rows if r["case"] == case)

    def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        json_schema: dict[str, Any],
        timeout: float,
    ) -> BackendResponse:
        return BackendResponse(
            text=self.responses.popleft(), input_tokens=0, output_tokens=0, stop_reason="end_turn", model=model
        )


class AnthropicBackend:
    """Real Anthropic Messages API, forcing a single tool call whose input schema is the output schema."""

    def __init__(self, api_key: str, max_retries: int = 2) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, max_retries=max_retries)

    def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        json_schema: dict[str, Any],
        timeout: float,
    ) -> BackendResponse:
        resp = self._client.messages.create(  # type: ignore[call-overload]
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            timeout=timeout,
            tools=[{"name": "emit", "description": "Return the result.", "input_schema": json_schema}],
            tool_choice={"type": "tool", "name": "emit"},
        )
        text = next((json.dumps(b.input) for b in resp.content if getattr(b, "type", "") == "tool_use"), "")
        return BackendResponse(
            text=text,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            stop_reason=str(resp.stop_reason),
            model=model,
        )
