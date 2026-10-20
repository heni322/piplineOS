@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
set PYTHONIOENCODING=utf-8
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run ruff check . > ..\.sync\p23-be.log 2>&1
uv run ruff format --check . >> ..\.sync\p23-be.log 2>&1
uv run alembic upgrade head >> ..\.sync\p23-be.log 2>&1
uv run pytest -p no:cacheprovider -W ignore -q tests/security/test_company_searches.py tests/security/test_places.py tests/security/test_ops.py tests/security/test_import_contracts.py >> ..\.sync\p23-be.log 2>&1
echo done > ..\.sync\p23-be-done.txt
