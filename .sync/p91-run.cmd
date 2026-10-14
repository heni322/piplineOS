@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do set "%%a=%%b"
set "VIRTUAL_ENV="
set L=..\.sync\p91.log
uv sync > %L% 2>&1
uv run pipelineos config sync >> %L% 2>&1
set "DATABASE_URL=postgresql+psycopg://pipelineos_app:app_dev_only@127.0.0.1:5433/pipelineos_show"
set "DATABASE_OWNER_URL=postgresql+psycopg://pipelineos_owner:owner_dev_only@127.0.0.1:5433/pipelineos_show"
uv run pipelineos config sync >> %L% 2>&1
echo SYNCED >> %L%
uv run python -m tests.evals.local_model_run --model qwen2.5:7b --language nordic --skip-pipeline --out ..\.sync\llm-eval-qwen7b-nordic2.json >> %L% 2>&1
echo NORDIC-EVAL-DONE >> %L%
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run pytest -q -p no:cacheprovider --junitxml=..\.sync\p91-junit.xml > ..\.sync\p91-pytest.log 2>&1
echo DONE >> ..\.sync\p91-pytest.log