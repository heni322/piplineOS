@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do set "%%a=%%b"
set "VIRTUAL_ENV="
set PYTHONIOENCODING=utf-8
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run pytest -q -p no:cacheprovider --junitxml=..\.sync\p19-junit.xml > ..\.sync\p19-pytest.log 2>&1
echo DONE >> ..\.sync\p19-pytest.log