from __future__ import annotations

import base64
import json
import os
import secrets

import pytest
from pydantic import ValidationError

from app.core.crypto import blind_index, normalise_domain, normalise_email, suppression_hash
from app.core.logging import configure_logging, get_logger, scrub_value
from app.core.masking import mask_email, mask_name, mask_url
from app.settings import Settings


def test_logs_never_contain_emails_or_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    log = get_logger("t")
    log.info(
        "event",
        email="alex@contoso.example",
        note="contact alex@contoso.example now",
        api_key="sk-ant-secret",
        nested={"password": "hunter2", "ok": "fine"},
        name="Alex Morgan",
    )
    out = capsys.readouterr().out
    record = json.loads(out.strip().splitlines()[-1])
    assert "alex@contoso.example" not in out
    assert "hunter2" not in out and "sk-ant-secret" not in out and "Alex Morgan" not in out
    assert record["note"] == "contact [EMAIL] now"
    assert record["nested"] == {"password": "[REDACTED]", "ok": "fine"}


def test_scrub_handles_bearer_tokens() -> None:
    assert "abc123" not in scrub_value("Authorization: Bearer abc123")


def test_masking() -> None:
    assert mask_name("Alex Morgan") == "A*** M***"
    assert mask_email("alex.morgan@contoso-it.co.uk") == "a***@c***.co.uk"
    assert mask_email("jo@acme.example") == "j***@a***.example"
    assert mask_url("https://www.linkedin.com/in/someone") == "https://www.linkedin.com/***"
    assert mask_name(None) is None


def test_normalisation_and_hashes_are_stable() -> None:
    assert normalise_email("  Alex.Morgan@Contoso.EXAMPLE ") == "alex.morgan@contoso.example"
    assert normalise_domain("WWW.Contoso.Example.") == "contoso.example"
    k = b"k" * 32
    assert suppression_hash(k, "A@B.example", "email") == suppression_hash(k, "a@b.example ", "email")
    assert suppression_hash(k, "b.example", "domain") != suppression_hash(k, "b.example", "email")
    import uuid

    t1, t2 = uuid.uuid4(), uuid.uuid4()
    # blind indexes are tenant-salted: the same person cannot be correlated across tenants by index
    assert blind_index(k, "a@b.example", t1) != blind_index(k, "a@b.example", t2)


def _keys() -> dict[str, str]:
    return {
        n: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        for n in ("pii_master_key", "blind_index_key", "suppression_hash_key", "unsubscribe_signing_key")
    }


def test_settings_reject_short_keys() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**_keys(), "pii_master_key": "c2hvcnQ="})


def test_settings_reject_reused_keys() -> None:
    k = _keys()
    with pytest.raises(ValidationError):
        Settings(**{**k, "blind_index_key": k["pii_master_key"]})


def test_global_go_live_is_impossible_outside_prod() -> None:
    for env in ("dev", "test", "staging"):
        with pytest.raises(ValidationError):
            Settings(
                **_keys(),
                environment=env,
                global_go_live=True,
                cookie_secure=True,
                database_url="postgresql+psycopg://u:p@h/db",
            )


def test_prod_requires_secure_cookies_and_real_credentials() -> None:
    with pytest.raises(ValidationError):
        Settings(**_keys(), environment="prod", cookie_secure=False, database_url="postgresql+psycopg://u:p@h/db")
    with pytest.raises(ValidationError):
        Settings(**_keys(), environment="prod", cookie_secure=True)  # default dev_only credentials


def test_defaults_are_safe() -> None:
    s = Settings(**_keys())
    assert s.global_go_live is False
    assert {s.email_adapter_mode, s.calendar_adapter_mode, s.crm_adapter_mode, s.llm_adapter_mode} == {"mock"}
    assert os.environ.get("GLOBAL_GO_LIVE") == "false"


def test_staging_can_never_reach_real_people() -> None:
    base = {**_keys(), "environment": "staging", "cookie_secure": True, "database_url": "postgresql+psycopg://u:p@h/db"}
    for k in ("email", "calendar", "crm"):
        with pytest.raises(ValueError, match=f"staging may not use live {k}"):
            Settings(**base, **{f"{k}_adapter_mode": "live"})
    assert Settings(**base, email_adapter_mode="sandbox").environment == "staging"
