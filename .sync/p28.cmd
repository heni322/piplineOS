@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
set PYTHONIOENCODING=utf-8
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run ruff check --fix . > ..\.sync\p28.log 2>&1
uv run ruff format . >> ..\.sync\p28.log 2>&1
uv run mypy >> ..\.sync\p28.log 2>&1
uv run lint-imports 2>&1 | findstr /C:"Contracts:" >> ..\.sync\p28.log
uv run pytest -p no:cacheprovider -W ignore -q tests/integration/test_source_quality.py tests/integration/test_subject_ab.py tests/security/test_billing_api.py tests/contract/test_boamp.py tests/contract/test_ted.py >> ..\.sync\p28.log 2>&1
cd ..\frontend
call pnpm typecheck >> ..\.sync\p28.log 2>&1
call pnpm lint >> ..\.sync\p28.log 2>&1
call pnpm test >> ..\.sync\p28.log 2>&1
echo done > ..\.sync\p28-done.txt
