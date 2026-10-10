@echo off
rem Release knowledge loop v1 (backup, build, security gate, deploy, enroll, verify).
rem Rollback: RUN-release-knowledge-loop.cmd -Rollback
cd /d "%~dp0"
where pwsh >nul 2>&1
if %errorlevel%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\release-knowledge-loop.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\release-knowledge-loop.ps1" %*
)
echo.
echo Finished. You can close this window.
pause
