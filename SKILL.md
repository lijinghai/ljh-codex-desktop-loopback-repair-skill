---
name: ljh-codex-desktop-loopback-repair-skill
description: Diagnose and repair Codex Desktop for Windows reconnecting, stream disconnected, or local OpenAI-compatible proxy failures caused by AppContainer loopback isolation, Codex sandbox firewall/WFP filtering, sandbox users, config.toml base_url ports, netsh portproxy, CheckNetIsolation LoopbackExempt, or port conflicts with Clash/CC-Switch. Use when Codex Desktop cannot reach 127.0.0.1 or localhost, especially when CC-Switch runs on 15721 and Codex must be routed through the sandbox-allowed 7897 port. 也用于修复 Windows 上 Codex Desktop 一直重连、流断开、本地 127.0.0.1 代理不可达、CC-Switch 代理不可用、Clash 占用 7897 等问题。
---

# Codex Desktop Loopback Repair

## Core Model

Treat this as a Windows loopback connectivity repair workflow for Codex Desktop, not a generic proxy tweak.

The expected failure is:

```text
Codex Desktop -> 127.0.0.1:<base_url port> -> local OpenAI-compatible proxy
```

Typical symptoms:

- Codex Desktop repeatedly shows `Reconnecting` or `stream disconnected`.
- A local proxy such as CC-Switch is healthy on `127.0.0.1:15721`, but Codex Desktop cannot reach it.
- Clash or another proxy uses port `7897`, conflicting with Codex's reachable local port.

On affected Windows Codex Desktop installs, the Codex sandbox WFP filtering may block loopback ports except `7897`. Confirm this from `%USERPROFILE%\.codex\.sandbox\sandbox.log`; a line like this means only `7897` is allowed:

```text
RemotePorts=1-7896,7898-65535
```

When that pattern is present, Codex's `base_url` must use `http://127.0.0.1:7897/v1`, and `7897` should forward to the actual backend proxy port such as `15721`.

## Operating Rules

- First inspect, then change. Use current machine state instead of stale package names or guessed ports.
- Before repairing sandbox users, WFP/firewall rules, or portproxy, stop Codex Desktop because it can recreate sandbox state while running.
- Use `127.0.0.1` instead of `localhost` for Codex-facing URLs.
- If sandbox log proves only `7897` is allowed, do not choose a different Codex-facing port. Move other software, such as Clash, away from `7897` instead.
- Back up `%USERPROFILE%\.codex\config.toml` before editing it.
- Do not overwrite the whole Codex config. Preserve unrelated providers, models, and settings.
- Request approval or elevation when required for `netsh`, `CheckNetIsolation`, `net user`, firewall changes, or stopping processes.
- Do not delete users or firewall rules by fuzzy matching. Touch only the known Codex sandbox names or explicitly confirmed rules.

## Discovery

Run lightweight checks and report what they show:

```powershell
Get-Content "$env:USERPROFILE\.codex\config.toml" -ErrorAction SilentlyContinue
Get-Content "$env:USERPROFILE\.codex\.sandbox\sandbox.log" -ErrorAction SilentlyContinue | Select-String 'RemotePorts'
Get-AppxPackage -Name '*OpenAI*' | Select-Object Name, PackageFullName
CheckNetIsolation LoopbackExempt -s
netsh interface portproxy show all
netsh advfirewall firewall show rule name="codex_sandbox_offline_block_loopback_tcp"
netsh advfirewall firewall show rule name="codex_sandbox_offline_block_loopback_udp"
netsh advfirewall firewall show rule name="codex_sandbox_offline_block_outbound"
net user CodexSandboxOffline
net user CodexSandboxOnline
```

Check the backend proxy before changing Codex-facing rules. For CC-Switch the expected backend is usually `15721`:

```powershell
curl.exe http://127.0.0.1:15721/status --max-time 10
```

If the backend is down, fix or start that proxy first. Do not hide a dead backend behind portproxy changes.

## Port Plan

Use this plan when `sandbox.log` shows `RemotePorts=1-7896,7898-65535`:

| Port | Owner | Purpose |
| --- | --- | --- |
| `7897` | Codex | Codex-facing port allowed by sandbox WFP; forwards to backend proxy |
| `15721` | CC-Switch | Local OpenAI-compatible backend API |
| `7890` | Clash | Clash mixed-port after moving it away from `7897` |
| `7898`/`7899` | Clash | SOCKS/HTTP ports if configured |

If Clash uses `7897`, move Clash to `7890` and reserve `7897` for Codex. Common files are:

```text
%APPDATA%\io.github.clash-verge-rev.clash-verge-rev\config.yaml
%APPDATA%\io.github.clash-verge-rev.clash-verge-rev\verge.yaml
```

Expected values:

```yaml
mixed-port: 7890
verge_mixed_port: 7890
```

## Repair Workflow

### 1. Stop Codex Desktop

Ask before doing this if the user has unsaved work:

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force
```

### 2. Back Up And Patch Codex Config

Back up first:

```powershell
Copy-Item "$env:USERPROFILE\.codex\config.toml" "$env:USERPROFILE\.codex\config.toml.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

Ensure the Windows sandbox mode and chosen provider use the Codex-facing port:

```toml
[windows]
sandbox = "unelevated"

[model_providers.custom]
base_url = "http://127.0.0.1:7897/v1"
```

Adapt the provider table name to the user's actual config. Preserve all unrelated settings.

### 3. Add AppContainer Loopback Exemption

Resolve the current package name dynamically:

```powershell
$pkg = (Get-AppxPackage -Name '*OpenAI*').PackageFullName
if ($pkg) { CheckNetIsolation LoopbackExempt -a -n="$pkg" }
```

If no package is found, broaden the search:

```powershell
Get-AppxPackage | Where-Object { $_.Name -match 'Codex|OpenAI' } | Select-Object Name, PackageFullName
```

### 4. Recreate Portproxy

For the common CC-Switch case:

```powershell
netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1
netsh interface portproxy add v4tov4 listenport=7897 listenaddress=127.0.0.1 connectport=15721 connectaddress=127.0.0.1
```

If the backend port is not `15721`, substitute the discovered backend port. Keep `listenport=7897` when sandbox WFP only allows `7897`.

### 5. Remove Known Sandbox Blocks When Present

Delete only these known rules when they exist and are relevant:

```powershell
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_tcp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_udp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_outbound"
```

Add an allow rule for the Codex-facing port if firewall policy requires it:

```powershell
netsh advfirewall firewall add rule name="Allow Codex Proxy 7897" dir=in action=allow protocol=tcp localport=7897
```

### 6. Remove Stale Sandbox Users When Needed

Only remove these exact accounts after confirming they exist and the user approves:

```powershell
net user CodexSandboxOffline /delete
net user CodexSandboxOnline /delete
```

Codex may recreate them on next launch; that is expected.

### 7. Restart In Order

Start the backend proxy first, then Clash if used, then Codex Desktop.

For the Clash/CC-Switch plan, the intended order is:

1. CC-Switch listens on `127.0.0.1:15721`.
2. Clash listens on `7890`, not `7897`.
3. `netsh portproxy` forwards `127.0.0.1:7897` to `127.0.0.1:15721`.
4. Codex Desktop uses `http://127.0.0.1:7897/v1`.

## Verification

Verify each layer separately:

```powershell
curl.exe http://127.0.0.1:15721/status --max-time 10
netsh interface portproxy show all
CheckNetIsolation LoopbackExempt -s
Get-Content "$env:USERPROFILE\.codex\.sandbox\sandbox.log" -ErrorAction SilentlyContinue | Select-String 'RemotePorts'
```

Verify the Codex-facing OpenAI-compatible endpoint with the user's actual model name:

```powershell
curl.exe -X POST http://127.0.0.1:7897/v1/responses -H "Content-Type: application/json" -d '{"model":"<configured-model>","input":[{"role":"user","content":"hi"}],"max_output_tokens":10}' --max-time 10
```

If `15721/status` works but the `7897` request fails, focus on `portproxy` and firewall. If the `7897` request works but Codex still reconnects, focus on `config.toml`, AppContainer loopback exemption, Codex package identity, and whether Codex was restarted after changes.

## Common Conclusions

- `sandbox.log` shows `RemotePorts=1-7896,7898-65535`: reserve `7897` for Codex.
- Clash occupied `7897`: move Clash mixed-port to `7890`.
- Codex update happened: re-query `PackageFullName` and re-add loopback exemption.
- Sandbox users or block rules reappear: stop Codex before repair, then remove the known stale state.
- The backend proxy is unreachable: repair CC-Switch or the local proxy before changing Codex.
