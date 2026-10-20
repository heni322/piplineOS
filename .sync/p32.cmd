@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
set PYTHONIOENCODING=utf-8
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run ruff check --fix . > ..\.sync\p32.log 2>&1
uv run ruff format . >> ..\.sync\p32.log 2>&1
uv run ruff check . >> ..\.sync\p32.log 2>&1
uv run mypy >> ..\.sync\p32.log 2>&1
uv run lint-imports 2>&1 | findstr /C:"Contracts:" /C:"BROKEN" >> ..\.sync\p32.log
uv run pytest -p no:cacheprovider -W ignore -q tests/security/test_web_research.py tests/security/test_company_searches.py >> ..\.sync\p32.log 2>&1
echo done > ..\.sync\p32-done.txt
