---
name: ljh-codex-desktop-loopback-repair-skill
description: Diagnose and repair Codex Desktop for Windows reconnecting, stream disconnected, or local API proxy failures. Fixes AppContainer loopback isolation, Codex sandbox WFP port blocking, CC-Switch Live Takeover config conflicts, sandbox users, and netsh portproxy issues. 修复 Windows 上 Codex Desktop 一直重连、流断开、本地 127.0.0.1 代理不可达等问题。优先使用 unelevated 沙箱策略，简单可靠。
---

# Codex Desktop Loopback Repair

## Core Model

Codex Desktop (Windows Store / MSIX) runs in a sandbox. When `sandbox = "elevated"`, the sandbox creates WFP firewall rules that block loopback TCP/UDP on ports `1-7896,7898-65535`, leaving only port `7897` reachable.

**CC-Switch Live Takeover** — if CC-Switch is in use, it automatically manages Codex's `config.toml`, writing `base_url = "http://127.0.0.1:15721/v1"` and `experimental_bearer_token = "PROXY_MANAGED"`. Any manual `base_url` change will be reverted by CC-Switch within seconds.

This creates a deadlock: elevated sandbox blocks 15721 → CC-Switch forces base_url to 15721 → Codex can't connect.

**The fix**: use `sandbox = "unelevated"` so the main Codex process is not subject to WFP port filtering, allowing it to reach CC-Switch on 15721 directly. No portproxy needed.

## Quick Diagnosis

Run these and report findings before deciding the strategy.

Non-admin checks (run first):

```powershell
# 1. Current config
Get-Content "$env:USERPROFILE\.codex\config.toml" -ErrorAction SilentlyContinue | Select-String 'sandbox|base_url'

# 2. WFP port restrictions in sandbox log
Get-Content "$env:USERPROFILE\.codex\.sandbox\sandbox.log" -ErrorAction SilentlyContinue | Select-String 'RemotePorts'

# 3. CC-Switch status
curl.exe http://127.0.0.1:15721/status --max-time 5 2>$null | Select-Object -Last 1

# 4. Codex package identity
Get-AppxPackage -Name '*OpenAI*' | Select-Object Name, PackageFullName
```

Admin checks (require elevated PowerShell):

```powershell
# 5. Loopback exemptions
CheckNetIsolation LoopbackExempt -s | Select-String 'codex|openai'

# 6. Sandbox firewall rules
netsh advfirewall firewall show rule name="codex_sandbox_offline_block_loopback_tcp"
netsh advfirewall firewall show rule name="codex_sandbox_offline_block_outbound"

# 7. Sandbox users exist?
net user CodexSandboxOffline 2>&1 | Select-Object -First 1
net user CodexSandboxOnline 2>&1 | Select-Object -First 1

# 8. Existing portproxy
netsh interface portproxy show all
```

**Interpreting results:**

| Finding | Meaning |
| --- | --- |
| `sandbox = "elevated"` | WFP rules active — main fix target |
| `sandbox = "unelevated"` | WFP rules exist but don't block main process |
| `RemotePorts=1-7896,7898-65535` | Only port 7897 is allowed through sandbox |
| CC-Switch `/status` returns `"running":true` | Backend proxy is alive |
| CC-Switch `/status` shows `"current_provider":null` | Provider lost — need CC-Switch recovery |
| CC-Switch `/status` returns connection refused | CC-Switch is down — start it first |
| `experimental_bearer_token = "PROXY_MANAGED"` in config | CC-Switch Live Takeover is active |

## Repair Strategy Selector

After diagnosis, pick one:

### Strategy A: `sandbox = "unelevated"` (Recommended)

Use when CC-Switch Live Takeover is active (config has `PROXY_MANAGED`). Simple, no portproxy needed, no conflict with CC-Switch.

- Pro: CC-Switch keeps managing base_url, no conflict
- Pro: No portproxy to maintain
- Con: slightly reduced sandbox security (read-ACL-only mode)

### Strategy B: `sandbox = "elevated"` + Portproxy (Legacy)

Use only when elevated sandbox is strictly required AND CC-Switch Live Takeover is NOT active.

- Con: CC-Switch will fight your base_url changes
- Con: Portproxy must survive reboots and IP Helper service restarts
- Con: More admin commands needed

## Strategy A: Unelevated Sandbox Repair

### A1. Stop Codex Desktop

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force
```

### A2. Back Up And Patch Config

```powershell
Copy-Item "$env:USERPROFILE\.codex\config.toml" "$env:USERPROFILE\.codex\config.toml.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

Change ONLY the sandbox mode. Do NOT touch base_url — CC-Switch manages it:

```toml
[windows]
sandbox = "unelevated"   # was "elevated"
```

Keep ALL other settings intact, especially `base_url` and `experimental_bearer_token` that CC-Switch wrote.

### A3. Ensure CC-Switch Is Running And Has Provider

```powershell
$status = curl.exe http://127.0.0.1:15721/status --max-time 5 2>$null | Select-Object -Last 1
Write-Host $status
```

If CC-Switch is not running, start it (path varies, common locations):
- `%LOCALAPPDATA%\com.ccswitch.desktop\cc-switch.exe`
- Custom install path like `F:\CC\cc-switch.exe`

If status shows `"current_provider":null`, CC-Switch needs to recover its Live configuration. Try:
1. Wait 30 seconds — CC-Switch auto-recovers from backup on startup
2. If still null after waiting, CC-Switch may need manual reconfiguration via its GUI

Verify CC-Switch can forward requests:

```powershell
curl.exe -X POST http://127.0.0.1:15721/v1/responses -H "Content-Type: application/json" -d '{"model":"gpt-5.5","input":[{"role":"user","content":"hi"}],"max_output_tokens":10}' --max-time 15 2>$null | Select-Object -Last 1
```

Expected: a JSON response with model output, NOT a `proxy_error`.

### A4. Add AppContainer Loopback Exemption *(Admin)*

Resolve the current package name dynamically (Codex updates change it):

```powershell
$pkg = (Get-AppxPackage -Name '*OpenAI*').PackageFullName
if ($pkg) { CheckNetIsolation LoopbackExempt -a -n="$pkg" }
```

### A5. Clean Sandbox State *(Admin)*

Delete firewall rules and sandbox users while Codex is stopped. Codex will recreate them on next launch, but with `sandbox = "unelevated"` they only apply to sandbox worker processes, not the main Codex process making API calls.

```powershell
# Delete sandbox firewall rules
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_tcp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_udp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_outbound"

# Delete sandbox users (Codex recreates them on launch — expected)
net user CodexSandboxOffline /delete
net user CodexSandboxOnline /delete
```

### A6. Start In Order

1. **First**: CC-Switch must be running with a valid provider (verified in A3)
2. **Then**: Start Codex Desktop
3. **Wait**: CC-Switch Live Takeover will update Codex config within seconds, writing `base_url` back to `15721` and `experimental_bearer_token = "PROXY_MANAGED"` — this is expected and correct
4. **Verify**: Codex should connect without reconnecting errors

## Strategy B: Elevated Sandbox + Portproxy (Legacy)

Use only when `sandbox = "elevated"` is mandatory AND CC-Switch Live Takeover is disabled.

### B1. Stop Codex Desktop

Same as A1.

### B2. Back Up And Patch Config

```powershell
Copy-Item "$env:USERPROFILE\.codex\config.toml" "$env:USERPROFILE\.codex\config.toml.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

Set both sandbox mode AND base_url:

```toml
[windows]
sandbox = "elevated"

[model_providers.custom]
base_url = "http://127.0.0.1:7897/v1"    # 7897 is the only sandbox-allowed port
```

**WARNING**: If CC-Switch Live Takeover is active (`experimental_bearer_token = "PROXY_MANAGED"`), it WILL revert `base_url` back to 15721. Strategy B is incompatible with CC-Switch Live Takeover.

### B3. Portproxy Setup

```powershell
netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1
netsh interface portproxy add v4tov4 listenport=7897 listenaddress=127.0.0.1 connectport=15721 connectaddress=127.0.0.1
```

### B4. AppContainer Loopback Exemption *(Admin)*

Same as A4.

### B5. Firewall And Sandbox Cleanup *(Admin)*

```powershell
# Remove sandbox block rules
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_tcp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_udp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_outbound"

# Allow Codex-facing port
netsh advfirewall firewall add rule name="Allow Codex Proxy 7897" dir=in action=allow protocol=tcp localport=7897

# Delete sandbox users
net user CodexSandboxOffline /delete
net user CodexSandboxOnline /delete
```

### B6. Start In Order

1. CC-Switch on 15721
2. Verify portproxy: `netsh interface portproxy show all`
3. Codex Desktop

## CC-Switch Recovery

If CC-Switch shows `"current_provider":null` after restart:

1. **Wait** — CC-Switch auto-recovers Live configuration from backup within 30-60 seconds
2. **Check logs** — Look for `Live 配置已从备份恢复` in CC-Switch console output
3. **Manual restart** — If auto-recovery doesn't happen, find and restart:
   ```powershell
   # Find cc-switch.exe location
   $path = (Get-Process cc-switch -ErrorAction SilentlyContinue | Select-Object -First 1).Path
   if (-not $path) {
       # Try common locations
       $common = @(
           "$env:LOCALAPPDATA\com.ccswitch.desktop\cc-switch.exe",
           "F:\CC\cc-switch.exe",
           "C:\Program Files\CC-Switch\cc-switch.exe"
       )
       $path = $common | Where-Object { Test-Path $_ } | Select-Object -First 1
   }
   if ($path) {
       Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force
       Start-Sleep 2
       Start-Process $path -WindowStyle Hidden
   }
   ```
4. **If database was wiped** — Reconfigure provider and targets in CC-Switch GUI

## Verification

### Strategy A Verification

```powershell
# 1. CC-Switch accepts requests
curl.exe -X POST http://127.0.0.1:15721/v1/responses -H "Content-Type: application/json" -d '{"model":"<actual-model>","input":[{"role":"user","content":"hi"}],"max_output_tokens":10}' --max-time 15

# 2. Config is correct
Get-Content "$env:USERPROFILE\.codex\config.toml" | Select-String 'sandbox'

# 3. Loopback exemption exists
CheckNetIsolation LoopbackExempt -s | Select-String 'codex'
```

### Strategy B Verification

```powershell
# 1. Portproxy active
netsh interface portproxy show all

# 2. Request through portproxy works
curl.exe -X POST http://127.0.0.1:7897/v1/responses -H "Content-Type: application/json" -d '{"model":"<actual-model>","input":[{"role":"user","content":"hi"}],"max_output_tokens":10}' --max-time 10

# 3. Codex config points to 7897
Get-Content "$env:USERPROFILE\.codex\config.toml" | Select-String 'base_url|sandbox'
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
| --- | --- | --- |
| Codex reconnecting, sandbox=elevated | WFP blocking 15721 | Switch to Strategy A (unelevated) |
| Codex reconnecting, sandbox=unelevated | CC-Switch provider null | CC-Switch recovery (see above) |
| CC-Switch `/status` works but API fails | CC-Switch provider has no active targets | Check CC-Switch GUI → provider → targets |
| CC-Switch `/status` connection refused | CC-Switch not running | Start CC-Switch |
| `base_url` keeps reverting to 15721 | CC-Switch Live Takeover active | This is normal; use Strategy A |
| Codex updated, loopback exemption lost | PackageFullName changed | Re-run A4 with new package name |
| Portproxy disappeared after reboot | IP Helper service issue | Re-run B3; check IP Helper service |
| Sandbox rules keep reappearing | Codex recreates on launch | Expected; clean while Codex is stopped |
| Codex was working, suddenly reconnects | CC-Switch crashed and lost provider; or portproxy disappeared | Check CC-Switch `/status`; if provider is null, run CC-Switch Recovery |
| CC-Switch `/status` shows provider but `active_targets` empty | Targets not yet loaded or configured | Wait 30s; if still empty, check CC-Switch GUI |
| `error sending request for url (http://127.0.0.1:7897/...)` | Portproxy deleted or IP Helper service stopped | Re-run B3; check `services.msc` → IP Helper is running |
| Codex uses `codex/cx/gpt-5.5` as model name | CC-Switch rewrote model field | Normal — CC-Switch prefixes provider-scoped model names |
