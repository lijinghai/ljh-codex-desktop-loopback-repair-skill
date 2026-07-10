param(
    [string]$ConfigPath = "$env:USERPROFILE\.codex\config.toml",
    [string]$TaskName = "CodexSandboxGuard",
    [string]$GuardPath = "$env:USERPROFILE\.codex\sandbox-guard-system.ps1"
)

$ErrorActionPreference = "Continue"

$guardDir = Split-Path -Parent $GuardPath
New-Item -ItemType Directory -Force -Path $guardDir | Out-Null

$escapedConfig = $ConfigPath.Replace("'", "''")
$guard = @"
`$configPath = '$escapedConfig'
`$logPath = Join-Path (Split-Path -Parent `$configPath) 'sandbox-guard.log'

function Write-GuardLog([string]`$Message) {
    "`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | `$Message" | Out-File `$logPath -Append -Encoding utf8
}

function Repair-Sandbox {
    try {
        if (-not (Test-Path `$configPath)) { return }
        `$content = Get-Content `$configPath -Raw -Encoding UTF8 -ErrorAction Stop
        `$fixed = `$content
        if (`$fixed -match 'sandbox\s*=\s*"[^"]+"') {
            `$fixed = `$fixed -replace 'sandbox\s*=\s*"[^"]+"', 'sandbox = "unelevated"'
        }
        elseif (`$fixed -match '(?m)^\[windows\]\s*`$') {
            `$fixed = [regex]::Replace(`$fixed, '(?m)^\[windows\]\s*`$', "[windows]``r``nsandbox = `"unelevated`"", 1)
        }
        else {
            `$fixed = `$fixed.TrimEnd() + "``r``n``r``n[windows]``r``nsandbox = `"unelevated`"``r``n"
        }
        if (`$fixed -ne `$content) {
            [System.IO.File]::WriteAllText(`$configPath, `$fixed, [System.Text.UTF8Encoding]::new(`$false))
            Write-GuardLog 'FIXED: sandbox -> unelevated'
        }
    }
    catch {
        Write-GuardLog "ERROR: `$_"
    }
}

Write-GuardLog '=== Guard system task started (10s poll) ==='
Repair-Sandbox
while (`$true) {
    Start-Sleep -Seconds 10
    Repair-Sandbox
}
"@

Set-Content -Path $GuardPath -Value $guard -Encoding UTF8

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$GuardPath`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4

Write-Output "Task=$TaskName GuardPath=$GuardPath ConfigPath=$ConfigPath"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State | Format-Table -AutoSize | Out-String -Width 220
Get-CimInstance Win32_Process -Filter "name = 'powershell.exe'" |
    Where-Object { $_.CommandLine -like "*$GuardPath*" } |
    Select-Object ProcessId,CommandLine |
    Format-Table -AutoSize |
    Out-String -Width 260
