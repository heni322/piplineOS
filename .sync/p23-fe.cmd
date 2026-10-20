@echo off
cd /d C:\Users\heniz\Desktop\piplineOS\frontend
call pnpm typecheck > ..\.sync\p23-fe.log 2>&1
echo ---lint--- >> ..\.sync\p23-fe.log
call pnpm lint >> ..\.sync\p23-fe.log 2>&1
echo ---test--- >> ..\.sync\p23-fe.log
call pnpm test >> ..\.sync\p23-fe.log 2>&1
echo done > ..\.sync\p23-fe-done.txt
