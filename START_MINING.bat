@echo off
rem ============================================
rem  MINERS - Bitcoin Lottery
rem  Double-click = mine. Close window = stop.
rem  Output goes to the screen AND to miner.log.
rem  Dashboard: double-click dashboard.html
rem ============================================
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title MINERS - Bitcoin Lottery (public-pool.io)
powershell -NoProfile -Command "python -u miner.py 2>&1 | Tee-Object -FilePath miner.log -Append"
echo.
echo Mining stopped. Press any key to close this window.
pause >nul
