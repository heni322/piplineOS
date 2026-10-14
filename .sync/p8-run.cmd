@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do set "%%a=%%b"
set "VIRTUAL_ENV="
set L=..\.sync\p8.log
uv sync > %L% 2>&1
uv run alembic -x url=%DATABASE_OWNER_URL% upgrade head >> %L% 2>&1
uv run alembic -x url=postgresql+psycopg://pipelineos_owner:owner_dev_only@127.0.0.1:5433/pipelineos_show upgrade head >> %L% 2>&1
echo MIGRATED >> %L%
uv run python -m tests.evals.local_model_run --model qwen2.5:7b --language fr --skip-pipeline --out ..\.sync\llm-eval-qwen7b-fr.json >> %L% 2>&1
echo FR-EVAL-DONE >> %L%
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run pytest -q -p no:cacheprovider --junitxml=..\.sync\p8-junit.xml > ..\.sync\p8-pytest.log 2>&1
echo DONE >> ..\.sync\p8-pytest.log
