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

**The fix**: use `sandbox = "unelevated"` so the main Codex process is not subject to WFP port filtering, allowing it to reach CC-Switch on 15721 directly. No portproxy needed.

**413 Payload Too Large** — when Codex conversation context grows too large (many tool calls, large file reads, long history), the request body can exceed CC-Switch's 10 MB limit. This is a separate issue from the sandbox deadlock — CC-Switch is reachable but rejects the oversized request. Fix: start a new Codex conversation to reset context, or clean up large sessions.

**CC-Switch credential loss** — CC-Switch can lose its provider credentials after a crash or restart, resulting in `"No credentials for provider: openai"` or `"current_provider":null`. CC-Switch auto-recovers from backup within 30-60 seconds. If not, it needs a manual restart.

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
| `sandbox = "unelevated"` | WFP not blocking main process | Skip to Step 4 (CC-Switch check) |
| CC-Switch `/status` returns `"running":true` with valid provider | Backend is healthy | Skip to Step 5 |
| CC-Switch `/status` shows `"current_provider":null` | Provider lost | Go to Step 4a (CC-Switch Recovery) |
| CC-Switch `/status` shows `"No credentials"` in last_error | Credential loss after crash | Go to Step 4a (CC-Switch Recovery) |
| CC-Switch `/status` connection refused | CC-Switch not running | Go to Step 4b (Start CC-Switch) |
| CC-Switch `/status` shows `active_targets:[]` | No targets configured | Check CC-Switch GUI |
| `experimental_bearer_token = "PROXY_MANAGED"` | CC-Switch Live Takeover active | Do NOT change base_url; use Strategy A |
| Error contains `413 Payload Too Large` | Request body > 10 MB | Go to Step 6 (Context too large) |
| Loopback exemption missing | AppContainer isolation blocks loopback | Go to Step 3d |
| Portproxy `7897→15721` exists | Legacy Strategy B artifact | Can be cleaned (Step 3e) |

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

#### 3e. Clean Up Legacy Portproxy (Optional)

If `netsh interface portproxy show all` shows `7897 → 15721`, this is a legacy Strategy B artifact. With unelevated sandbox, Codex connects directly to 15721, so portproxy is unnecessary. Remove it to keep things clean:

```powershell
netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1
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
curl.exe -s -X POST http://127.0.0.1:15721/v1/responses -H "Content-Type: application/json" -d '{"model":"gpt-5.5","input":[{"role":"user","content":"hi"}],"max_output_tokens":10}' --max-time 15 2>$null | Select-Object -Last 1
```

Expected: a JSON response with model output (e.g., `"Hi! How can I help?"`), NOT a `proxy_error`.

### Step 5: Start Codex In Order

1. **First**: CC-Switch must be running with a valid provider (verified in Step 4)
2. **Then**: Start Codex Desktop
3. **Wait**: CC-Switch Live Takeover will update Codex config within seconds, writing `base_url` back to `15721` and `experimental_bearer_token = "PROXY_MANAGED"` — this is expected and correct
4. **Verify**: Codex should connect without reconnecting errors

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
if (Test-Path $src) {
    Move-Item "$src\*.jsonl" "$env:USERPROFILE\.codex\archived_sessions\" -Force
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
curl.exe -s -X POST http://127.0.0.1:15721/v1/responses -H "Content-Type: application/json" -d '{"model":"gpt-5.5","input":[{"role":"user","content":"hi"}],"max_output_tokens":10}' --max-time 15 | Select-Object -Last 1

# 4. Loopback exemption exists
Write-Host "=== Loopback ===" 
CheckNetIsolation LoopbackExempt -s | Select-String 'codex'

# 5. No portproxy (Strategy A) or portproxy present (Strategy B)
Write-Host "=== Portproxy ===" 
netsh interface portproxy show all
```

### Success Criteria

| Check | Strategy A Expected | Strategy B Expected |
|---|---|---|
| sandbox | `"unelevated"` | `"elevated"` |
| base_url | `http://127.0.0.1:15721/v1` | `http://127.0.0.1:7897/v1` |
| CC-Switch `/status` | `"running":true`, provider not null | Same |
| API test | Model response, no `proxy_error` | Same (via 7897) |
| Loopback | Codex package listed | Same |
| Portproxy | Empty (or cleaned) | `7897 → 15721` |
| Codex behavior | No reconnecting, no 413 | Same |

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Codex reconnecting, sandbox=elevated | WFP blocking 15721 | Strategy A: switch to unelevated |
| Codex reconnecting, sandbox=unelevated | CC-Switch provider null or down | CC-Switch Recovery (Step 4a) |
| `413 Payload Too Large` | Conversation context > 10 MB | Start new conversation (Step 6) |
| `413` persists after new chat | Old sessions loading large context | Archive old sessions (Step 6) |
| CC-Switch `/status` works but API fails | CC-Switch provider has no active targets | Check CC-Switch GUI → provider → targets |
| CC-Switch `/status` connection refused | CC-Switch not running | Start CC-Switch (Step 4b) |
| CC-Switch `/status` shows `"No credentials"` | Credential loss after crash | Wait 30s for auto-recovery, or restart (Step 4a) |
| CC-Switch `/status` shows `"current_provider":null` | Provider configuration lost | Wait 30-60s; if not recovered, restart CC-Switch |
| `base_url` keeps reverting to 15721 | CC-Switch Live Takeover active | Normal behavior; use Strategy A |
| Codex updated, loopback exemption lost | PackageFullName changed | Re-run Step 3c with new package name |
| Portproxy disappeared after reboot | IP Helper service issue | Re-run B3; check IP Helper service |
| Sandbox rules keep reappearing | Codex recreates on launch | Expected; clean while Codex is stopped |
| Codex was working, suddenly reconnects | CC-Switch crashed and lost provider | Check CC-Switch `/status`; run Step 4a |
| CC-Switch shows provider but `active_targets:[]` | Targets not loaded or configured | Wait 30s; if still empty, check CC-Switch GUI |
| `error sending request for url (http://127.0.0.1:7897/...)` | Portproxy deleted or IP Helper stopped | Re-run B3; check `services.msc` → IP Helper |
| Codex uses `codex/cx/gpt-5.5` as model name | CC-Switch rewrote model field | Normal — CC-Switch prefixes provider-scoped model names |
| All requests fail with 400 "No credentials" | CC-Switch lost API keys | CC-Switch Recovery (Step 4a); if persistent, re-enter keys in CC-Switch GUI |
| CC-Switch restarted but `/status` still shows errors | CC-Switch needs more time for auto-recovery | Wait 30-60s, re-check; CC-Switch recovers Live config from backup |

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
- Do NOT fight CC-Switch's `base_url` — use Strategy A instead
- CC-Switch Live Takeover writes `base_url = "http://127.0.0.1:15721/v1"` and `experimental_bearer_token = "PROXY_MANAGED"` — this is NORMAL, don't revert it
- Some repair commands require Administrator PowerShell
- Codex Store updates change `PackageFullName` — re-run loopback exemption after updates
- Every Codex launch recreates sandbox users and firewall rules — `unelevated` mode ensures they don't block the main process
