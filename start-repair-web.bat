@echo off
setlocal

cd /d "%~dp0"

echo Codex one-click repair web panel
echo URL: http://127.0.0.1:8765/
echo.
echo Keep this window open while using the page.
echo Press Ctrl+C in this window to stop the repair web server.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-repair-web.ps1"

echo.
echo Repair web server stopped.
endlocal
