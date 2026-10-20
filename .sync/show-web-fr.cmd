@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\frontend
set "NEXT_PUBLIC_LOCALE=fr-FR"
set "API_BASE_URL=http://127.0.0.1:8010"
call pnpm dev > ..\.sync\show-web.log 2>&1
