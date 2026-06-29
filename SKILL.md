---
# 算个文科生吧，联系方式WX：RabbitRobot2025
name: ljh-codex-desktop-loopback-repair-skill
description: Diagnose and repair Codex Desktop for Windows reconnecting, stream disconnected, 413 Payload Too Large, or local API proxy failures. Fixes AppContainer loopback isolation, Codex sandbox WFP port blocking, CC-Switch Live Takeover config conflicts, CC-Switch credential loss and crash recovery, sandbox users, and netsh portproxy cleanup. 修复 Windows 上 Codex Desktop 一直重连、流断开、413 请求体过大、本地 127.0.0.1 代理不可达等问题。优先使用 unelevated 沙箱策略，简单可靠。
---

# Codex Desktop Loopback Repair

## Core Model

Codex Desktop (Windows Store / MSIX) runs in a sandbox. When `sandbox = "elevated"`, the sandbox creates WFP firewall rules that block loopback TCP/UDP on ports `1-7896,7898-65535`, leaving only port `7897` reachable.

**CC-Switch Live Takeover** — if CC-Switch is in use, it automatically manages Codex's `config.toml`, writing `base_url = "http://127.0.0.1:15721/v1"` and `experimental_bearer_token = "PROXY_MANAGED"`. Any manual `base_url` change will be reverted by CC-Switch within seconds.

This creates a deadlock: elevated sandbox blocks 15721 → CC-Switch forces base_url to 15721 → Codex can't connect → Reconnecting loop.

**The fix**: use `sandbox = "unelevated"` so the main Codex process is not subject to WFP port filtering, allowing it to reach CC-Switch on 15721 directly. **However, Codex Desktop (v26.x) hardcodes port 7897 as its proxy port**, so a portproxy from 7897→15721 is still required even with unelevated sandbox. The unelevated sandbox simply ensures the portproxy itself isn't blocked by WFP.

**413 Payload Too Large** — when Codex conversation context grows too large (many tool calls, large file reads, long history), the request body can exceed CC-Switch's 10 MB limit. This is a separate issue from the sandbox deadlock — CC-Switch is reachable but rejects the oversized request. Fix: start a new Codex conversation to reset context, or clean up large sessions.

**CC-Switch credential loss** — CC-Switch can lose its provider credentials after a crash or restart, resulting in `"No credentials for provider: openai"` or `"current_provider":null`. CC-Switch auto-recovers from backup within 30-60 seconds. If not, it needs a manual restart.

**CRITICAL: CC-Switch Live Takeover writes `sandbox = "elevated"`** — This is the ROOT CAUSE of recurring failures. Every time CC-Switch restarts (crash, reboot, manual restart), it writes `sandbox = "elevated"` into `config.toml`, undoing any manual fix. The permanent solution requires a **sandbox guard** that monitors `config.toml` and auto-corrects sandbox back to `"unelevated"` whenever CC-Switch changes it.

**HTTP_PROXY kills CC-Switch outbound** — If `HTTP_PROXY` or `HTTPS_PROXY` environment variable is set to `http://127.0.0.1:XXXX`, CC-Switch routes ALL outbound API requests through that proxy. If nothing is listening there, CC-Switch fails with `"连接失败"`. Curl and browsers are unaffected (they don't auto-use these vars), making this very hard to diagnose. Fix: delete the env var.

**Upstream API server can be down** — CC-Switch can be perfectly healthy (running, valid provider, no credential errors) but ALL requests fail because the upstream API server (e.g. `llm4.slashrobot.top`) is unreachable. The `/status` endpoint shows 0% success rate with `"所有供应商都失败"` or `"请求超时"`. CC-Switch's `proxy_live_backup` table stores a snapshot of the last working config — on restart, CC-Switch restores auth tokens from this backup, overwriting manual database edits. Fix: switch to a working provider endpoint, or update both the provider's `settings_config` AND the `proxy_live_backup` in CC-Switch's SQLite database.

**Upstream credential expired (third-party proxy)** — CC-Switch is running and routing correctly, but the upstream API proxy service (e.g. `llm.slashrobot.top`) returns `HTTP 404: No active credentials for provider: openai`. This means the third-party proxy's own upstream credentials (OpenAI/Anthropic) have expired — the user's API key is valid on the proxy, but the proxy cannot forward requests. This is a **server-side issue, NOT fixable locally**. The API test through CC-Switch will return `upstream_status: HTTP 404` and `cause: No active credentials for provider: openai`. Fix: contact the proxy service admin to renew their upstream credentials, or switch to a different provider in CC-Switch GUI (e.g. configure a direct DeepSeek or other API key).

**Codex Provider missing upstream base_url** - CC-Switch can keep Codex Live Takeover pointing at the local proxy (`http://127.0.0.1:15721/v1`) while the current Codex provider row in `%USERPROFILE%\.cc-switch\cc-switch.db` loses its upstream `base_url` inside `providers.settings_config.config`. The visible error is usually `Codex Provider ... 缺少 base_url 配置` or `Codex Provider missing base_url`. Fix: stop Codex and CC-Switch, back up the database, restore a full provider config from `proxy_live_backup`, `settings.common_config_codex`, or `provider_endpoints`, preserve `auth`, set `commonConfigEnabled=false`, update BOTH `providers.settings_config` and `proxy_live_backup.original_config`, reset provider health, then restart CC-Switch and verify `/v1/responses`.

**Deep Recovery note** — After `DELETE FROM proxy_live_backup`, CC-Switch will show `current_provider: null` until Codex is started. CC-Switch needs Codex to launch to trigger Live Takeover, which recreates the backup and initializes the provider. Start order: 1) CC-Switch first, 2) then Codex.

## Quick Auto-Fix Flow

When invoked, follow this priority order. Stop at the first step that resolves the issue:

### Step 1: Stop Codex

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force
```

### Step 2: Diagnose

Run these and report findings:

```powershell
# A. Current sandbox mode and base_url
Get-Content "$env:USERPROFILE\.codex\config.toml" -ErrorAction SilentlyContinue | Select-String 'sandbox|base_url|experimental_bearer_token'

# B. CC-Switch status
curl.exe -s http://127.0.0.1:15721/status --max-time 5 2>$null | Select-Object -Last 1

# C. Loopback exemption
CheckNetIsolation LoopbackExempt -s | Select-String 'codex|openai'

# D. Portproxy
netsh interface portproxy show all

# E. Sandbox firewall rules (admin)
netsh advfirewall firewall show rule name="codex_sandbox_offline_block_loopback_tcp" 2>&1
```

**Interpretation table:**

| Finding | Meaning | Action |
|---|---|---|
| `sandbox = "elevated"` | WFP blocks 15721, deadlock with CC-Switch | Go to Step 3 (Strategy A) |
| `sandbox = "unelevated"` | WFP not blocking main process | Skip to Step 3e (portproxy check) |
| CC-Switch `/status` returns `"running":true` with valid provider | Backend is healthy | Skip to Step 5 |
| CC-Switch `/status` shows `"current_provider":null` | Provider lost | Go to Step 4a (CC-Switch Recovery) |
| CC-Switch `/status` shows `"No credentials"` in last_error | Credential loss after crash | Go to Step 4a (CC-Switch Recovery) |
| CC-Switch `/status` connection refused | CC-Switch not running | Go to Step 4b (Start CC-Switch) |
| CC-Switch `/status` shows `active_targets:[]` | No targets configured | Check CC-Switch GUI |
| `experimental_bearer_token = "PROXY_MANAGED"` | CC-Switch Live Takeover active | Do NOT change base_url; use Strategy A |
| Error contains `413 Payload Too Large` | Request body > 10 MB | Go to Step 6 (Context too large) |
| Loopback exemption missing | AppContainer isolation blocks loopback | Go to Step 3d |
| Portproxy `7897→15721` missing | Codex can't reach CC-Switch | Add portproxy (Step 3e) |
| Portproxy `7897→15721` exists | Essential routing in place | Keep it |
| CC-Switch reports `"连接失败"` but curl can reach upstream | `HTTP_PROXY` env var poisoning outbound | Go to Step 4d (Remove HTTP_PROXY) |
| CC-Switch `/status` valid provider but API test times out | Upstream API endpoint unreachable (server down) | Go to Step 4e (Upstream Endpoint Repair) |
| CC-Switch `/status` shows high fail rate, `"所有供应商都失败"` | Upstream endpoint dead or credentials invalid for that endpoint | Go to Step 4e (Upstream Endpoint Repair) |
| CC-Switch API test returns `upstream_status: HTTP 404` with `No active credentials for provider: openai` | Third-party proxy (e.g. `llm.slashrobot.top`) upstream credentials expired — server-side, NOT fixable locally | Contact proxy admin or switch provider in CC-Switch GUI |
| CC-Switch `/status` shows `current_provider: null` after deep recovery | Normal — `proxy_live_backup` was cleaned, needs Codex launch to trigger Live Takeover | Start Codex; CC-Switch will auto-recover provider within seconds |
| Error contains `缺少 base_url 配置` or `missing base_url` | Current CC-Switch Codex provider lost upstream `base_url` in SQLite | Go to Step 4f (Provider base_url Repair) |

### Step 3: Strategy A — Unelevated Sandbox (Primary Fix)

Apply when `sandbox = "elevated"` OR the deadlock is suspected.

#### 3a. Back Up Config

```powershell
Copy-Item "$env:USERPROFILE\.codex\config.toml" "$env:USERPROFILE\.codex\config.toml.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

#### 3b. Patch Config — Change ONLY sandbox mode

Change `sandbox = "elevated"` to `sandbox = "unelevated"` in `$env:USERPROFILE\.codex\config.toml`.

**CRITICAL**: Do NOT touch `base_url` or `experimental_bearer_token` — CC-Switch manages these. Keep ALL other settings intact.

```toml
[windows]
sandbox = "unelevated"   # was "elevated"
```

#### 3c. Add AppContainer Loopback Exemption (Admin)

Resolve the current package name dynamically (Codex updates change it):

```powershell
$pkg = (Get-AppxPackage -Name '*OpenAI*').PackageFullName
if ($pkg) { CheckNetIsolation LoopbackExempt -a -n="$pkg" }
```

#### 3d. Clean Sandbox State (Admin)

Delete firewall rules and sandbox users while Codex is stopped. Codex will recreate them on next launch, but with `sandbox = "unelevated"` they only apply to sandbox worker processes, not the main Codex process making API calls.

```powershell
# Delete sandbox firewall rules (may already be deleted — that's fine)
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_tcp" 2>$null
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_udp" 2>$null
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_outbound" 2>$null

# Delete sandbox users (Codex recreates on launch — expected)
net user CodexSandboxOffline /delete 2>$null
net user CodexSandboxOnline /delete 2>$null
```

#### 3e. Ensure Portproxy 7897→15721 (Essential)

Codex Desktop (v26.x) hardcodes port 7897 as its proxy port. Even with unelevated sandbox, the portproxy is required to forward 7897→15721 (where CC-Switch listens).

```powershell
# Delete old rule first (in case of corruption), then add fresh
netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1
netsh interface portproxy add v4tov4 listenport=7897 listenaddress=127.0.0.1 connectport=15721 connectaddress=127.0.0.1
```

### Step 4: CC-Switch Health Check

CC-Switch must be running with a valid provider before starting Codex.

#### 4a. CC-Switch Recovery (Provider/Credential Loss)

If CC-Switch `/status` shows `"current_provider":null` or `"No credentials for provider"`:

1. **Wait 30-60 seconds** — CC-Switch auto-recovers Live configuration from backup on startup. Check `/status` again.
2. **If still broken after waiting**, find and restart CC-Switch:

```powershell
# Find cc-switch.exe location
$path = (Get-Process cc-switch -ErrorAction SilentlyContinue | Select-Object -First 1).Path
if (-not $path) {
    $common = @(
        "F:\CC\cc-switch.exe",
        "$env:LOCALAPPDATA\com.ccswitch.desktop\cc-switch.exe",
        "C:\Program Files\CC-Switch\cc-switch.exe"
    )
    $path = $common | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($path) {
    Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    Start-Process $path -WindowStyle Hidden
    Write-Host "CC-Switch restarted from: $path"
    Write-Host "Wait 30s for auto-recovery, then check /status"
}
```

3. **If database was wiped** — Reconfigure provider and targets in CC-Switch GUI.

#### 4b. CC-Switch Not Running

If `/status` returns connection refused:

```powershell
# Find and start CC-Switch
$common = @(
    "F:\CC\cc-switch.exe",
    "$env:LOCALAPPDATA\com.ccswitch.desktop\cc-switch.exe",
    "C:\Program Files\CC-Switch\cc-switch.exe"
)
$path = $common | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($path) {
    Start-Process $path -WindowStyle Hidden
    Write-Host "CC-Switch started. Wait 10s before checking /status"
} else {
    Write-Host "ERROR: CC-Switch not found. Install CC-Switch first."
}
```

#### 4c. Verify CC-Switch Forwards Requests

```powershell
$body = @{
    model = "gpt-5.5"
    input = @(@{ role = "user"; content = "hi" })
    max_output_tokens = 10
} | ConvertTo-Json -Depth 5 -Compress

Invoke-RestMethod -Uri "http://127.0.0.1:15721/v1/responses" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 15 | ConvertTo-Json -Depth 5 -Compress
```

Expected: a JSON response with model output (e.g., `"Hi! How can I help?"`), NOT a `proxy_error`.

#### 4d. Remove HTTP_PROXY (Fixes CC-Switch Outbound)

If CC-Switch `/status` is healthy but API test fails with `"连接失败"` while `curl` directly to the upstream works, check for proxy env vars poisoning CC-Switch's outbound:

```powershell
# Check for proxy env vars
[Environment]::GetEnvironmentVariable('HTTP_PROXY', 'User')
[Environment]::GetEnvironmentVariable('HTTPS_PROXY', 'User')
```

If any are set to `http://127.0.0.1:XXXX`:

```powershell
# Remove them — they break CC-Switch's outbound connections
[Environment]::SetEnvironmentVariable('HTTP_PROXY', $null, 'User')
[Environment]::SetEnvironmentVariable('HTTPS_PROXY', $null, 'User')
[Environment]::SetEnvironmentVariable('http_proxy', $null, 'User')
[Environment]::SetEnvironmentVariable('https_proxy', $null, 'User')

# Also clear from current session
Remove-Item Env:HTTP_PROXY, Env:HTTPS_PROXY, Env:http_proxy, Env:https_proxy -ErrorAction SilentlyContinue

# Restart CC-Switch for changes to take effect
Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
$path = (Get-Process cc-switch -ErrorAction SilentlyContinue | Select-Object -First 1).Path
if (-not $path) { $path = @("F:\CC\cc-switch.exe", "$env:LOCALAPPDATA\com.ccswitch.desktop\cc-switch.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1 }
if ($path) { Start-Process $path -WindowStyle Hidden }
```

#### 4e. Upstream Endpoint Repair (Provider reachable but upstream API dead)

CC-Switch can be healthy (running, valid provider, no credential errors) but ALL requests fail because the upstream API server is unreachable. The `/status` endpoint may show 0% success rate with `"所有供应商都失败"` or `"请求超时"`.

**Diagnose upstream connectivity:**

```powershell
# 1. Check CC-Switch logs for the upstream URL being used
Get-Content "$env:USERPROFILE\.cc-switch\logs\cc-switch.log" -Tail 5 | Select-String '>>> 请求 URL'

# 2. Test the upstream URL directly
curl.exe -v --max-time 10 "https://<UPSTREAM_HOST>/v1/responses" -H "Content-Type: application/json" -d '{"model":"gpt-5.5","input":[{"role":"user","content":"hi"}],"max_output_tokens":10}'
```

**If the upstream URL is unreachable** (connection timeout, no route to host):

The CC-Switch database stores each provider's upstream endpoint. There are two fix strategies:

**Option 1 — Switch to another working provider** (preferred):
1. Open CC-Switch GUI
2. Switch to a provider with a working endpoint (e.g., switch from `llm4` to `llm2` endpoint)
3. Or select a different provider entirely and verify it works

**Option 2 — Repair the database directly** (when GUI switching not available):
```powershell
# Stop CC-Switch first
Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2

# Find CC-Switch database
$db = "$env:USERPROFILE\.cc-switch\cc-switch.db"

# Check current provider config and upstream URL
sqlite3 $db "SELECT id, name, settings_config FROM providers WHERE app_type='codex' AND is_current=1;"

# List all available provider endpoints
sqlite3 $db "SELECT * FROM provider_endpoints;"

# If a working endpoint exists for another provider, switch is_current:
sqlite3 $db "UPDATE providers SET is_current = 1 WHERE id = 'default' AND app_type = 'codex';"
sqlite3 $db "UPDATE providers SET is_current = 0 WHERE id = '<DEAD_PROVIDER_ID>' AND app_type = 'codex';"

# ALSO update proxy_live_backup — CC-Switch restores from this on restart
# Check current backup:
sqlite3 $db "SELECT substr(original_config, 1, 200) FROM proxy_live_backup WHERE app_type='codex';"

# Update the backup's endpoint URL and API key to a working combination:
sqlite3 $db "UPDATE proxy_live_backup SET original_config = replace(replace(original_config, '<DEAD_HOST>', '<WORKING_HOST>'), '<OLD_API_KEY>', '<WORKING_API_KEY>') WHERE app_type = 'codex';"

# Start CC-Switch
Start-Process "F:\CC\cc-switch.exe" -WindowStyle Hidden
Start-Sleep 5
curl.exe -s http://127.0.0.1:15721/status
```

**Key insight**: CC-Switch's `proxy_live_backup` table stores a snapshot of the last working config. On restart, CC-Switch restores auth tokens from this backup, overwriting any manual database edits. Both the provider's `settings_config` AND the `proxy_live_backup` must be updated together.

**Prevention**: If an upstream API server is known to be unstable, configure multiple provider endpoints in CC-Switch (via its GUI) so failover can happen automatically.

#### 4f. Provider base_url Repair (Codex Provider missing base_url)

Use this when Codex reports `Codex Provider ... 缺少 base_url 配置` / `missing base_url`. This is not the local Codex `base_url`; it means the selected CC-Switch Codex provider lost the upstream API `base_url` inside the CC-Switch SQLite database.

Run from the skill directory while Codex is stopped. Stop CC-Switch before the write so SQLite is not locked:

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force
python .\scripts\fix_codex_provider_base_url.py --db "$env:USERPROFILE\.cc-switch\cc-switch.db"
```

If the helper cannot infer the upstream URL from `proxy_live_backup`, `settings.common_config_codex`, or `provider_endpoints`, pass it explicitly:

```powershell
python .\scripts\fix_codex_provider_base_url.py --db "$env:USERPROFILE\.cc-switch\cc-switch.db" --base-url "https://YOUR-UPSTREAM.example/v1" --model "cx/gpt-5.5"
```

What the helper does:
- Backs up `cc-switch.db` to `cc-switch.db.bak-fix-base-url-YYYYMMDD-HHMMSS`
- Preserves provider `auth`
- Restores a full `[model_providers.custom]` config with `wire_api = "responses"` and upstream `base_url`
- Sets `commonConfigEnabled=false`, `endpointAutoSelect=true`, and `apiFormat=openai_responses`
- Updates BOTH `providers.settings_config` and `proxy_live_backup.original_config`
- Deletes stale `provider_health` for the repaired provider

After the repair, start CC-Switch, wait a few seconds, then verify forwarding:

```powershell
$body = @{ model = "cx/gpt-5.5"; input = @(@{ role = "user"; content = "hi" }); max_output_tokens = 8 } | ConvertTo-Json -Depth 5 -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:15721/v1/responses" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 20
```

Expected: HTTP 200 model response, `/status` success rate improving, and no `Codex Provider ... missing base_url` proxy error.

### Step 5: Start Codex In Order

1. **First**: CC-Switch must be running with a valid provider (verified in Step 4)
2. **Then**: Start Codex Desktop
3. **Wait**: CC-Switch Live Takeover will update Codex config within seconds, writing `base_url` back to `15721` and `experimental_bearer_token = "PROXY_MANAGED"` — this is expected and correct
4. **Verify**: Codex should connect without reconnecting errors

### Step 5b: Install Sandbox Guard (Permanent Fix)

CC-Switch Live Takeover will revert `sandbox` back to `"elevated"` every time it restarts. The guard script prevents this permanently.

**Install the guard:**

```powershell
# Copy guard scripts to .codex
Copy-Item "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts\sandbox-guard.ps1" "$env:USERPROFILE\.codex\sandbox-guard.ps1" -Force
Copy-Item "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts\sandbox-guard.vbs" "$env:USERPROFILE\.codex\sandbox-guard.vbs" -Force

# Add to Startup folder (runs hidden at every login)
Copy-Item "$env:USERPROFILE\.codex\sandbox-guard.vbs" "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\sandbox-guard.vbs" -Force

# Start the guard now
Start-Process powershell.exe -ArgumentList '-WindowStyle Hidden -ExecutionPolicy Bypass -File', "$env:USERPROFILE\.codex\sandbox-guard.ps1" -WindowStyle Hidden

Write-Host "Sandbox Guard installed and running"
```

**How it works:**
- Waits 30 seconds after startup (let CC-Switch finish its writes)
- Checks `config.toml` every 10 seconds
- If `sandbox = "elevated"` is detected, auto-fixes to `"unelevated"`
- Logs all fixes to `$env:USERPROFILE\.codex\sandbox-guard.log`
- Runs hidden at every Windows login via Startup folder

**Alternative: One-click launcher (`start-codex.bat`)**

```powershell
Copy-Item "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts\start-codex.bat" "$env:USERPROFILE\.codex\start-codex.bat" -Force
```

This batch file starts everything in correct order: CC-Switch → wait → fix sandbox → check sessions → launch Codex. Use it instead of clicking the Codex icon.

### Step 6: 413 Payload Too Large Fix

When Codex reports `413 Payload Too Large: Request body too large. Maximum allowed: 10 MB`:

**Root cause**: The conversation context (system prompt + tool definitions + conversation history + file contents) exceeds the upstream API's 10 MB request body limit. The 413 comes from the upstream provider (not CC-Switch itself) — check CC-Switch `/status` for `"上游错误 (状态码 413)"`. CC-Switch IS reachable and working; the request body is just too large for the model API.

**Auto-Fix**:

```powershell
# 1. Stop Codex
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force

# 2. Find the culprit — sessions over 5 MB
Write-Host "=== Large sessions ==="
Get-ChildItem "$env:USERPROFILE\.codex\sessions" -Recurse -Filter "*.jsonl" | Where-Object { $_.Length -gt 5MB } | Sort-Object Length -Descending | Select-Object Length, Name

# 3. Archive today's sessions to clear context
$today = Get-Date -Format "yyyy\MM\dd"
$src = "$env:USERPROFILE\.codex\sessions\$today"
$archive = "$env:USERPROFILE\.codex\archived_sessions"
if (Test-Path $src) {
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    Get-ChildItem $src -Filter "*.jsonl" -File | Move-Item -Destination $archive -Force
    Write-Host "Archived today's sessions — context cleared"
}

# 4. Verify empty
Get-ChildItem "$env:USERPROFILE\.codex\sessions\$today" -ErrorAction SilentlyContinue
```

After archiving, start Codex fresh. The new conversation will have near-zero context.

**Prevention**: If a conversation accumulates many tool calls or reads large files, context can exceed 10 MB quickly. Start a new conversation periodically for long-running tasks.

Note: The 413 error is NOT a sandbox/network issue. If CC-Switch `/status` returns healthy and test requests work, but Codex shows 413, the fix is archiving sessions — not changing sandbox or network settings.

## Strategy B: Elevated Sandbox + Portproxy (Legacy)

Use only when `sandbox = "elevated"` is strictly required AND CC-Switch Live Takeover is disabled.

**WARNING**: CC-Switch Live Takeover WILL revert `base_url` back to 15721, making this strategy fail. Only use if `experimental_bearer_token = "PROXY_MANAGED"` is NOT in config.

### B1. Stop Codex Desktop

Same as Step 1.

### B2. Back Up And Patch Config

Set both sandbox mode AND base_url:

```toml
[windows]
sandbox = "elevated"

[model_providers.custom]
base_url = "http://127.0.0.1:7897/v1"    # 7897 is the only sandbox-allowed port
```

### B3. Portproxy Setup (Admin)

```powershell
netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1
netsh interface portproxy add v4tov4 listenport=7897 listenaddress=127.0.0.1 connectport=15721 connectaddress=127.0.0.1
```

### B4. AppContainer Loopback Exemption (Admin)

Same as Step 3c.

### B5. Firewall And Sandbox Cleanup (Admin)

```powershell
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_tcp" 2>$null
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_udp" 2>$null
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_outbound" 2>$null
netsh advfirewall firewall add rule name="Allow Codex Proxy 7897" dir=in action=allow protocol=tcp localport=7897
net user CodexSandboxOffline /delete 2>$null
net user CodexSandboxOnline /delete 2>$null
```

### B6. Start In Order

1. CC-Switch on 15721
2. Verify portproxy: `netsh interface portproxy show all`
3. Codex Desktop

## Verification Checklist

After repair, verify all of these:

```powershell
# 1. Config is correct
Write-Host "=== Config ===" 
Get-Content "$env:USERPROFILE\.codex\config.toml" | Select-String 'sandbox|base_url'

# 2. CC-Switch healthy
Write-Host "=== CC-Switch ===" 
curl.exe -s http://127.0.0.1:15721/status --max-time 5 | Select-Object -Last 1

# 3. CC-Switch forwards requests
Write-Host "=== API Test ===" 
$body = @{
    model = "gpt-5.5"
    input = @(@{ role = "user"; content = "hi" })
    max_output_tokens = 10
} | ConvertTo-Json -Depth 5 -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:15721/v1/responses" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 15 | ConvertTo-Json -Depth 5 -Compress

# 4. Loopback exemption exists
Write-Host "=== Loopback ===" 
CheckNetIsolation LoopbackExempt -s | Select-String 'codex'

# 5. Portproxy is present (REQUIRED — Codex uses port 7897)
Write-Host "=== Portproxy ===" 
netsh interface portproxy show all
```

### Success Criteria

| Check | Expected |
|---|---|
| sandbox | `"unelevated"` |
| base_url | `http://127.0.0.1:15721/v1` |
| CC-Switch `/status` | `"running":true`, provider not null |
| API test | Model response via 7897 or 15721, no `proxy_error` |
| Loopback | Codex package listed |
| Portproxy | `7897 → 15721` present |
| Codex behavior | No reconnecting, no 413 |

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Codex reconnecting, sandbox=elevated | WFP blocking 15721 | Strategy A: switch to unelevated |
| Codex reconnecting, sandbox=unelevated | Portproxy 7897→15721 missing, or CC-Switch provider null/down | Ensure portproxy (Step 3e); if still fails, CC-Switch Recovery (Step 4a) |
| `413 Payload Too Large` | Conversation context > 10 MB | Start new conversation (Step 6) |
| `413` persists after new chat | Old sessions loading large context | Archive old sessions (Step 6) |
| CC-Switch `/status` works but API fails | CC-Switch provider has no active targets | Check CC-Switch GUI → provider → targets |
| CC-Switch `/status` connection refused | CC-Switch not running | Start CC-Switch (Step 4b) |
| CC-Switch `/status` shows `"No credentials"` | Credential loss after crash | Wait 30s for auto-recovery, or restart (Step 4a) |
| CC-Switch `/status` shows `"current_provider":null` | Provider configuration lost | Wait 30-60s; if not recovered, restart CC-Switch |
| `base_url` keeps reverting to 15721 | CC-Switch Live Takeover active | Normal behavior; use Strategy A |
| Codex updated, loopback exemption lost | PackageFullName changed | Re-run Step 3c with new package name |
| Portproxy disappeared after reboot | IP Helper service issue | Re-run Step 3e; check IP Helper service |
| Sandbox rules keep reappearing | Codex recreates on launch | Expected; clean while Codex is stopped |
| Codex was working, suddenly reconnects | CC-Switch crashed and lost provider | Check CC-Switch `/status`; run Step 4a |
| CC-Switch shows provider but `active_targets:[]` | Targets not loaded or configured | Wait 30s; if still empty, check CC-Switch GUI |
| `error sending request for url (http://127.0.0.1:7897/...)` | Portproxy deleted or IP Helper stopped | Re-run Step 3e; check `services.msc` → IP Helper |
| Codex uses `codex/cx/gpt-5.5` as model name | CC-Switch rewrote model field | Normal — CC-Switch prefixes provider-scoped model names |
| All requests fail with 400 "No credentials" | CC-Switch lost API keys | CC-Switch Recovery (Step 4a); if persistent, re-enter keys in CC-Switch GUI |
| CC-Switch restarted but `/status` still shows errors | CC-Switch needs more time for auto-recovery, or `proxy_live_backup` has stale config | Wait 30-60s; if persistent, check upstream endpoint health (Step 4e) |
| CC-Switch provider valid but API requests time out | Upstream API server unreachable (server down, DNS failure) | Run Step 4e — check logs, test direct connectivity, switch endpoint or patch database |
| CC-Switch shows `"所有供应商都失败"` with 0% success | Upstream endpoint unreachable or credentials invalid | Run Step 4e — update endpoint URL and API key in database + backup |
| Requests fail after updating provider config in DB | `proxy_live_backup` overwrote manual changes on restart | Must update BOTH provider settings_config AND proxy_live_backup (Step 4e) |
| Codex reports `Codex Provider ... 缺少 base_url 配置` | Provider `settings_config.config` was stripped and lacks upstream `base_url` | Run Step 4f; update provider config and `proxy_live_backup` together |

## CC-Switch Deep Recovery

If standard recovery (Step 4a) doesn't work:

### Check CC-Switch Logs

Look for recovery messages in CC-Switch console output:
- `Live 配置已从备份恢复` — Live config recovered from backup (good)
- `No credentials for provider` — API keys missing
- `failed to connect to upstream` — Network/DNS issue on CC-Switch side

### Full CC-Switch Restart Cycle

```powershell
# 1. Kill CC-Switch
Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 3

# 2. Find and start CC-Switch
$path = @("F:\CC\cc-switch.exe", "$env:LOCALAPPDATA\com.ccswitch.desktop\cc-switch.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($path) {
    Start-Process $path -WindowStyle Hidden
    Write-Host "Started CC-Switch from $path"
}

# 3. Wait for full initialization
Start-Sleep 10

# 4. Check status
curl.exe -s http://127.0.0.1:15721/status --max-time 5

# 5. If still broken after 60s, CC-Switch needs manual reconfiguration via its GUI
```

### Provider Lost — Manual Recovery

If CC-Switch repeatedly shows `"current_provider":null` after restarts:
1. Open CC-Switch GUI
2. Check provider configuration — re-enter API keys if needed
3. Verify provider targets are configured and enabled
4. Check that the Live configuration toggle is ON

## Safety Notes

- Always stop Codex Desktop before cleaning sandbox users or firewall/WFP state
- Always back up `%USERPROFILE%\.codex\config.toml` before edits
- Keep unrelated Codex config intact — only change `sandbox` mode
- Resolve the current Codex `PackageFullName` dynamically — never hard-code it
- Delete only known Codex sandbox rules or exact sandbox user names
- Codex Desktop (v26.x) hardcodes port 7897 as proxy port — portproxy 7897→15721 is essential even with unelevated sandbox
- CC-Switch Live Takeover writes `base_url = "http://127.0.0.1:15721/v1"` and `experimental_bearer_token = "PROXY_MANAGED"` — this is NORMAL, don't revert it
- Some repair commands require Administrator PowerShell
- Codex Store updates change `PackageFullName` — re-run loopback exemption after updates
- Every Codex launch recreates sandbox users and firewall rules — `unelevated` mode ensures they don't block the main process

## Quick Compact Repair (Most Common Case)

When Codex shows "Reconnecting" / "stream disconnected" on Windows with CC-Switch, the root cause is almost always: **CC-Switch restarted → wrote `sandbox = "elevated"` → WFP blocks port 15721 → deadlock**.

Run these three steps as Administrator PowerShell:

```powershell
# Step 1: Stop Codex
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force

# Step 2: Fix sandbox to unelevated (backup first)
$config = "$env:USERPROFILE\.codex\config.toml"
Copy-Item $config "$config.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
$text = Get-Content $config -Raw -Encoding UTF8
$text = $text -replace 'sandbox\s*=\s*"[^\"]+"', 'sandbox = "unelevated"'
[IO.File]::WriteAllText($config, $text, [Text.UTF8Encoding]::new($false))

# Step 3: Loopback exemption + Portproxy
$pkg = (Get-AppxPackage -Name '*OpenAI*').PackageFullName
if ($pkg) { CheckNetIsolation LoopbackExempt -a -n="$pkg" }
netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1
netsh interface portproxy add v4tov4 listenport=7897 listenaddress=127.0.0.1 connectport=15721 connectaddress=127.0.0.1
```

Then install the Sandbox Guard to prevent recurrence:

```powershell
$skillScripts = "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts"
if (-not (Test-Path $skillScripts)) {
    $skillScripts = "E:\Codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts"
}
Copy-Item "$skillScripts\sandbox-guard.ps1" "$env:USERPROFILE\.codex\sandbox-guard.ps1" -Force
Copy-Item "$skillScripts\sandbox-guard.vbs" "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\sandbox-guard.vbs" -Force
Start-Process powershell.exe -ArgumentList '-WindowStyle Hidden -ExecutionPolicy Bypass -File', "$env:USERPROFILE\.codex\sandbox-guard.ps1" -WindowStyle Hidden
```

After repair, verify:

```powershell
# Check config
Get-Content "$env:USERPROFILE\.codex\config.toml" | Select-String 'sandbox|base_url'
# Check CC-Switch
curl.exe -s http://127.0.0.1:15721/status --max-time 5
# Check portproxy
netsh interface portproxy show all
```

Expected: `sandbox = "unelevated"`, CC-Switch running with valid provider, portproxy `7897→15721` present.

### If CC-Switch API test shows upstream credential expired

This is a server-side issue on the third-party proxy (e.g. `llm.slashrobot.top`), NOT fixable locally:

```
upstream_status: HTTP 404
cause: No active credentials for provider: openai
```

The user must contact the proxy service admin or switch to a different provider in CC-Switch GUI.

### If CC-Switch shows provider=null after deep recovery

This is normal — start Codex Desktop and CC-Switch Live Takeover will auto-initialize the provider within seconds.
