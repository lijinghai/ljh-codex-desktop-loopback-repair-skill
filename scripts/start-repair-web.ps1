# Local web panel for Codex Desktop loopback repair.

param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pagePath = Join-Path $repoRoot "web\repair.html"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Add-Log {
    param([System.Collections.Generic.List[string]]$Log, [string]$Message)
    $stamp = Get-Date -Format "HH:mm:ss"
    $Log.Add("[$stamp] $Message") | Out-Null
}

function New-ActionResult {
    param([System.Collections.Generic.List[string]]$Log, [bool]$Ok = $true, [string]$Error = $null)
    $result = @{ ok = $Ok; log = $Log; status = Get-RepairStatus }
    if ($Error) { $result.error = $Error }
    return $result
}

function ConvertTo-ResultJson {
    param([hashtable]$Data)
    return ($Data | ConvertTo-Json -Depth 8 -Compress)
}

function Get-CodexConfigPath { Join-Path $env:USERPROFILE ".codex\config.toml" }

function Get-CodexConfigLines {
    $config = Get-CodexConfigPath
    if (-not (Test-Path $config)) { return @() }
    return Get-Content -LiteralPath $config -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^\s*(sandbox|base_url|experimental_bearer_token)\s*=' }
}

function Get-CcSwitchStatus {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:15721/status" -TimeoutSec 5 -ErrorAction Stop
    } catch {
        return $null
    }
}

function Find-CcSwitchPath {
    $runningPath = (Get-Process cc-switch -ErrorAction SilentlyContinue | Select-Object -First 1).Path
    if ($runningPath) { return $runningPath }

    $common = @(
        "F:\CC\cc-switch.exe",
        (Join-Path $env:LOCALAPPDATA "com.ccswitch.desktop\cc-switch.exe"),
        "C:\Program Files\CC-Switch\cc-switch.exe"
    )
    return $common | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Get-SandboxGuardProcesses {
    try {
        return @(Get-WmiObject Win32_Process -Filter "name='powershell.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -like '*sandbox-guard*' })
    } catch {
        return @()
    }
}

function Stop-CodexProcesses {
    param([System.Collections.Generic.List[string]]$Log)
    $procs = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like '*codex*' })
    if ($procs.Count -eq 0) {
        Add-Log $Log "No Codex process is currently running."
        return
    }
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Add-Log $Log "Stopped $($procs.Count) Codex process(es)."
}

function Backup-CodexConfig {
    param([System.Collections.Generic.List[string]]$Log)
    $config = Get-CodexConfigPath
    if (-not (Test-Path $config)) {
        Add-Log $Log "config.toml was not found at $config."
        return $null
    }
    $backup = "$config.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item -LiteralPath $config -Destination $backup -Force
    Add-Log $Log "Backed up config.toml to $backup."
    return $config
}

function Set-SandboxMode {
    param([string]$Mode, [System.Collections.Generic.List[string]]$Log)
    $config = Backup-CodexConfig $Log
    if (-not $config) { return }

    $text = Get-Content -LiteralPath $config -Raw -Encoding UTF8
    if ($text -match 'sandbox\s*=\s*"[^\"]+"') {
        $text = $text -replace 'sandbox\s*=\s*"[^\"]+"', "sandbox = `"$Mode`""
        [IO.File]::WriteAllText($config, $text, [Text.UTF8Encoding]::new($false))
        Add-Log $Log "Set [windows] sandbox to $Mode."
    } else {
        $append = "`r`n[windows]`r`nsandbox = `"$Mode`"`r`n"
        [IO.File]::AppendAllText($config, $append, [Text.UTF8Encoding]::new($false))
        Add-Log $Log "Added [windows] sandbox = $Mode."
    }
}

function Set-BaseUrlForStrategyB {
    param([System.Collections.Generic.List[string]]$Log)
    $config = Backup-CodexConfig $Log
    if (-not $config) { return }

    $text = Get-Content -LiteralPath $config -Raw -Encoding UTF8
    if ($text -match 'base_url\s*=\s*"[^\"]+"') {
        $text = $text -replace 'base_url\s*=\s*"[^\"]+"', 'base_url = "http://127.0.0.1:7897/v1"'
        [IO.File]::WriteAllText($config, $text, [Text.UTF8Encoding]::new($false))
        Add-Log $Log "Set base_url to http://127.0.0.1:7897/v1 for Strategy B."
    } else {
        Add-Log $Log "No base_url line found; Strategy B portproxy was set, but base_url was not changed."
    }
}

function Add-LoopbackExemption {
    param([System.Collections.Generic.List[string]]$Log)
    try {
        $pkg = (Get-AppxPackage -Name '*OpenAI*' -ErrorAction SilentlyContinue | Select-Object -First 1).PackageFullName
        if ($pkg) {
            CheckNetIsolation LoopbackExempt -a -n="$pkg" | Out-Null
            Add-Log $Log "Added/confirmed AppContainer loopback exemption for $pkg."
        } else {
            Add-Log $Log "OpenAI MSIX package was not found; loopback exemption skipped."
        }
    } catch {
        Add-Log $Log "Loopback exemption failed: $($_.Exception.Message)"
    }
}

function Clean-SandboxState {
    param([System.Collections.Generic.List[string]]$Log)
    if (-not (Test-IsAdmin)) {
        Add-Log $Log "Administrator rights are required for firewall and sandbox user cleanup."
        return $false
    }
    netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_tcp" | Out-Null
    netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_udp" | Out-Null
    netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_outbound" | Out-Null
    net user CodexSandboxOffline /delete 2>$null | Out-Null
    net user CodexSandboxOnline /delete 2>$null | Out-Null
    Add-Log $Log "Deleted known Codex sandbox firewall rules and sandbox users."
    return $true
}

function Remove-Portproxy {
    param([System.Collections.Generic.List[string]]$Log)
    if (-not (Test-IsAdmin)) {
        Add-Log $Log "Administrator rights are required to delete netsh portproxy entries."
        return $false
    }
    netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1 | Out-Null
    Add-Log $Log "Deleted portproxy 127.0.0.1:7897 -> 127.0.0.1:15721 if it existed."
    return $true
}

function Set-Portproxy {
    param([System.Collections.Generic.List[string]]$Log)
    if (-not (Test-IsAdmin)) {
        Add-Log $Log "Administrator rights are required to set netsh portproxy entries."
        return $false
    }
    netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1 | Out-Null
    netsh interface portproxy add v4tov4 listenport=7897 listenaddress=127.0.0.1 connectport=15721 connectaddress=127.0.0.1 | Out-Null
    netsh advfirewall firewall add rule name="Allow Codex Proxy 7897" dir=in action=allow protocol=tcp localport=7897 | Out-Null
    Add-Log $Log "Created portproxy 127.0.0.1:7897 -> 127.0.0.1:15721 and allowed local port 7897."
    return $true
}

function Remove-ProxyVars {
    param([System.Collections.Generic.List[string]]$Log)
    $removed = 0
    foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')) {
        $value = [Environment]::GetEnvironmentVariable($name, 'User')
        if ($value) {
            [Environment]::SetEnvironmentVariable($name, $null, 'User')
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
            Add-Log $Log "Removed user environment variable $name=$value."
            $removed++
        }
    }
    if ($removed -eq 0) { Add-Log $Log "No user-level HTTP_PROXY or HTTPS_PROXY variables were found." }
}

function Start-CcSwitch {
    param([System.Collections.Generic.List[string]]$Log)
    $path = Find-CcSwitchPath
    if (-not $path) {
        Add-Log $Log "CC-Switch executable was not found. Open the CC-Switch GUI or install it first."
        return $false
    }
    Start-Process $path -WindowStyle Hidden
    Add-Log $Log "Started CC-Switch from $path."
    return $true
}

function Restart-CcSwitch {
    param([System.Collections.Generic.List[string]]$Log)
    $path = Find-CcSwitchPath
    Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
    if (-not $path) {
        Add-Log $Log "CC-Switch executable was not found after stopping process."
        return $false
    }
    Start-Process $path -WindowStyle Hidden
    Add-Log $Log "Restarted CC-Switch from $path. Wait 30-60 seconds for provider auto-recovery."
    return $true
}

function Get-RepairStatus {
    $configLines = @(Get-CodexConfigLines)
    $sandbox = "unknown"
    $baseUrl = "unknown"
    $tokenManaged = $false

    foreach ($line in $configLines) {
        if ($line -match '^\s*sandbox\s*=\s*"([^"]+)"') { $sandbox = $Matches[1] }
        if ($line -match '^\s*base_url\s*=\s*"([^"]+)"') { $baseUrl = $Matches[1] }
        if ($line -match '^\s*experimental_bearer_token\s*=\s*"PROXY_MANAGED"') { $tokenManaged = $true }
    }

    $cc = Get-CcSwitchStatus
    $ccHealthy = $false
    $provider = "none"
    $lastError = $null
    if ($cc) {
        $provider = [string]$cc.current_provider
        $lastError = $cc.last_error
        $ccHealthy = [bool]($cc.running -and $cc.current_provider -and -not $cc.last_error)
    }

    $loopback = @()
    try {
        $loopback = @(CheckNetIsolation LoopbackExempt -s 2>$null | Select-String 'codex|openai' | ForEach-Object { $_.ToString().Trim() })
    } catch {}

    $portproxy = @()
    try { $portproxy = @(netsh interface portproxy show all 2>$null | ForEach-Object { $_.ToString() }) } catch {}
    $hasPortproxy = (($portproxy -join "`n") -match '127\.0\.0\.1\s+7897\s+127\.0\.0\.1\s+15721')

    $largeSessions = @()
    $sessionRoot = Join-Path $env:USERPROFILE ".codex\sessions"
    if (Test-Path $sessionRoot) {
        $largeSessions = @(Get-ChildItem $sessionRoot -Recurse -Filter "*.jsonl" -ErrorAction SilentlyContinue |
            Where-Object { $_.Length -gt 5MB } |
            Sort-Object Length -Descending |
            Select-Object -First 10 @{Name="mb";Expression={[math]::Round($_.Length / 1MB, 2)}}, FullName)
    }

    $proxyVars = @()
    foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')) {
        $value = [Environment]::GetEnvironmentVariable($name, 'User')
        if ($value) { $proxyVars += "$name=$value" }
    }

    return @{
        ok = $true
        isAdmin = Test-IsAdmin
        sandbox = $sandbox
        baseUrl = $baseUrl
        tokenManaged = $tokenManaged
        ccSwitchHealthy = $ccHealthy
        ccSwitchRunning = [bool]$cc
        provider = $provider
        lastError = $lastError
        hasLoopback = ($loopback.Count -gt 0)
        loopback = $loopback
        hasPortproxy = $hasPortproxy
        largeSessions = $largeSessions
        proxyVars = $proxyVars
        guardInstalled = (Test-Path (Join-Path $env:USERPROFILE ".codex\sandbox-guard.ps1"))
        guardRunning = [bool](Get-SandboxGuardProcesses)
        checkedAt = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    }
}

function Invoke-Diagnose {
    $log = [System.Collections.Generic.List[string]]::new()
    $status = Get-RepairStatus
    Add-Log $log "sandbox=$($status.sandbox), base_url=$($status.baseUrl), PROXY_MANAGED=$($status.tokenManaged)."
    Add-Log $log "CC-Switch running=$($status.ccSwitchRunning), healthy=$($status.ccSwitchHealthy), provider=$($status.provider), last_error=$($status.lastError)."
    Add-Log $log "Loopback exemption found=$($status.hasLoopback)."
    Add-Log $log "portproxy found=$($status.hasPortproxy)."
    Add-Log $log "Sandbox guard installed=$($status.guardInstalled), running=$($status.guardRunning)."
    Add-Log $log "Large session files over 5MB: $($status.largeSessions.Count)."
    if ($status.proxyVars.Count -gt 0) { Add-Log $log "Proxy variables: $($status.proxyVars -join '; ')." }
    if ($status.sandbox -eq 'elevated') { Add-Log $log "Recommendation: use Strategy A and install Sandbox Guard." }
    if (-not $status.ccSwitchHealthy) { Add-Log $log "Recommendation: start/restart CC-Switch and wait 30-60 seconds for recovery." }
    return @{ ok = $true; log = $log; status = $status }
}

function Invoke-StrategyA {
    $log = [System.Collections.Generic.List[string]]::new()
    Stop-CodexProcesses $log
    Set-SandboxMode "unelevated" $log
    Add-LoopbackExemption $log
    Clean-SandboxState $log | Out-Null
    Set-Portproxy $log | Out-Null
    return (New-ActionResult $log)
}

function Invoke-RecommendedRepair {
    $log = [System.Collections.Generic.List[string]]::new()
    Stop-CodexProcesses $log
    Remove-ProxyVars $log
    Set-SandboxMode "unelevated" $log
    Add-LoopbackExemption $log
    Clean-SandboxState $log | Out-Null
    Set-Portproxy $log | Out-Null

    $cc = Get-CcSwitchStatus
    if (-not $cc -or -not $cc.current_provider -or $cc.last_error) {
        Restart-CcSwitch $log | Out-Null
    } else {
        Add-Log $log "CC-Switch is reachable with provider $($cc.current_provider)."
    }
    return (New-ActionResult $log)
}

function Invoke-StrategyB {
    $log = [System.Collections.Generic.List[string]]::new()
    $status = Get-RepairStatus
    if ($status.tokenManaged) {
        Add-Log $log "WARNING: PROXY_MANAGED is active. CC-Switch Live Takeover will probably revert base_url to 15721. Strategy A is recommended."
    }
    Stop-CodexProcesses $log
    Set-SandboxMode "elevated" $log
    Set-BaseUrlForStrategyB $log
    Set-Portproxy $log | Out-Null
    Add-LoopbackExemption $log
    Clean-SandboxState $log | Out-Null
    return (New-ActionResult $log)
}

function Install-SandboxGuard {
    $log = [System.Collections.Generic.List[string]]::new()
    $guardPs1 = Join-Path $repoRoot "scripts\sandbox-guard.ps1"
    $guardVbs = Join-Path $repoRoot "scripts\sandbox-guard.vbs"
    if (-not (Test-Path $guardPs1)) {
        $skillRoot = Join-Path $env:USERPROFILE ".codex\skills\ljh-codex-desktop-loopback-repair-skill"
        $guardPs1 = Join-Path $skillRoot "scripts\sandbox-guard.ps1"
        $guardVbs = Join-Path $skillRoot "scripts\sandbox-guard.vbs"
    }
    if (-not (Test-Path $guardPs1) -or -not (Test-Path $guardVbs)) {
        Add-Log $log "Sandbox guard scripts were not found."
        return (New-ActionResult $log $false "sandbox guard scripts not found")
    }

    $codexDir = Join-Path $env:USERPROFILE ".codex"
    New-Item -ItemType Directory -Force -Path $codexDir | Out-Null
    Copy-Item $guardPs1 (Join-Path $codexDir "sandbox-guard.ps1") -Force
    Copy-Item $guardVbs (Join-Path $codexDir "sandbox-guard.vbs") -Force
    Copy-Item $guardVbs (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\sandbox-guard.vbs") -Force
    Get-SandboxGuardProcesses |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 1
    Start-Process powershell.exe -ArgumentList '-WindowStyle Hidden -ExecutionPolicy Bypass -File', (Join-Path $codexDir "sandbox-guard.ps1") -WindowStyle Hidden
    Add-Log $log "Installed Sandbox Guard to .codex and Startup folder, then started it."
    return (New-ActionResult $log)
}

function Install-Launcher {
    $log = [System.Collections.Generic.List[string]]::new()
    $bat = Join-Path $repoRoot "scripts\start-codex.bat"
    if (-not (Test-Path $bat)) {
        $bat = Join-Path $env:USERPROFILE ".codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts\start-codex.bat"
    }
    if (-not (Test-Path $bat)) {
        Add-Log $log "start-codex.bat was not found."
        return (New-ActionResult $log $false "start-codex.bat not found")
    }
    $codexDir = Join-Path $env:USERPROFILE ".codex"
    New-Item -ItemType Directory -Force -Path $codexDir | Out-Null
    Copy-Item $bat (Join-Path $codexDir "start-codex.bat") -Force
    Add-Log $log "Installed start-codex.bat to $codexDir."
    return (New-ActionResult $log)
}

function Clear-Context413 {
    $log = [System.Collections.Generic.List[string]]::new()
    Stop-CodexProcesses $log
    $today = Get-Date -Format "yyyy\MM\dd"
    $src = Join-Path (Join-Path $env:USERPROFILE ".codex\sessions") $today
    $archive = Join-Path $env:USERPROFILE ".codex\archived_sessions"
    $moved = 0
    if (Test-Path $src) {
        New-Item -ItemType Directory -Force -Path $archive | Out-Null
        Get-ChildItem $src -Filter "*.jsonl" -File -ErrorAction SilentlyContinue | ForEach-Object {
            Move-Item -LiteralPath $_.FullName -Destination $archive -Force
            $moved++
        }
        Add-Log $log "Archived $moved session file(s) from today's folder to $archive."
    } else {
        Add-Log $log "Today's session folder was not found: $src."
    }
    $result = New-ActionResult $log
    $result.moved = $moved
    return $result
}

function Verify-CcSwitchApi {
    $log = [System.Collections.Generic.List[string]]::new()
    $body = @{ model = "gpt-5.5"; input = @(@{ role = "user"; content = "hi" }); max_output_tokens = 10 } | ConvertTo-Json -Depth 5 -Compress
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:15721/v1/responses" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 20 -ErrorAction Stop
        $compact = $response | ConvertTo-Json -Depth 5 -Compress
        if ($compact.Length -gt 1000) { $compact = $compact.Substring(0, 1000) + "..." }
        Add-Log $log "API test succeeded: $compact"
        return (New-ActionResult $log)
    } catch {
        Add-Log $log "API test failed: $($_.Exception.Message)"
        return (New-ActionResult $log $false $_.Exception.Message)
    }
}

function Test-UpstreamConnectivity {
    $log = [System.Collections.Generic.List[string]]::new()
    try {
        $db = "$env:USERPROFILE\.cc-switch\cc-switch.db"
        if (-not (Test-Path $db)) { return (New-ActionResult $log $false "CC-Switch database not found at $db") }

        # Get current provider's upstream URL from settings_config
        $configs = & sqlite3 $db "SELECT id, name, settings_config FROM providers WHERE app_type='codex' AND is_current=1;"
        if (-not $configs) { return (New-ActionResult $log $false "No active codex provider found") }

        # Parse base_url from settings_config JSON
        $providerId = ($configs -split '\|')[0]
        $providerName = ($configs -split '\|')[1]
        $settingsJson = ($configs -split '\|', 3)[2]

        $upstreamUrl = $null
        if ($settingsJson -match 'base_url\s*=\s*"([^"]+)"') {
            $upstreamUrl = $Matches[1]
        }
        if (-not $upstreamUrl) {
            Add-Log $log "Could not parse base_url from provider config."
            return (New-ActionResult $log $false "Cannot parse upstream URL")
        }

        Add-Log $log "Provider: $providerName ($providerId)"
        Add-Log $log "Upstream URL: $upstreamUrl"

        # Test connectivity to upstream
        $testUrl = "$upstreamUrl/responses"
        Add-Log $log "Testing connectivity to $testUrl ..."
        try {
            $response = Invoke-WebRequest -Uri $testUrl -Method Post -ContentType "application/json" -Body '{"model":"gpt-5.5","input":[{"role":"user","content":"hi"}],"max_output_tokens":5}' -TimeoutSec 10 -SkipHttpErrorCheck
            $statusCode = $response.StatusCode
            if ($statusCode -eq 200) {
                Add-Log $log "Upstream reachable! HTTP $statusCode. API is healthy."
                return (New-ActionResult $log)
            } elseif ($statusCode -eq 401) {
                Add-Log $log "Upstream reachable but returned HTTP 401. URL is correct but API key may be invalid."
                return (New-ActionResult $log $false "HTTP 401 — check API key")
            } else {
                Add-Log $log "Upstream reachable but returned HTTP $statusCode."
                return (New-ActionResult $log $false "HTTP $statusCode")
            }
        } catch {
            $errMsg = $_.Exception.Message
            if ($errMsg -match 'timeout|timed out|操作超时') {
                Add-Log $log "Upstream TIMEOUT! $upstreamUrl is unreachable. Server may be down."
            } else {
                Add-Log $log "Upstream connection failed: $errMsg"
            }
            return (New-ActionResult $log $false "Upstream unreachable")
        }
    } catch {
        Add-Log $log "ERROR: $($_.Exception.Message)"
        return (New-ActionResult $log $false $_.Exception.Message)
    }
}

function Invoke-DeepRecovery {
    $log = [System.Collections.Generic.List[string]]::new()
    try {
        Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force
        Add-Log $log "Stopped CC-Switch."
        Start-Sleep 2

        $db = "$env:USERPROFILE\.cc-switch\cc-switch.db"
        if (-not (Test-Path $db)) { return (New-ActionResult $log $false "CC-Switch database not found at $db") }

        # Check available providers
        $providers = & sqlite3 $db "SELECT id, app_type, name, is_current FROM providers WHERE app_type='codex';"
        Add-Log $log "Available codex providers: $($providers -join ' | ')"

        # Show current backup state
        $backupInfo = & sqlite3 $db "SELECT length(original_config) FROM proxy_live_backup WHERE app_type='codex';"
        if ($backupInfo) {
            Add-Log $log "proxy_live_backup exists ($backupInfo bytes). Will clean on restart."
        } else {
            Add-Log $log "No proxy_live_backup found for codex."
        }

        # If the default provider has working config, switch to it
        $defaultCfg = & sqlite3 $db "SELECT settings_config FROM providers WHERE id='default' AND app_type='codex';"
        if ($defaultCfg -and $defaultCfg -match 'base_url\s*=\s*"([^"]+)"') {
            $defaultUrl = $Matches[1]
            Add-Log $log "Default provider endpoint: $defaultUrl"

            # Test if default provider's upstream is reachable
            try {
                $test = Invoke-WebRequest -Uri "$defaultUrl/responses" -Method Post -ContentType "application/json" -Body '{"model":"gpt-5.5","input":[{"role":"user","content":"hi"}],"max_output_tokens":5}' -TimeoutSec 8 -SkipHttpErrorCheck
                Add-Log $log "Default provider upstream is reachable (HTTP $($test.StatusCode))."
            } catch {
                Add-Log $log "Warning: Default provider upstream may be unreachable: $($_.Exception.Message)"
            }

            # Switch to default provider
            & sqlite3 $db "UPDATE providers SET is_current = 0 WHERE app_type = 'codex'; UPDATE providers SET is_current = 1 WHERE id = 'default' AND app_type = 'codex';"
            Add-Log $log "Switched active codex provider to 'default'."
        }

        # Clean proxy_live_backup to prevent stale config restoration
        & sqlite3 $db "DELETE FROM proxy_live_backup WHERE app_type = 'codex';"
        Add-Log $log "Cleared proxy_live_backup for codex (CC-Switch will recreate on next Takeover)."

        # Restart CC-Switch
        $ccPath = Find-CcSwitchPath
        if ($ccPath) {
            Start-Process $ccPath -WindowStyle Hidden
            Start-Sleep 4
            Add-Log $log "Restarted CC-Switch from $ccPath."

            # Check status
            try {
                $status = Invoke-RestMethod -Uri "http://127.0.0.1:15721/status" -TimeoutSec 5
                Add-Log $log "CC-Switch status: running=$($status.running), provider=$($status.current_provider), last_error=$($status.last_error)"
            } catch {
                Add-Log $log "CC-Switch status check failed: $($_.Exception.Message)"
            }
        } else {
            Add-Log $log "ERROR: CC-Switch executable not found. Start it manually."
        }

        return (New-ActionResult $log)
    } catch {
        Add-Log $log "ERROR: $($_.Exception.Message)"
        return (New-ActionResult $log $false $_.Exception.Message)
    }
}

function Send-Response {
    param($Client, [int]$StatusCode, [string]$ContentType, [string]$Body)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Body)
    $reason = if ($StatusCode -eq 200) { "OK" } elseif ($StatusCode -eq 404) { "Not Found" } else { "Internal Server Error" }
    $headers = @(
        "HTTP/1.1 $StatusCode $reason",
        "Content-Type: $ContentType; charset=utf-8",
        "Content-Length: $($bytes.Length)",
        "Cache-Control: no-store",
        "Connection: close",
        "",
        ""
    ) -join "`r`n"
    $headerBytes = [Text.Encoding]::ASCII.GetBytes($headers)
    $stream = $Client.GetStream()
    $stream.Write($headerBytes, 0, $headerBytes.Length)
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush()
}

function Invoke-Route {
    param([string]$Method, [string]$Path)
    if ($Method -eq "GET" -and ($Path -eq "/" -or $Path -eq "/repair.html")) {
        return @{ status = 200; type = "text/html"; body = (Get-Content -LiteralPath $pagePath -Raw -Encoding UTF8) }
    }
    if ($Method -eq "GET" -and $Path -eq "/api/status") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Get-RepairStatus)) } }
    if ($Method -eq "GET" -and $Path -eq "/api/diagnose") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Invoke-Diagnose)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/repair") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Invoke-RecommendedRepair)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/strategy-a") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Invoke-StrategyA)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/strategy-b") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Invoke-StrategyB)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/clear-413") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Clear-Context413)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/install-guard") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Install-SandboxGuard)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/install-launcher") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Install-Launcher)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/remove-proxy") { $log = [System.Collections.Generic.List[string]]::new(); Remove-ProxyVars $log; return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (New-ActionResult $log)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/start-ccswitch") { $log = [System.Collections.Generic.List[string]]::new(); Start-CcSwitch $log | Out-Null; return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (New-ActionResult $log)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/restart-ccswitch") { $log = [System.Collections.Generic.List[string]]::new(); Restart-CcSwitch $log | Out-Null; return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (New-ActionResult $log)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/verify-api") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Verify-CcSwitchApi)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/stop-codex") { $log = [System.Collections.Generic.List[string]]::new(); Stop-CodexProcesses $log; return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (New-ActionResult $log)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/add-loopback") { $log = [System.Collections.Generic.List[string]]::new(); Add-LoopbackExemption $log; return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (New-ActionResult $log)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/clean-sandbox") { $log = [System.Collections.Generic.List[string]]::new(); Clean-SandboxState $log | Out-Null; return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (New-ActionResult $log)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/clean-portproxy") { $log = [System.Collections.Generic.List[string]]::new(); Set-Portproxy $log | Out-Null; return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (New-ActionResult $log)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/upstream-check") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Test-UpstreamConnectivity)) } }
    if ($Method -eq "POST" -and $Path -eq "/api/deep-recovery") { return @{ status = 200; type = "application/json"; body = (ConvertTo-ResultJson (Invoke-DeepRecovery)) } }
    return @{ status = 404; type = "application/json"; body = '{"ok":false,"error":"not found"}' }
}

if (-not (Test-Path $pagePath)) {
    Write-Error "Missing web page: $pagePath"
    exit 1
}

$existing = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    Write-Host "Port $Port is in use by PID $($existing.OwningProcess), stopping it..."
    Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep 1
}

$address = [Net.IPAddress]::Parse("127.0.0.1")
$listener = [Net.Sockets.TcpListener]::new($address, $Port)
$prefix = "http://127.0.0.1:$Port/"

try {
    $listener.Start()
} catch {
    Write-Error "Could not start repair web server on $prefix. $($_.Exception.Message)"
    exit 1
}

Write-Host "Codex repair web panel: $prefix"
Write-Host "Press Ctrl+C to stop."
if (-not $NoBrowser) { Start-Process $prefix }

try {
    while ($true) {
        $client = $null
        try {
            $client = $listener.AcceptTcpClient()
            $client.ReceiveTimeout = 5000
            $stream = $client.GetStream()
            $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::ASCII, $false, 1024, $true)
            $requestLine = $reader.ReadLine()
            if (-not $requestLine) { continue }
            while ($true) {
                $header = $reader.ReadLine()
                if ($null -eq $header -or $header -eq "") { break }
            }
            $parts = $requestLine.Split(' ')
            $method = $parts[0]
            $path = ($parts[1] -split '\?')[0]
            $route = Invoke-Route $method $path
            Send-Response $client $route.status $route.type $route.body
        } catch [System.Net.Sockets.SocketException] {
            break
        } catch {
            if ($client) { Send-Response $client 500 "application/json" (ConvertTo-ResultJson @{ ok = $false; error = $_.Exception.Message }) }
        } finally {
            if ($client) { $client.Close() }
        }
    }
} finally {
    $listener.Stop()
}
