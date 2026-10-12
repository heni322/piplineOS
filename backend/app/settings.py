"""Runtime settings, loaded from environment variables (see ../.env.example).

Secrets are SecretStr so they never appear in reprs or logs. Business/legal rules are NOT here:
they live in versioned YAML under config/ (ADR-0007).
"""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AdapterMode = Literal["mock", "sandbox", "live"]
Environment = Literal["dev", "test", "staging", "prod"]

_KEY_FIELDS = ("pii_master_key", "blind_index_key", "suppression_hash_key", "unsubscribe_signing_key")


def decode_key(value: SecretStr) -> bytes:
    raw = base64.urlsafe_b64decode(value.get_secret_value() + "==")
    if len(raw) != 32:
        raise ValueError("key must decode to exactly 32 bytes")
    return raw


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Environment = "dev"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    database_url: str = "postgresql+psycopg://pipelineos_app:app_dev_only@localhost:5433/pipelineos"
    database_owner_url: str = "postgresql+psycopg://pipelineos_owner:owner_dev_only@localhost:5433/pipelineos"
    redis_url: str = "redis://localhost:6380/0"

    # 32-byte keys, urlsafe base64. Generate with: pipelineos keys generate
    pii_master_key: SecretStr
    blind_index_key: SecretStr
    suppression_hash_key: SecretStr
    unsubscribe_signing_key: SecretStr

    # Three-key live switch, key 3 (keys 1 and 2: adapter mode + tenant.live_mode). Default OFF.
    global_go_live: bool = False

    # Adapter modes (key 1). Default mock everywhere.
    email_adapter_mode: AdapterMode = "mock"
    calendar_adapter_mode: AdapterMode = "mock"
    crm_adapter_mode: AdapterMode = "mock"
    enrichment_adapter_mode: AdapterMode = "mock"
    verifier_adapter_mode: AdapterMode = "mock"
    signals_adapter_mode: AdapterMode = "mock"
    companies_house_adapter_mode: AdapterMode = "mock"
    llm_adapter_mode: AdapterMode = "mock"

    anthropic_api_key: SecretStr | None = None
    llm_model_volume: str = "claude-sonnet-5-5"
    llm_model_reasoning: str = "claude-opus-5-5"
    llm_timeout_seconds: float = 60.0
    llm_log_bodies: bool = True

    session_idle_hours_tenant: int = 12
    session_absolute_hours_operator: int = 8
    cookie_secure: bool = False

    send_decision_max_age_minutes: int = Field(default=15, ge=1, le=60)
    sending_reconcile_after_minutes: int = 10

    @field_validator(*_KEY_FIELDS)
    @classmethod
    def _key_is_32_bytes(cls, v: SecretStr) -> SecretStr:
        decode_key(v)
        return v

    @model_validator(mode="after")
    def _prod_guards(self) -> Settings:
        keys = [getattr(self, f).get_secret_value() for f in _KEY_FIELDS]
        if len(set(keys)) != len(keys):
            raise ValueError("each cryptographic key must be different")
        if self.environment in ("staging", "prod"):
            if not self.cookie_secure:
                raise ValueError("cookie_secure must be true outside dev/test")
            if "dev_only" in self.database_url:
                raise ValueError("development database credentials are not allowed outside dev/test")
        if self.environment != "prod" and self.global_go_live:
            raise ValueError("GLOBAL_GO_LIVE can only be enabled in the prod environment")
        return self

    def key(self, name: str) -> bytes:
        if name not in _KEY_FIELDS:
            raise KeyError(name)
        return decode_key(getattr(self, name))


@lru_cache
def get_settings() -> Settings:
    return Settings()
