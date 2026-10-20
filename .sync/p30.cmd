@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
set PYTHONIOENCODING=utf-8
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run ruff check --fix . > ..\.sync\p30.log 2>&1
uv run ruff format . >> ..\.sync\p30.log 2>&1
uv run mypy >> ..\.sync\p30.log 2>&1
uv run pytest -p no:cacheprovider -W ignore -q tests/contract/test_more_job_boards.py tests/contract/test_free_sources.py >> ..\.sync\p30.log 2>&1
echo ---live--- >> ..\.sync\p30.log
uv run pipelineos signals check job-board --target workable:huggingface --query "" --days 400 >> ..\.sync\p30.log 2>&1
uv run pipelineos signals check job-board --target recruitee:bunq --query "" --days 30 >> ..\.sync\p30.log 2>&1
uv run pipelineos signals check job-board --target smartrecruiters:BoschGroup --query "IT" --days 3 >> ..\.sync\p30.log 2>&1
echo done > ..\.sync\p30-done.txt
