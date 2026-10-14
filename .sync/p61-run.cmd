@echo off
cd /d C:\Users\heniz\Desktop\piplineOS
for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
set "VIRTUAL_ENV="
cd backend
uv sync > ..\.sync\p61-migrate.log 2>&1
uv run alembic -x url=postgresql+psycopg://pipelineos_owner:owner_dev_only@127.0.0.1:5433/pipelineos upgrade head >> ..\.sync\p61-migrate.log 2>&1
uv run alembic -x url=postgresql+psycopg://pipelineos_owner:owner_dev_only@127.0.0.1:5433/pipelineos_show upgrade head >> ..\.sync\p61-migrate.log 2>&1
echo MIGRATED >> ..\.sync\p61-migrate.log
start "pipelineos-api" /min cmd /c C:\Users\heniz\Desktop\piplineOS\.sync\show-api.cmd
start "pipelineos-web" /min cmd /c C:\Users\heniz\Desktop\piplineOS\.sync\show-web.cmd
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run pytest -q -p no:cacheprovider --junitxml=..\.sync\p61-junit.xml > ..\.sync\p61-pytest.log 2>&1
echo DONE >> ..\.sync\p61-pytest.log
