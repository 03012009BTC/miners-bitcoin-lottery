@echo off
REM ============================================================
REM  Laptop fix: stop Windows from putting the USB miners to sleep.
REM
REM  On a laptop, Windows suspends USB devices to save battery.
REM  A mining stick that gets suspended goes silent until you
REM  physically unplug and replug it. This script turns that off.
REM
REM  Everything it changes is reversible - see UNDO at the bottom.
REM  Run it ONCE on the laptop, then reboot.
REM ============================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Asking for administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo.
echo  MINERS - laptop USB fix
echo  =======================
echo.

echo  [1/3] Disabling USB selective suspend (on power and on battery)...
powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-9a4e-e3aa1c26c4b2 0
powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-9a4e-e3aa1c26c4b2 0

echo  [2/3] Keeping the machine awake while plugged in (lid can stay closed)...
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 5ca83367-6e45-459f-a27b-476b1d01c936 0
powercfg /setactive SCHEME_CURRENT

echo  [3/3] Telling Windows not to power down USB hubs...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$n=0; Get-WmiObject MSPower_DeviceEnable -Namespace root\wmi -ErrorAction SilentlyContinue | Where-Object { $_.InstanceName -like 'USB\*' } | ForEach-Object { try { $_.Enable = $false; $_.Put() | Out-Null; $n++ } catch {} }; Write-Host ('        power saving switched off on ' + $n + ' USB devices')"

echo.
echo  Done. Please REBOOT the laptop, then plug the miners in and
echo  start the miner with START_MINING.bat.
echo.
echo  If a stick still goes silent after a while, it is not power
echo  management - try another cable and a directly powered USB hub.
echo.

REM ============================================================
REM  UNDO (run these three lines as Administrator to revert):
REM    powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-9a4e-e3aa1c26c4b2 1
REM    powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-9a4e-e3aa1c26c4b2 1
REM    powercfg /setactive SCHEME_CURRENT
REM  and tick "Allow the computer to turn off this device to save
REM  power" back on for the USB Root Hubs in Device Manager.
REM ============================================================
pause
