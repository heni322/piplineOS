@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
set PYTHONIOENCODING=utf-8
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run ruff check . > ..\.sync\p34-lint.log 2>&1
uv run mypy >> ..\.sync\p34-lint.log 2>&1
uv run lint-imports >> ..\.sync\p34-lint.log 2>&1
uv run alembic upgrade head > ..\.sync\p34-migrate.log 2>&1
uv run pytest -q -p no:cacheprovider -W ignore --junitxml=..\.sync\p34-junit.xml > ..\.sync\p34-tests.log 2>&1
echo done > ..\.sync\p34-done.txt
