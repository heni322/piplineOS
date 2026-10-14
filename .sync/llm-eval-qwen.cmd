@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do set "%%a=%%b"
set "VIRTUAL_ENV="
set L=..\.sync\llm-eval-qwen.log
echo waiting for model > %L%
:wait
"%LOCALAPPDATA%\Programs\Ollama\ollama.exe" list | findstr /c:"qwen2.5:3b" >nul
if errorlevel 1 ( timeout /t 60 /nobreak >nul & goto wait )
echo model ready >> %L%
docker exec pipelineos-postgres-1 psql -U postgres -c "DROP DATABASE IF EXISTS pipelineos_llmeval_qwen WITH (FORCE)" >> %L% 2>&1
docker exec pipelineos-postgres-1 psql -U postgres -c "CREATE DATABASE pipelineos_llmeval_qwen OWNER pipelineos_owner" >> %L% 2>&1
docker exec pipelineos-postgres-1 psql -U postgres -d pipelineos_llmeval_qwen -c "CREATE EXTENSION vector; CREATE EXTENSION pgcrypto;" >> %L% 2>&1
set "DATABASE_URL=postgresql+psycopg://pipelineos_app:app_dev_only@127.0.0.1:5433/pipelineos_llmeval_qwen"
set "DATABASE_OWNER_URL=postgresql+psycopg://pipelineos_owner:owner_dev_only@127.0.0.1:5433/pipelineos_llmeval_qwen"
uv run alembic -x url=%DATABASE_OWNER_URL% upgrade head >> %L% 2>&1
uv run python -m tests.evals.local_model_run --model qwen2.5:3b --out ..\.sync\llm-eval-qwen3b.json >> %L% 2>&1
echo DONE >> %L%
