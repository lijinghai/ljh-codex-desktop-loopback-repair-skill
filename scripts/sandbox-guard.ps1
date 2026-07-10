# Codex Sandbox Guard v3 — 纯轮询守护，简单可靠
# CC-Switch Live Takeover 在启动时会把 sandbox 改回 "elevated"
# 此脚本每10秒检查一次，发现后立即修复

$configPath = "$env:USERPROFILE\.codex\config.toml"
$logPath = "$env:USERPROFILE\.codex\sandbox-guard.log"

function Write-Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $msg" | Out-File $logPath -Append -Encoding utf8
}

Write-Log "=== Guard v3 started (10s poll) ==="

# 启动时等30秒让CC-Switch先完成初始化
Start-Sleep -Seconds 30
Write-Log "Initial wait done, starting monitor"

$fixCount = 0

while ($true) {
    try {
        if (Test-Path $configPath) {
            $content = Get-Content $configPath -Raw -Encoding UTF8 -ErrorAction Stop
            if ($content -match 'sandbox\s*=\s*"elevated"') {
                $fixed = $content -replace 'sandbox\s*=\s*"elevated"', 'sandbox = "unelevated"'
                Set-Content $configPath $fixed -Encoding UTF8 -NoNewline -ErrorAction Stop
                $fixCount++
                Write-Log "FIXED (#$fixCount): sandbox elevated -> unelevated"
            }
        }
    } catch {
        Write-Log "ERROR: $_"
    }
    Start-Sleep -Seconds 10
}
