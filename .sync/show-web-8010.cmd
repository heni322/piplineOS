@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\frontend
set "API_BASE_URL=http://127.0.0.1:8010"
call pnpm dev > ..\.sync\show-web.log 2>&1
