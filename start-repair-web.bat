@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

:: Kill any process already using port 8765
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":8765 .*LISTENING" 2^>nul') do (
    set PID=%%a
    if not "!PID!"=="" (
        echo Stopping existing repair web server (PID: !PID!)...
        taskkill /PID !PID! /F >nul 2>&1
        timeout /t 1 /nobreak >nul
    )
)

echo ============================================
echo   Codex 一键修复 Web 面板
echo   http://127.0.0.1:8765/
echo ============================================
echo.
echo 保持此窗口打开。按 Ctrl+C 停止。
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-repair-web.ps1"
set EXITCODE=%ERRORLEVEL%

echo.
echo Web 面板已停止。
endlocal
exit /b %EXITCODE%
