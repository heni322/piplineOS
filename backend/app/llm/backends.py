"""LLM backends: Mock (scripted), Recorded (golden replay), Anthropic and Gemini (real, paid: only behind the three-key
switch) and Ollama (free, local)."""

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


GEMINI_URL = "https://generativelanguage.googleapis.com"
_GEMINI_DROP = {"title", "default", "additionalProperties", "$schema", "examples", "$defs", "definitions"}


def gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic's JSON schema -> the OpenAPI subset Gemini's `responseSchema` accepts: references inlined, `const`
    as a one-value enum, `X | None` as nullable, unsupported keywords dropped. The answer is still validated against
    the full Pydantic model afterwards, so anything lost here only loosens the hint, never the check."""
    defs = {**schema.get("$defs", {}), **schema.get("definitions", {})}

    def conv(node: Any, depth: int = 0) -> Any:
        if depth > 20:
            return {"type": "object"}
        if isinstance(node, list):
            return [conv(n, depth + 1) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return conv(defs.get(str(node["$ref"]).rsplit("/", 1)[-1], {"type": "object"}), depth + 1)
        variants = node.get("anyOf") or node.get("oneOf")
        if variants:
            real = [v for v in variants if v.get("type") != "null"]
            alt: dict[str, Any] = conv(real[0], depth + 1) if len(real) == 1 else {"anyOf": conv(real, depth + 1)}
            if len(real) < len(variants):
                alt["nullable"] = True
            if "description" in node:
                alt.setdefault("description", node["description"])
            return alt
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in _GEMINI_DROP or k in ("anyOf", "oneOf"):
                continue
            if k == "const":
                out["enum"] = [v]
            elif k == "properties":
                out[k] = {name: conv(sub, depth + 1) for name, sub in v.items()}
            elif k in ("items",):
                out[k] = conv(v, depth + 1)
            else:
                out[k] = v
        if "enum" in out and "type" not in out:
            out["type"] = "string"
        return out

    result: dict[str, Any] = conv({k: v for k, v in schema.items() if k not in ("$defs", "definitions")})
    return result


_GEMINI_BLOCKED = {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY", "OTHER"}


def _gemini_stop(data: dict[str, Any], cand: dict[str, Any]) -> str:
    reason = cand.get("finishReason")
    if (data.get("promptFeedback") or {}).get("blockReason") or reason in _GEMINI_BLOCKED or not data.get("candidates"):
        return "refusal"  # escalated to a person, like a refusal from any model
    return "max_tokens" if reason == "MAX_TOKENS" else "end_turn"


class GeminiBackend:
    """Google's Gemini API (generateContent, JSON output constrained by a schema). Paid: only selected in live mode
    behind the three-key switch, with LLM_PROVIDER=gemini. Use a key on the PAID tier: on the free tier Google uses
    prompts to improve its products, which is not acceptable for prospect data. The key goes in a header, never in
    the URL (so it never reaches access logs)."""

    def __init__(self, api_key: str, model: str, *, transport: Any = None, base_url: str = GEMINI_URL) -> None:
        import httpx

        if not api_key:
            raise ValueError("Gemini API key missing")
        self.model = model
        self._http = httpx.Client(
            base_url=base_url, transport=transport, follow_redirects=False, headers={"x-goog-api-key": api_key}
        )

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
        import httpx

        role = {"user": "user", "assistant": "model"}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": role.get(m["role"], "user"), "parts": [{"text": m["content"]}]} for m in messages],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
                "responseSchema": gemini_schema(json_schema),
            },
        }
        try:
            resp = self._http.post(f"/v1beta/models/{self.model}:generateContent", json=body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise TimeoutError("Gemini timed out") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            from app.adapters.base import RetryableProviderError

            raise RetryableProviderError(f"{resp.status_code} from Gemini")
        resp.raise_for_status()
        data = resp.json()
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(
            str(p.get("text", "")) for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought")
        )
        usage = data.get("usageMetadata") or {}
        return BackendResponse(
            text=text,
            input_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0),
            stop_reason=_gemini_stop(data, cand),
            model=self.model,
        )


LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "host.docker.internal", "ollama"})
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _private_only(host: str, resolve: Any = None) -> bool:
    """True only when every address the host resolves to is private or loopback (a container network)."""
    import ipaddress
    import socket

    try:
        infos = (resolve or socket.getaddrinfo)(host, 11434, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    addrs = {ipaddress.ip_address(i[4][0]) for i in infos}
    return bool(addrs) and all(a.is_private or a.is_loopback for a in addrs)


class OllamaBackend:
    """A free, open-weights model running on this machine through Ollama (https://ollama.com), for testing the agents
    with a real model at no cost. Only a local address is accepted, so prompts (synthetic test data only) never leave
    the machine. Output is constrained to the schema with Ollama's structured outputs (`format` = JSON schema).

    Costs are recorded as zero (model name `local:<model>`)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        transport: Any = None,
        min_timeout: float = 600.0,
        num_ctx: int = 8192,
        think: str | None = "false",
        keep_alive: str | None = "24h",
        resolve: Any = None,
    ) -> None:
        import httpx

        host = httpx.URL(base_url).host
        if host not in LOCAL_HOSTS:
            raise ValueError(f"local model URL must point at this machine (got host {host!r})")
        if host not in LOOPBACK and not _private_only(host, resolve):
            # "ollama" (the compose service) must resolve to the private Docker network, never the internet:
            # in production the prompts carry real prospect data
            raise ValueError(f"local model host {host!r} does not resolve to a private address only")
        self.model, self.min_timeout, self.num_ctx = model, min_timeout, num_ctx
        self.think, self.keep_alive = think, keep_alive
        self._http = httpx.Client(base_url=base_url, transport=transport, follow_redirects=False)

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
        import httpx

        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "format": json_schema,
            "stream": False,
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": self.num_ctx},
        }
        if self.think is not None:  # reasoning models (Qwen3.x, GLM-4.7) answer far faster without a thinking pass
            body["think"] = {"false": False, "true": True}.get(self.think, self.think)
        if self.keep_alive:  # keep the model in memory: reloading 20 GB on every call would be slow
            body["keep_alive"] = self.keep_alive
        try:
            resp = self._http.post("/api/chat", json=body, timeout=max(timeout, self.min_timeout))
        except httpx.TimeoutException as exc:
            raise TimeoutError("local model timed out") from exc
        resp.raise_for_status()
        data = resp.json()
        return BackendResponse(
            text=str((data.get("message") or {}).get("content") or ""),
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            stop_reason="max_tokens" if data.get("done_reason") == "length" else "end_turn",
            model=f"local:{self.model}",
        )
