@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do set "%%a=%%b"
set "VIRTUAL_ENV="
set L=..\.sync\p11.log
uv sync > %L% 2>&1
uv run alembic -x url=%DATABASE_OWNER_URL% upgrade head >> %L% 2>&1
uv run alembic -x url=postgresql+psycopg://pipelineos_owner:owner_dev_only@127.0.0.1:5433/pipelineos_show upgrade head >> %L% 2>&1
echo MIGRATED >> %L%
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run pytest -q -p no:cacheprovider --junitxml=..\.sync\p11-junit.xml > ..\.sync\p11-pytest.log 2>&1
echo DONE >> ..\.sync\p11-pytest.log