@echo off
rem Double-click to commit the September work in slices and push main to Gbuzz-V2.
rem Runs scripts\commit-release-baseline.ps1; add -WhatIf inside if you want a preview first.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\commit-release-baseline.ps1" -Remote v2 -RemoteUrl https://github.com/Abdenour7496/Gbuzz-V2.git
echo.
echo Done. Review the output above, then check https://github.com/Abdenour7496/Gbuzz-V2/actions
pause
