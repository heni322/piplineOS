@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
uv run ruff check --fix . > ..\.sync\p23-be-lint.log 2>&1
uv run ruff format . >> ..\.sync\p23-be-lint.log 2>&1
uv run mypy >> ..\.sync\p23-be-lint.log 2>&1
uv run lint-imports >> ..\.sync\p23-be-lint.log 2>&1
echo done > ..\.sync\p23-be-lint-done.txt
