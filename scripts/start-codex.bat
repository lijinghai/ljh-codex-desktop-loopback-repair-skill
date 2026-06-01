@echo off
chcp 65001 >nul
echo === Codex 一键启动 (修复版) ===
echo.

:: 1. 确保 CC-Switch 运行
echo [1/4] 检查 CC-Switch...
curl.exe -s http://127.0.0.1:15721/status --max-time 3 >nul 2>&1
if errorlevel 1 (
    echo   启动 CC-Switch...
    start "" /MIN "F:\CC\cc-switch.exe"
    echo   等待 CC-Switch 初始化 (30秒)...
    timeout /t 30 /nobreak >nul
) else (
    echo   CC-Switch 已运行
)

:: 2. 修复 sandbox (CC-Switch Live Takeover 会把它改回 elevated)
echo [2/4] 修复 sandbox...
powershell -NoProfile -Command "$c = Get-Content '%USERPROFILE%\.codex\config.toml' -Raw -Encoding UTF8; if ($c -match 'sandbox\s*=\s*""elevated""') { $c -replace 'sandbox\s*=\s*""elevated""', 'sandbox = ""unelevated""' | Set-Content '%USERPROFILE%\.codex\config.toml' -Encoding UTF8 -NoNewline; Write-Host '   sandbox elevated -> unelevated (已修复)' } else { Write-Host '   sandbox 正常' }"

:: 3. 清理旧会话 (防止 413)
echo [3/4] 检查会话大小...
powershell -NoProfile -Command "$big = Get-ChildItem '%USERPROFILE%\.codex\sessions' -Recurse -Filter '*.jsonl' -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 10MB } | Sort-Object Length -Descending; if ($big) { Write-Host '   警告: 发现大于10MB的会话文件!'; $big | Select-Object Length, Name } else { Write-Host '   会话文件正常' }"

:: 4. 启动 Codex
echo [4/4] 启动 Codex...
start "" shell:appsFolder\OpenAI.Codex_2p2nqsd0c76g0!app
echo.
echo === Codex 已启动 ===
echo.
pause
