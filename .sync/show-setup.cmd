@echo off
cd /d C:\Users\heniz\Desktop\piplineOS
for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
set "DATABASE_URL=postgresql+psycopg://pipelineos_app:app_dev_only@127.0.0.1:5433/pipelineos_show"
set "DATABASE_OWNER_URL=postgresql+psycopg://pipelineos_owner:owner_dev_only@127.0.0.1:5433/pipelineos_show"
set "VIRTUAL_ENV="
set LOG=.sync\show-setup.log
echo start > %LOG%
docker exec pipelineos-postgres-1 psql -U postgres -c "CREATE DATABASE pipelineos_show OWNER pipelineos_owner" >> %LOG% 2>&1
docker exec pipelineos-postgres-1 psql -U postgres -d pipelineos_show -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;" >> %LOG% 2>&1
cd backend
uv run alembic -x url=%DATABASE_OWNER_URL% upgrade head >> ..\%LOG% 2>&1
uv run pipelineos demo seed > ..\.sync\show-seed.json 2>> ..\%LOG%
uv run pipelineos demo run > ..\.sync\show-run.json 2>> ..\%LOG%
uv run pipelineos demo conversations > ..\.sync\show-conv.json 2>> ..\%LOG%
uv run pipelineos demo billing > ..\.sync\show-billing.json 2>> ..\%LOG%
echo BACKEND-DONE >> ..\%LOG%
cd ..\frontend
call pnpm install --frozen-lockfile >> ..\%LOG% 2>&1
echo ALL-DONE >> ..\%LOG%
