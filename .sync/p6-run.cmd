@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do set "%%a=%%b"
uv sync > ..\.sync\p6-migrate.log 2>&1
uv run alembic -x url=%DATABASE_OWNER_URL% upgrade head >> ..\.sync\p6-migrate.log 2>&1
uv run alembic -x url=%DATABASE_OWNER_URL% current >> ..\.sync\p6-migrate.log 2>&1
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run pytest -q -p no:cacheprovider --junitxml=..\.sync\p6-junit.xml > ..\.sync\p6-pytest.log 2>&1
echo DONE >> ..\.sync\p6-pytest.log
