"""Test harness. DB tests run against a real PostgreSQL 16 (+pgvector): a fresh database per test session,
migrated with Alembic as the OWNER role, exercised as the APP role (no BYPASSRLS) — exactly as in production.

Configure with TEST_PG_ADMIN_URL (superuser) — default postgresql://postgres@127.0.0.1:5433/postgres.
"""

from __future__ import annotations

import base64
import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
CONFIG = BACKEND.parent / "config"
ADMIN_URL = os.environ.get("TEST_PG_ADMIN_URL", "postgresql://postgres@127.0.0.1:5433/postgres")
TEST_DB = os.environ.get("TEST_PG_DB", "pipelineos_test")
_host = ADMIN_URL.split("@", 1)[1].rsplit("/", 1)[0]
APP_URL = f"postgresql+psycopg://pipelineos_app:app_dev_only@{_host}/{TEST_DB}"
OWNER_URL = f"postgresql+psycopg://pipelineos_owner:owner_dev_only@{_host}/{TEST_DB}"


def _key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


# Settings must exist before any app module that reads them is imported.
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": APP_URL,
        "DATABASE_OWNER_URL": OWNER_URL,
        "PII_MASTER_KEY": _key(),
        "BLIND_INDEX_KEY": _key(),
        "SUPPRESSION_HASH_KEY": _key(),
        "UNSUBSCRIBE_SIGNING_KEY": _key(),
        "GLOBAL_GO_LIVE": "false",
        # tests never use a real provider or a local model, whatever the developer's shell has loaded
        **{
            f"{name}_ADAPTER_MODE": "mock"
            for name in (
                "EMAIL",
                "CALENDAR",
                "CRM",
                "ENRICHMENT",
                "VERIFIER",
                "SIGNALS",
                "COMPANIES_HOUSE",
                "LLM",
                "FR_REGISTRY",
                "NO_REGISTRY",
                "FI_REGISTRY",
                "SE_REGISTRY",
                "DK_REGISTRY",
                "BE_REGISTRY",
                "IE_REGISTRY",
            )
        },
        "LOG_LEVEL": "WARNING",
    }
)


def _pg_available() -> bool:
    try:
        import psycopg

        with psycopg.connect(ADMIN_URL, connect_timeout=3):
            return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def database() -> Iterator[str]:
    if not _pg_available():
        pytest.skip(f"PostgreSQL not reachable at {ADMIN_URL}")
    import psycopg

    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute("""DO $$BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='pipelineos_owner') THEN
              CREATE ROLE pipelineos_owner LOGIN PASSWORD 'owner_dev_only'; END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='pipelineos_app') THEN
              CREATE ROLE pipelineos_app LOGIN PASSWORD 'app_dev_only' NOBYPASSRLS; END IF; END$$""")
        c.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{TEST_DB}" OWNER pipelineos_owner')
    with psycopg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}", autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"url={OWNER_URL}", "upgrade", "head"],
        cwd=BACKEND,
        check=True,
        capture_output=True,
    )
    yield TEST_DB


@pytest.fixture(scope="session")
def engine(database: str):  # type: ignore[no-untyped-def]
    from app.db.session import get_engine

    eng = get_engine(APP_URL)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def owner_engine(database: str):  # type: ignore[no-untyped-def]
    from sqlalchemy import create_engine

    eng = create_engine(OWNER_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def clean_db(owner_engine, engine):  # type: ignore[no-untyped-def]
    """Empty every table, then load the versioned config (countries disabled, nothing verified)."""
    from sqlalchemy import text

    from app.config_sync import sync_all
    from app.db.session import DbContext, transaction

    with owner_engine.begin() as c:
        tables = (
            c.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename NOT IN ('alembic_version')"
                )
            )
            .scalars()
            .all()
        )
        c.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))
    with transaction(DbContext.system(), engine) as conn:
        sync_all(conn, CONFIG)
    return engine


@pytest.fixture(scope="session")
def settings():  # type: ignore[no-untyped-def]
    from app.settings import get_settings

    return get_settings()


@pytest.fixture
def cipher(settings):  # type: ignore[no-untyped-def]
    from app.core.crypto import PiiCipher

    return PiiCipher(settings.key("pii_master_key"))
