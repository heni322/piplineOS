@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
set PYTHONIOENCODING=utf-8
set LOCAL_LLM_URL=http://127.0.0.1:11434
uv run pipelineos llm benchmark --models qwen2.5:7b > ..\.sync\p21-bench.json 2> ..\.sync\p21-bench.err
set TEST_PG_ADMIN_URL=postgresql://postgres:postgres_dev_only@127.0.0.1:5433/postgres
uv run pytest -q -p no:cacheprovider --junitxml=..\.sync\p21-junit.xml > ..\.sync\p21-tests.log 2>&1
echo done > ..\.sync\p21-done.txt
