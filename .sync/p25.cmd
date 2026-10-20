@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
set PYTHONIOENCODING=utf-8
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run ruff check --fix . > ..\.sync\p25.log 2>&1
uv run ruff format . >> ..\.sync\p25.log 2>&1
uv run mypy >> ..\.sync\p25.log 2>&1
uv run lint-imports >> ..\.sync\p25.log 2>&1
uv run pytest -p no:cacheprovider -W ignore -q tests/contract/test_boamp.py tests/contract/test_ted.py >> ..\.sync\p25.log 2>&1
echo ---live--- >> ..\.sync\p25.log
uv run pipelineos signals check boamp --query "informatique; logiciel; infogerance; cybersecurite" --days 14 >> ..\.sync\p25.log 2>&1
echo done > ..\.sync\p25-done.txt
