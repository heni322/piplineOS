@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
set PYTHONIOENCODING=utf-8
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run pytest -p no:cacheprovider -W ignore -q -rs tests/security/test_web_research.py tests/security/test_company_searches.py > ..\.sync\p33.log 2>&1
cd ..\frontend
call pnpm typecheck >> ..\.sync\p33.log 2>&1
call pnpm lint >> ..\.sync\p33.log 2>&1
call pnpm test >> ..\.sync\p33.log 2>&1
echo done > ..\.sync\p33-done.txt
