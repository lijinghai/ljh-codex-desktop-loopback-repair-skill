param(
    [string]$CcSwitchPath = "",
    [string]$TaskName = "StartCCSwitchInteractive",
    [switch]$Restart
)

$ErrorActionPreference = "Continue"

if (-not $CcSwitchPath) {
    $CcSwitchPath = (Get-Process cc-switch -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Path)
}
if (-not $CcSwitchPath) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\CC Switch\cc-switch.exe",
        "$env:LOCALAPPDATA\com.ccswitch.desktop\cc-switch.exe",
        "C:\Program Files\CC-Switch\cc-switch.exe",
        "F:\CC\cc-switch.exe",
        "D:\Users\$env:USERNAME\AppData\Local\Programs\CC Switch\cc-switch.exe"
    )
    $CcSwitchPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not $CcSwitchPath -or -not (Test-Path $CcSwitchPath)) {
    Write-Error "cc-switch.exe not found. Pass -CcSwitchPath explicitly."
    exit 1
}

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if ($Restart) {
    Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

$action = New-ScheduledTaskAction -Execute $CcSwitchPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 8

Write-Output "Task=$TaskName User=$user Path=$CcSwitchPath"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State | Format-Table -AutoSize | Out-String -Width 220
Get-Process cc-switch -ErrorAction SilentlyContinue | Select-Object ProcessName,Id,Path | Format-Table -AutoSize | Out-String -Width 220
curl.exe -s --max-time 8 http://127.0.0.1:15721/status 2>$null | Select-Object -Last 1
