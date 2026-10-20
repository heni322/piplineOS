@echo off
cd /d C:\Users\heniz\Desktop\piplineOS
for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
set "DATABASE_URL=postgresql+psycopg://pipelineos_app:app_dev_only@127.0.0.1:5433/pipelineos_show"
set "DATABASE_OWNER_URL=postgresql+psycopg://pipelineos_owner:owner_dev_only@127.0.0.1:5433/pipelineos_show"
set "VIRTUAL_ENV="
cd backend
uv run pipelineos users create-operator visual-check@pipelineos.example --password byivDuBURhVKF8Ig9rnH > ..\.sync\show-op.log 2>&1
