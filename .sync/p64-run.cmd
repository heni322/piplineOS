@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
set "VIRTUAL_ENV="
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run pytest -q -p no:cacheprovider --junitxml=..\.sync\p64-junit.xml > ..\.sync\p64-pytest.log 2>&1
echo DONE >> ..\.sync\p64-pytest.log
