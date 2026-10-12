"""Chooses the LLM backend. Paid model calls follow the same three-key rule as sending (brief: DRY-RUN everywhere):
adapter mode live + tenant live + GLOBAL_GO_LIVE with the go-live checklist signed. Otherwise: the simulator."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import Engine

from app.core.crypto import PiiCipher
from app.domain.outbound.modes import resolve_mode
from app.llm.backends import AnthropicBackend
from app.llm.client import LLMBackend, LLMClient
from app.llm.pricing import load_pricer
from app.llm.recorder import db_recorder
from app.llm.simulator import SimulatedBackend
from app.settings import Settings

PRICES = Path(__file__).resolve().parents[3] / "config" / "llm_prices.yaml"


def select_backend(settings: Settings, *, tenant_live: bool, golive_complete: bool) -> LLMBackend:
    mode = resolve_mode(
        adapter_mode=settings.llm_adapter_mode,
        tenant_live=tenant_live,
        global_go_live=settings.global_go_live,
        golive_complete=golive_complete,
    )
    if mode == "live" and settings.anthropic_api_key is not None:
        return AnthropicBackend(settings.anthropic_api_key.get_secret_value())
    return SimulatedBackend()


def build_llm_client(
    settings: Settings,
    engine: Engine,
    cipher: PiiCipher,
    *,
    tenant_live: bool,
    golive_complete: bool,
    backend: LLMBackend | None = None,
) -> LLMClient:
    return LLMClient(
        backend=backend or select_backend(settings, tenant_live=tenant_live, golive_complete=golive_complete),
        models={"volume": settings.llm_model_volume, "reasoning": settings.llm_model_reasoning},
        recorder=db_recorder(engine, cipher, settings.llm_log_bodies),
        pricer=load_pricer(PRICES),
        timeout=settings.llm_timeout_seconds,
    )
