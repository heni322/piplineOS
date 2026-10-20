"""The free local model backend (Ollama): only this machine's address is accepted, answers are constrained to the
schema, costs are zero, and LLM_ADAPTER_MODE=sandbox selects it whatever the live keys say."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.llm.backends import OllamaBackend
from app.llm.factory import PRICES, select_backend
from app.llm.pricing import load_pricer
from app.llm.simulator import SimulatedBackend

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def test_request_shape_and_response_mapping() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": '{"ok": true}'},
                "prompt_eval_count": 812,
                "eval_count": 9,
                "done_reason": "stop",
            },
        )

    b = OllamaBackend("http://127.0.0.1:11434", "qwen2.5:7b", transport=httpx.MockTransport(handler))
    out = b.complete(
        model="claude-sonnet-5-5",
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=300,
        json_schema=SCHEMA,
        timeout=60,
    )
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/api/chat"
    assert body["model"] == "qwen2.5:7b" and body["stream"] is False and body["format"] == SCHEMA
    assert body["messages"][0] == {"role": "system", "content": "SYS"} and body["messages"][1]["content"] == "hi"
    assert body["options"]["temperature"] == 0 and body["options"]["num_predict"] == 300
    assert (out.text, out.input_tokens, out.output_tokens, out.stop_reason, out.model) == (
        '{"ok": true}',
        812,
        9,
        "end_turn",
        "local:qwen2.5:7b",
    )


def test_truncation_and_timeout_are_reported() -> None:
    cut = OllamaBackend(
        "http://localhost:11434",
        "m",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"message": {"content": "{"}, "done_reason": "length"})
        ),
    )
    assert (
        cut.complete(model="x", system="", messages=[], max_tokens=5, json_schema={}, timeout=1).stop_reason
        == "max_tokens"
    )

    def slow(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(TimeoutError):
        OllamaBackend("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(slow)).complete(
            model="x", system="", messages=[], max_tokens=5, json_schema={}, timeout=1
        )


@pytest.mark.parametrize("url", ["https://api.example.com", "http://10.0.0.5:11434", "http://ollama.evil.example"])
def test_only_this_machine_is_accepted(url: str) -> None:
    with pytest.raises(ValueError, match="this machine"):
        OllamaBackend(url, "m")


def test_local_calls_cost_nothing_and_sandbox_selects_the_local_model(settings) -> None:  # type: ignore[no-untyped-def]
    assert load_pricer(Path(PRICES))("local:qwen2.5:7b", 10_000, 10_000) == 0
    s = settings.model_copy(update={"llm_adapter_mode": "sandbox"})
    assert isinstance(select_backend(s, tenant_live=False, golive_complete=False), OllamaBackend)
    assert isinstance(select_backend(settings, tenant_live=True, golive_complete=True), SimulatedBackend)
    live_no_keys = settings.model_copy(update={"llm_adapter_mode": "live"})
    assert isinstance(select_backend(live_no_keys, tenant_live=False, golive_complete=False), SimulatedBackend)


def _fake_dns(*addrs: str):  # type: ignore[no-untyped-def]
    return lambda host, port, proto=0: [(2, 1, 6, "", (a, port)) for a in addrs]


def test_the_compose_service_name_must_resolve_to_a_private_network_only() -> None:
    OllamaBackend("http://ollama:11434", "m", resolve=_fake_dns("172.18.0.4"))  # Docker network: fine
    with pytest.raises(ValueError, match="private address"):  # a public answer for "ollama": refused
        OllamaBackend("http://ollama:11434", "m", resolve=_fake_dns("172.18.0.4", "93.184.216.34"))

    def no_dns(*_a, **_k):  # type: ignore[no-untyped-def]
        raise OSError("no such host")

    with pytest.raises(ValueError, match="private address"):  # unresolvable: fail closed
        OllamaBackend("http://host.docker.internal:11434", "m", resolve=no_dns)


def test_thinking_off_and_keep_alive_are_sent() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": '{"ok": true}'}, "done_reason": "stop"})

    def call(**kw):  # type: ignore[no-untyped-def]
        OllamaBackend("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(handler), **kw).complete(
            model="x", system="", messages=[], max_tokens=5, json_schema=SCHEMA, timeout=1
        )
        return seen[-1]

    body = call()
    assert body["think"] is False and body["keep_alive"] == "24h" and body["options"]["num_ctx"] == 8192
    assert call(think="high", keep_alive="1h", num_ctx=16384)["think"] == "high"
    assert seen[-1]["keep_alive"] == "1h" and seen[-1]["options"]["num_ctx"] == 16384
    unset = call(think=None, keep_alive=None)
    assert "think" not in unset and "keep_alive" not in unset


def test_settings_reach_the_backend(settings) -> None:  # type: ignore[no-untyped-def]
    from app.llm.factory import local_backend

    s = settings.model_copy(update={"local_llm_think": "low", "local_llm_num_ctx": 4096, "local_llm_model": "a"})
    b = local_backend(s, "glm-4.7-flash")
    assert (b.model, b.think, b.num_ctx, b.keep_alive) == ("glm-4.7-flash", "low", 4096, "24h")


def test_gemini_request_shape_schema_and_header_key() -> None:
    from pydantic import BaseModel

    from app.llm.backends import GeminiBackend, gemini_schema

    class Inner(BaseModel):
        kind: str

    class Out(BaseModel):
        label: str
        note: str | None = None
        items: list[Inner]

    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "x", "thought": True}, {"text": '{"label":"a","items":[]}'}]},
                     "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 7, "thoughtsTokenCount": 3},
            },
        )  # fmt: skip

    b = GeminiBackend("g-key", "gemini-3.8-flash", transport=httpx.MockTransport(handler))
    out = b.complete(
        model="ignored",
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=200,
        json_schema=Out.model_json_schema(),
        timeout=30,
    )
    req = seen[0]
    assert req.url.path == "/v1beta/models/gemini-3.8-flash:generateContent" and "key" not in req.url.params
    assert req.headers["x-goog-api-key"] == "g-key"
    body = json.loads(req.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "SYS" and body["contents"][0]["role"] == "user"
    cfg = body["generationConfig"]
    assert cfg["temperature"] == 0 and cfg["responseMimeType"] == "application/json"
    schema = cfg["responseSchema"]
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema) and "title" not in schema
    assert schema["properties"]["note"]["nullable"] is True
    assert schema["properties"]["items"]["items"]["properties"]["kind"]["type"] == "string"
    assert (out.text, out.input_tokens, out.output_tokens, out.model) == (
        '{"label":"a","items":[]}', 50, 10, "gemini-3.8-flash",
    )  # fmt: skip
    assert gemini_schema({"const": "x"}) == {"enum": ["x"], "type": "string"}


def test_gemini_only_with_all_three_keys_and_never_under_free_only() -> None:
    import base64
    import secrets

    from app.llm.backends import GeminiBackend
    from app.settings import Settings

    keys = {
        n: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        for n in ("pii_master_key", "blind_index_key", "suppression_hash_key", "unsubscribe_signing_key")
    }
    prod = Settings(
        **keys,
        environment="prod",
        cookie_secure=True,
        global_go_live=True,
        llm_adapter_mode="live",
        llm_provider="gemini",
        gemini_api_key="g-key",
        database_url="postgresql+psycopg://u:p@h/db",
    )
    assert isinstance(select_backend(prod, tenant_live=True, golive_complete=True), GeminiBackend)
    assert isinstance(select_backend(prod, tenant_live=False, golive_complete=True), SimulatedBackend)
    assert isinstance(select_backend(prod, tenant_live=True, golive_complete=False), SimulatedBackend)
    free = prod.model_copy(update={"free_only": True})
    assert isinstance(select_backend(free, tenant_live=True, golive_complete=True), OllamaBackend)
    local = prod.model_copy(update={"llm_provider": "local"})
    assert isinstance(select_backend(local, tenant_live=True, golive_complete=True), OllamaBackend)
    from app.adapters.base import AdapterNotConfigured

    with pytest.raises(AdapterNotConfigured):
        select_backend(prod.model_copy(update={"gemini_api_key": None}), tenant_live=True, golive_complete=True)


def test_a_gemini_block_is_a_refusal() -> None:
    from app.llm.backends import GeminiBackend

    blocked = httpx.MockTransport(lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    out = GeminiBackend("k", "m", transport=blocked).complete(
        model="x", system="", messages=[], max_tokens=5, json_schema={}, timeout=1
    )
    assert out.stop_reason == "refusal"
