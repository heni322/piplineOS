@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do set "%%a=%%b"
set "VIRTUAL_ENV="
set PYTHONIOENCODING=utf-8
set L=..\.sync\live.log
uv sync > %L% 2>&1
echo ==== TED FR >> %L%
uv run pipelineos signals check ted --target FR >> %L% 2>&1
echo ==== TED DE >> %L%
uv run pipelineos signals check ted --target DE >> %L% 2>&1
echo ==== GREENHOUSE gitlab >> %L%
uv run pipelineos signals check job-board --target greenhouse:gitlab >> %L% 2>&1
echo ==== LEVER spotify >> %L%
uv run pipelineos signals check job-board --target lever:spotify >> %L% 2>&1
echo ==== ASHBY ramp >> %L%
uv run pipelineos signals check job-board --target ashby:ramp >> %L% 2>&1
echo ==== GAZETTE 00445790 >> %L%
uv run pipelineos signals check gazette --target 00445790 >> %L% 2>&1
echo ==== WEBSITE 00445790 >> %L%
uv run pipelineos signals check website --target 00445790 --name "TESCO PLC" >> %L% 2>&1
echo ==== OCDS AND REGISTERS >> %L%
uv run python ..\.sync\live_check.py >> %L% 2>&1
echo LIVE-DONE >> %L%