from __future__ import annotations

import base64
import json
import secrets

from app.demo.dataset import DEMO_CONFIG
from app.domain.tenants.config import TenantConfig
from app.llm.backends import AnthropicBackend
from app.llm.client import UntrustedSource, wrap_untrusted
from app.llm.factory import select_backend
from app.llm.simulator import SimulatedBackend
from app.settings import Settings


def _score(text: str, st: str = "job_posting") -> int:
    data = {"icp": DEMO_CONFIG.icp.model_dump(), "signal": {"signal_type": st}}
    user = f"INPUT_JSON: {json.dumps(data)}\n" + wrap_untrusted([UntrustedSource("s1", text)])
    out = SimulatedBackend().complete(
        model="m",
        system="",
        messages=[{"role": "user", "content": user}],
        max_tokens=1,
        json_schema={"title": "TriggerScore"},
        timeout=1,
    )
    assert out.model == "simulated"
    return int(json.loads(out.text)["relevance_score"])


def test_simulator_scores_icp_and_non_icp() -> None:
    assert _score("A managed IT provider is hiring a Head of Service Delivery for its service desk.") >= 60
    assert _score("Artisan bakery hiring an experienced pastry chef.") < 60


def test_simulator_is_deterministic() -> None:
    t = "A managed service provider won a service desk contract."
    assert _score(t, "tender") == _score(t, "tender")


def _keys() -> dict[str, str]:
    return {
        n: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        for n in ("pii_master_key", "blind_index_key", "suppression_hash_key", "unsubscribe_signing_key")
    }


def test_paid_llm_requires_all_three_keys() -> None:
    dev = Settings(**_keys(), llm_adapter_mode="live", anthropic_api_key="sk-test")
    assert isinstance(
        select_backend(dev, tenant_live=True, golive_complete=True), SimulatedBackend
    )  # GLOBAL_GO_LIVE off
    prod = Settings(
        **_keys(),
        environment="prod",
        cookie_secure=True,
        global_go_live=True,
        llm_adapter_mode="live",
        anthropic_api_key="sk-test",
        database_url="postgresql+psycopg://u:p@h/db",
    )
    assert isinstance(select_backend(prod, tenant_live=False, golive_complete=True), SimulatedBackend)
    assert isinstance(select_backend(prod, tenant_live=True, golive_complete=False), SimulatedBackend)
    assert isinstance(select_backend(prod, tenant_live=True, golive_complete=True), AnthropicBackend)
    no_key = prod.model_copy(update={"anthropic_api_key": None})
    assert isinstance(select_backend(no_key, tenant_live=True, golive_complete=True), SimulatedBackend)


def test_demo_config_is_valid() -> None:
    assert TenantConfig.model_validate(DEMO_CONFIG.model_dump()).signal_threshold == 60
