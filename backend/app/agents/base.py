"""Shared context for agents. Agents only PRODUCE validated data; the orchestrator decides side effects."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from app.core.crypto import PiiCipher
from app.domain.tenants.config import TenantConfig
from app.llm.client import LLMClient
from app.settings import Settings


@dataclass
class AgentContext:
    settings: Settings
    engine: Engine
    cipher: PiiCipher
    llm: LLMClient
    tenant_id: uuid.UUID
    campaign_id: uuid.UUID
    config: TenantConfig
    config_id: uuid.UUID
