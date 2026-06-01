<!-- 算个文科生吧，联系方式WX：RabbitRobot2025 -->

# ljh-codex-desktop-loopback-repair-skill

A Codex Skill for diagnosing and repairing Codex Desktop loopback proxy failures on Windows.

It targets three common failure modes:

1. **Reconnecting / stream disconnected** — elevated sandbox WFP blocks 15721, deadlocks with CC-Switch Live Takeover
2. **413 Payload Too Large** — conversation context exceeds CC-Switch's 10 MB request body limit
3. **CC-Switch credential loss** — provider becomes null or reports "No credentials" after crash

## What This Skill Handles

- **CC-Switch Live Takeover** — recognizes when CC-Switch is managing Codex config and adapts strategy accordingly
- **Codex sandbox mode** — switches from `elevated` (WFP port blocking) to `unelevated` (no port blocking for main process)
- **Windows Store / MSIX AppContainer loopback isolation** — adds `CheckNetIsolation LoopbackExempt`
- **Codex sandbox firewall/WFP rules** — removes `codex_sandbox_offline_block_*` rules
- **Codex sandbox users** — removes `CodexSandboxOffline` and `CodexSandboxOnline`
- **`config.toml`** — backs up and patches sandbox mode without breaking CC-Switch integrations
- **`netsh interface portproxy`** — cleans up legacy 7897→15721 forwarding when switching to Strategy A
- **CC-Switch crash recovery** — detects provider/credential loss, waits for auto-recovery, or restarts CC-Switch
- **413 Payload Too Large** — diagnoses oversized context and guides session cleanup

## Key Insight

Three independent problems can look the same:

1. **Sandbox deadlock**: elevated sandbox blocks all ports except 7897, CC-Switch forces base_url to 15721 → Codex can't connect → Reconnecting loop
2. **Context overflow**: CC-Switch is reachable but request body > 10 MB → 413 Payload Too Large
3. **CC-Switch crash**: CC-Switch lost provider credentials after restart → all requests fail with 400

The fix priorities:
- **First**: set `sandbox = "unelevated"` — eliminates WFP deadlock
- **Second**: verify CC-Switch `/status` — ensure provider is valid and API forwarding works
- **Third**: if 413 persists, start a new conversation to reset context

## Two Repair Strategies

| Strategy | Sandbox | Portproxy | CC-Switch Compatible | Complexity |
| --- | --- | --- | --- | --- |
| **A (Recommended)** | unelevated | Not needed | Yes | Low |
| B (Legacy) | elevated | 7897→15721 | No (conflict) | High |

## Installation

Copy this repository folder into your Codex skills directory:

```powershell
Copy-Item -Recurse -Force .\ljh-codex-desktop-loopback-repair-skill "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill"
```

Then restart Codex Desktop or open a new Codex session.

## Usage

Local one-click web repair panel:

```bat
start-repair-web.bat
```

Keep the BAT window open while using the page. Press `Ctrl+C` in that window to stop the local repair web server.

PowerShell equivalent:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-repair-web.ps1
```

Then open `http://127.0.0.1:8765/` and click **一键修复**.

Run the PowerShell command as Administrator if you also want the panel to clean Codex sandbox firewall rules, sandbox users, and legacy `7897 -> 15721` portproxy state. Without Administrator rights, it still backs up and patches `config.toml`, adds the loopback exemption when possible, checks CC-Switch, and can archive oversized 413 sessions.

Basic repair:

```text
Use $ljh-codex-desktop-loopback-repair-skill to fix Codex Desktop reconnecting on Windows.
```

With CC-Switch context:

```text
I use CC-Switch on 127.0.0.1:15721. Use $ljh-codex-desktop-loopback-repair-skill to fix Codex Desktop reconnecting.
```

With 413 error:

```text
Codex shows "413 Payload Too Large" error. Use $ljh-codex-desktop-loopback-repair-skill to fix it.
```

## Repair Flow

```
Stop Codex → Diagnose (sandbox, CC-Switch, loopback, portproxy)
    ↓
sandbox=elevated? → Strategy A: unelevated + direct 15721
    ↓
CC-Switch healthy? → If not: wait 30s for auto-recovery, or restart CC-Switch
    ↓
413 error? → Start new conversation (context too large)
    ↓
Start Codex → Verify
```

## Safety Notes

- Inspect current state before changing anything
- Stop Codex Desktop before cleaning sandbox users or firewall/WFP state
- Back up `%USERPROFILE%\.codex\config.toml` before edits
- Keep unrelated Codex config intact — only change `sandbox` mode
- Resolve the current Codex `PackageFullName` dynamically — never hard-code it
- Delete only known Codex sandbox rules or exact sandbox user names
- Do NOT fight CC-Switch's `base_url` — use Strategy A instead
- CC-Switch Live Takeover writes `base_url = "http://127.0.0.1:15721/v1"` — this is NORMAL

Some repair commands require Administrator PowerShell.

## Repository Layout

```
.
├── SKILL.md              # Main skill definition (Codex loads this)
├── agents/
│   └── openai.yaml       # Agent interface config
├── scripts/
│   └── validate_skill.py # Skill validation script
├── Codex修复方案.md        # Chinese repair guide (detailed)
├── README.md             # This file
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
└── .gitignore
```

## Validation

```powershell
python .\scripts\validate_skill.py .
```

## License

MIT License. See [LICENSE](LICENSE).

## Author

- Author: 算个文科生吧
- Contact: lijinghailjh@163.com
- WeChat: RabbitRobot2025
