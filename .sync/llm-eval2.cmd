@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do set "%%a=%%b"
set "VIRTUAL_ENV="
set L=..\.sync\llm-eval2.log
docker exec pipelineos-postgres-1 psql -U postgres -c "DROP DATABASE IF EXISTS pipelineos_llmeval WITH (FORCE)" > %L% 2>&1
docker exec pipelineos-postgres-1 psql -U postgres -c "CREATE DATABASE pipelineos_llmeval OWNER pipelineos_owner" >> %L% 2>&1
docker exec pipelineos-postgres-1 psql -U postgres -d pipelineos_llmeval -c "CREATE EXTENSION vector; CREATE EXTENSION pgcrypto;" >> %L% 2>&1
set "DATABASE_URL=postgresql+psycopg://pipelineos_app:app_dev_only@127.0.0.1:5433/pipelineos_llmeval"
set "DATABASE_OWNER_URL=postgresql+psycopg://pipelineos_owner:owner_dev_only@127.0.0.1:5433/pipelineos_llmeval"
uv run alembic -x url=%DATABASE_OWNER_URL% upgrade head >> %L% 2>&1
uv run python -m tests.evals.local_model_run --model phi3:mini --skip-replies --out ..\.sync\llm-eval-phi3-pipeline.json >> %L% 2>&1
echo DONE >> %L%
