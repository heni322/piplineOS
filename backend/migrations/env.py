"""Alembic environment. Migrations run as the OWNER role (DATABASE_OWNER_URL), never as the app role."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

url = context.get_x_argument(as_dictionary=True).get("url") or os.environ.get("DATABASE_OWNER_URL")
if not url:
    from app.settings import get_settings

    url = get_settings().database_owner_url

engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
