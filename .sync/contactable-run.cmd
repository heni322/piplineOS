@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\backend
for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do set "%%a=%%b"
set "VIRTUAL_ENV="
set PYTHONIOENCODING=utf-8
uv run python ..\.sync\contactable.py > ..\.sync\contactable.log 2>&1
echo DONE >> ..\.sync\contactable.log