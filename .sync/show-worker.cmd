@echo off
cd /d C:\Users\heniz\Desktop\piplineOS
for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
set "DATABASE_URL=postgresql+psycopg://pipelineos_app:app_dev_only@127.0.0.1:5433/pipelineos_show"
set "DATABASE_OWNER_URL=postgresql+psycopg://pipelineos_owner:owner_dev_only@127.0.0.1:5433/pipelineos_show"
set "VIRTUAL_ENV="
cd backend
uv run celery -A app.workflows.celery_app worker -Q default -P solo -l INFO > ..\.sync\show-worker.log 2>&1
