@echo off
setlocal
chcp 65001 >nul 2>&1

cd /d "%~dp0"

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765.*LISTENING" 2^>nul') do (
    echo Stopping old server PID %%a...
    taskkill /PID %%a /F >nul 2>&1
    timeout /t 1 /nobreak >nul
)

echo ============================================
echo   Codex Repair Web Panel
echo   http://127.0.0.1:8765/
echo ============================================
echo.
echo Keep this window open. Press Ctrl+C to stop.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-repair-web.ps1"

echo.
echo Server stopped.
endlocal
