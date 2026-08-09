@echo off
rem ============================================
rem  MINERS - Bitcoin Lottery
rem  Double-click = mine. Close window = stop.
rem  Output goes to the screen AND to miner.log.
rem  Dashboard: double-click dashboard.html
rem ============================================
cd /d "%~dp0"

rem  Never run two of these at once: they fight over the USB ports and the
rem  dashboard port, and the older one wins silently.
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*miner.py*' }) { exit 1 }"
if errorlevel 1 (
  echo MINERS is already mining in another window.
  echo Close that window first if you want to restart it.
  echo.
  pause
  exit /b
)

chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title MINERS - Bitcoin Lottery (public-pool.io)

rem  Use the bundled Python if this is the portable package, otherwise the
rem  one installed on the system.
set "PY=python"
if exist "%~dp0python\python.exe" set "PY=.\python\python.exe"

powershell -NoProfile -Command "& '%PY%' -u miner.py 2>&1 | Tee-Object -FilePath miner.log -Append"
echo.
echo Mining stopped. Press any key to close this window.
pause >nul
