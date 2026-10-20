@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
set PYTHONIOENCODING=utf-8
uv run ruff check . > ..\.sync\p31.log 2>&1
uv run ruff format --check . >> ..\.sync\p31.log 2>&1
uv run mypy >> ..\.sync\p31.log 2>&1
uv run pytest -p no:cacheprovider -W ignore -q tests/contract/test_more_job_boards.py tests/contract/test_free_sources.py >> ..\.sync\p31.log 2>&1
cd ..\frontend
call pnpm typecheck >> ..\.sync\p31.log 2>&1
call pnpm lint >> ..\.sync\p31.log 2>&1
call pnpm test >> ..\.sync\p31.log 2>&1
echo done > ..\.sync\p31-done.txt
