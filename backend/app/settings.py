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

    # Free-only: no paid provider is ever built, whatever the adapter modes say. Decision-makers come from the free
    # Companies House officers list, email addresses are never bought or verified, and the AI runs on the free local
    # model (LOCAL_LLM_URL) instead of the paid API.
    free_only: bool = False
    # Free-only: take the generic business address (sales@, info@...) the company publishes on its own website,
    # once a page of that site states the company's registered number. Off = register contacts stay without email.
    website_contacts: bool = True

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
    fr_registry_adapter_mode: AdapterMode = "mock"  # France: recherche-entreprises.api.gouv.fr (free, no key)
    no_registry_adapter_mode: AdapterMode = "mock"  # Norway: data.brreg.no Enhetsregisteret (free, no key)
    fi_registry_adapter_mode: AdapterMode = "mock"  # Finland: avoindata.prh.fi YTJ v3 (free, no key)
    se_registry_adapter_mode: AdapterMode = "mock"  # Sweden: Bolagsverket "värdefulla datamängder" (free, registration)
    dk_registry_adapter_mode: AdapterMode = "mock"  # Denmark: Erhvervsstyrelsen CVR distribution (free, credentials)
    be_registry_adapter_mode: AdapterMode = "mock"  # Belgium: local copy of KBO open data (owner imports the file)
    ie_registry_adapter_mode: AdapterMode = "mock"  # Ireland: local copy of CRO open data (owner imports the file)
    llm_adapter_mode: AdapterMode = "mock"

    # Real providers (Phase 6). Unused unless the matching adapter mode is sandbox/live.
    companies_house_api_key: SecretStr | None = None
    # Companies House streaming key (free, a separate "streaming" key in the developer hub): market-wide funding
    # signals (new share allotments and charges) for clients with SIC codes. Read every N minutes; 0 = off.
    companies_house_stream_key: SecretStr | None = None
    filing_stream_interval_minutes: int = Field(default=5, ge=0, le=1440)
    # Skip triggers from companies being wound up (the Gazette's corporate insolvency notices, free and open, plus
    # the register status). Real signal sources only.
    insolvency_filter: bool = True
    bolagsverket_client_id: str | None = None  # Sweden register (OAuth client credentials, free)
    bolagsverket_client_secret: SecretStr | None = None
    cvr_username: str | None = None  # Denmark register (system-to-system access, free, issued by Erhvervsstyrelsen)
    cvr_password: SecretStr | None = None
    hunter_api_key: SecretStr | None = None  # paid: used only when enrichment/verifier calls pass the three-key gate
    hunt_interval_hours: int = Field(default=6, ge=0, le=168)  # automatic trigger hunting; 0 = off
    hunt_lookback_days: int = Field(default=2, ge=1, le=30)
    msgraph_tenant_id: str | None = None
    msgraph_client_id: str | None = None
    msgraph_client_secret: SecretStr | None = None
    msgraph_webhook_secret: SecretStr | None = None  # derives a per-mailbox clientState for change notifications
    # Google Workspace: a service account with domain-wide delegation, authorised for the Gmail/Calendar scopes only
    google_service_account_json: SecretStr | None = None  # the key file's contents
    google_pubsub_topic: str | None = None  # projects/<project>/topics/<topic> that Gmail publishes new-mail events to
    google_push_audience: str | None = None  # the push subscription's OIDC audience (our webhook URL)
    google_push_service_account: str | None = None  # the service account Pub/Sub signs push requests as
    system_mail_from: str = "no-reply@pipelineos.example"  # a mailbox of system_mail_provider in live mode
    system_mail_provider: Literal["msgraph", "google"] = "msgraph"
    system_mail_name: str = "PipelineOS"
    sandbox_smtp_host: str = "mailpit"
    sandbox_smtp_port: int = 1025
    metrics_token: SecretStr | None = None  # bearer token for /metrics (Prometheus); unset = endpoint disabled

    anthropic_api_key: SecretStr | None = None
    llm_model_volume: str = "claude-sonnet-5-5"
    llm_model_reasoning: str = "claude-opus-5-5"
    llm_timeout_seconds: float = 60.0
    # LLM_ADAPTER_MODE=sandbox: a free local model through Ollama (this machine only), for testing
    local_llm_url: str = "http://127.0.0.1:11434"
    local_llm_model: str = "qwen2.5:7b"
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
