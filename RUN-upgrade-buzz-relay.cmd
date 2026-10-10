@echo off
rem Double-click to upgrade the Buzz relay (backup, pull, pin, rebuild, recreate).
rem Log: C:\Gbuzz\_diag\upgrade-relay-*.log   Status: C:\Gbuzz\_diag\upgrade-relay.status
cd /d "%~dp0"
where pwsh >nul 2>&1
if %errorlevel%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\upgrade-buzz-relay.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\upgrade-buzz-relay.ps1"
)
echo.
echo Finished. You can close this window.
timeout /t 30
