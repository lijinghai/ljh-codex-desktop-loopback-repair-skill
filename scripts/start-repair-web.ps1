# Author: 算个文科生吧
# Contact: lijinghailjh@163.com
# Project: ljh_codex-desktop-loopback-repair_skill

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

function ConvertTo-ResultJson {
    param([hashtable]$Data)
    return ($Data | ConvertTo-Json -Depth 8 -Compress)
}

function Get-CodexConfigLines {
    $config = Join-Path $env:USERPROFILE ".codex\config.toml"
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
    try {
        $portproxy = @(netsh interface portproxy show all 2>$null | ForEach-Object { $_.ToString() })
    } catch {}
    $hasLegacyPortproxy = (($portproxy -join "`n") -match '127\.0\.0\.1\s+7897\s+127\.0\.0\.1\s+15721')

    $largeSessions = @()
    $sessionRoot = Join-Path $env:USERPROFILE ".codex\sessions"
    if (Test-Path $sessionRoot) {
        $largeSessions = @(Get-ChildItem $sessionRoot -Recurse -Filter "*.jsonl" -ErrorAction SilentlyContinue |
            Where-Object { $_.Length -gt 5MB } |
            Sort-Object Length -Descending |
            Select-Object -First 10 @{Name="mb";Expression={[math]::Round($_.Length / 1MB, 2)}}, FullName)
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
        loopback = $loopback
        hasLoopback = ($loopback.Count -gt 0)
        hasLegacyPortproxy = $hasLegacyPortproxy
        largeSessions = $largeSessions
        guardInstalled = (Test-Path (Join-Path $env:USERPROFILE ".codex\sandbox-guard.ps1"))
        guardRunning = [bool](Get-WmiObject Win32_Process -Filter "name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*sandbox-guard*' })
        checkedAt = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    }
}

function Install-SandboxGuard {
    $log = [System.Collections.Generic.List[string]]::new()
    $repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
    $guardPs1 = Join-Path $repoRoot "scripts\sandbox-guard.ps1"
    $guardVbs = Join-Path $repoRoot "scripts\sandbox-guard.vbs"

    if (-not (Test-Path $guardPs1)) {
        Add-Log $log "Guard script not found at $guardPs1, trying .codex\skills fallback."
        $guardPs1 = Join-Path $env:USERPROFILE ".codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts\sandbox-guard.ps1"
        $guardVbs = Join-Path $env:USERPROFILE ".codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts\sandbox-guard.vbs"
    }

    if (Test-Path $guardPs1) {
        Copy-Item $guardPs1 (Join-Path $env:USERPROFILE ".codex\sandbox-guard.ps1") -Force
        Copy-Item $guardVbs (Join-Path $env:USERPROFILE ".codex\sandbox-guard.vbs") -Force
        Copy-Item $guardVbs (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\sandbox-guard.vbs") -Force
        Add-Log $log "Sandbox guard scripts installed to .codex and Startup folder."

        Get-WmiObject Win32_Process -Filter "name='powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like '*sandbox-guard*' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep 1
        Start-Process powershell.exe -ArgumentList '-WindowStyle Hidden -ExecutionPolicy Bypass -File', (Join-Path $env:USERPROFILE ".codex\sandbox-guard.ps1") -WindowStyle Hidden
        Add-Log $log "Sandbox guard started. It monitors config.toml every 10s and auto-fixes sandbox=elevated."
    } else {
        Add-Log $log "ERROR: sandbox-guard.ps1 not found. Make sure the skill is installed correctly."
    }

    return @{ ok = $true; log = $log; status = Get-RepairStatus }
}

function Install-Launcher {
    $log = [System.Collections.Generic.List[string]]::new()
    $repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
    $bat = Join-Path $repoRoot "scripts\start-codex.bat"

    if (-not (Test-Path $bat)) {
        $bat = Join-Path $env:USERPROFILE ".codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts\start-codex.bat"
    }

    if (Test-Path $bat) {
        Copy-Item $bat (Join-Path $env:USERPROFILE ".codex\start-codex.bat") -Force
        Add-Log $log "start-codex.bat installed to .codex directory."
        Add-Log $log "Double-click %USERPROFILE%\\.codex\\start-codex.bat to launch Codex safely."
    } else {
        Add-Log $log "ERROR: start-codex.bat not found."
    }

    return @{ ok = $true; log = $log; status = Get-RepairStatus }
}

function Repair-Codex {
    $log = [System.Collections.Generic.List[string]]::new()
    $isAdmin = Test-IsAdmin
    Add-Log $log "Stopping Codex Desktop processes if any are running."
    Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force -ErrorAction SilentlyContinue

    # Remove HTTP_PROXY that breaks CC-Switch outbound connections
    $proxyVars = @('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy')
    foreach ($pv in $proxyVars) {
        $val = [Environment]::GetEnvironmentVariable($pv, 'User')
        if ($val -and $val -match '127\.0\.0\.1') {
            [Environment]::SetEnvironmentVariable($pv, $null, 'User')
            Remove-Item "Env:$pv" -ErrorAction SilentlyContinue
            Add-Log $log "Removed $pv=$val — this was breaking CC-Switch outbound connections."
        }
    }

    $config = Join-Path $env:USERPROFILE ".codex\config.toml"
    if (Test-Path $config) {
        $backup = "$config.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
        Copy-Item -LiteralPath $config -Destination $backup -Force
        Add-Log $log "Backed up config.toml."

        $text = Get-Content -LiteralPath $config -Raw
        if ($text -match 'sandbox\s*=\s*"elevated"') {
            $text = $text -replace 'sandbox\s*=\s*"elevated"', 'sandbox = "unelevated"'
            [IO.File]::WriteAllText($config, $text, [Text.UTF8Encoding]::new($false))
            Add-Log $log "Changed sandbox from elevated to unelevated."
        } elseif ($text -match 'sandbox\s*=\s*"unelevated"') {
            Add-Log $log "Sandbox is already unelevated."
        } else {
            Add-Log $log "No sandbox line found; config was left unchanged."
        }
    } else {
        Add-Log $log "config.toml was not found."
    }

    try {
        $pkg = (Get-AppxPackage -Name '*OpenAI*' -ErrorAction SilentlyContinue | Select-Object -First 1).PackageFullName
        if ($pkg) {
            CheckNetIsolation LoopbackExempt -a -n="$pkg" | Out-Null
            Add-Log $log "Added/confirmed AppContainer loopback exemption."
        } else {
            Add-Log $log "OpenAI Codex MSIX package was not found; loopback exemption skipped."
        }
    } catch {
        Add-Log $log "Loopback exemption failed: $($_.Exception.Message)"
    }

    if ($isAdmin) {
        netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_tcp" | Out-Null
        netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_udp" | Out-Null
        netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_outbound" | Out-Null
        net user CodexSandboxOffline /delete 2>$null | Out-Null
        net user CodexSandboxOnline /delete 2>$null | Out-Null
        netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1 | Out-Null
        Add-Log $log "Cleaned Codex sandbox firewall rules, sandbox users, and legacy portproxy."
    } else {
        Add-Log $log "Not running as Administrator; skipped firewall/user/portproxy cleanup."
    }

    $cc = Get-CcSwitchStatus
    if (-not $cc -or -not $cc.current_provider -or $cc.last_error) {
        $path = Find-CcSwitchPath
        if ($path) {
            Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep 2
            Start-Process $path -WindowStyle Hidden
            Add-Log $log "Restarted CC-Switch. Wait 30 seconds for provider recovery."
        } else {
            Add-Log $log "CC-Switch executable was not found."
        }
    } else {
        Add-Log $log "CC-Switch is reachable with provider: $($cc.current_provider)."
    }

    return @{
        ok = $true
        log = $log
        status = Get-RepairStatus
    }
}

function Clear-Context413 {
    $log = [System.Collections.Generic.List[string]]::new()
    Add-Log $log "Stopping Codex Desktop before archiving sessions."
    Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force -ErrorAction SilentlyContinue

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
        Add-Log $log "Archived $moved session file(s) from today's folder."
    } else {
        Add-Log $log "Today's session folder was not found."
    }

    return @{
        ok = $true
        moved = $moved
        log = $log
        status = Get-RepairStatus
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

if (-not (Test-Path $pagePath)) {
    Write-Error "Missing web page: $pagePath"
    exit 1
}

# Kill any existing process on this port so we can bind
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

            if ($method -eq "GET" -and ($path -eq "/" -or $path -eq "/repair.html")) {
                Send-Response $client 200 "text/html" (Get-Content -LiteralPath $pagePath -Raw -Encoding UTF8)
            } elseif ($method -eq "GET" -and $path -eq "/api/status") {
                Send-Response $client 200 "application/json" (ConvertTo-ResultJson (Get-RepairStatus))
            } elseif ($method -eq "POST" -and $path -eq "/api/repair") {
                Send-Response $client 200 "application/json" (ConvertTo-ResultJson (Repair-Codex))
            } elseif ($method -eq "POST" -and $path -eq "/api/clear-413") {
                Send-Response $client 200 "application/json" (ConvertTo-ResultJson (Clear-Context413))
            } elseif ($method -eq "POST" -and $path -eq "/api/install-guard") {
                Send-Response $client 200 "application/json" (ConvertTo-ResultJson (Install-SandboxGuard))
            } elseif ($method -eq "POST" -and $path -eq "/api/install-launcher") {
                Send-Response $client 200 "application/json" (ConvertTo-ResultJson (Install-Launcher))
            } else {
                Send-Response $client 404 "application/json" '{"ok":false,"error":"not found"}'
            }
        } catch [System.Net.Sockets.SocketException] {
            break
        } catch {
            if ($client) {
                Send-Response $client 500 "application/json" (ConvertTo-ResultJson @{ ok = $false; error = $_.Exception.Message })
            }
        } finally {
            if ($client) { $client.Close() }
        }
    }
} finally {
    $listener.Stop()
}
