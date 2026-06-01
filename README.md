# ljh-codex-desktop-loopback-repair-skill

A Codex Skill for diagnosing and repairing Codex Desktop loopback proxy failures on Windows.

It targets the common case where Codex Desktop keeps showing `Reconnecting` or `stream disconnected` even though a local OpenAI-compatible proxy such as CC-Switch is running on `127.0.0.1`.

## What This Skill Handles

- **CC-Switch Live Takeover** — recognizes when CC-Switch is managing Codex config and adapts strategy accordingly
- **Codex sandbox mode** — switches from `elevated` (WFP port blocking) to `unelevated` (no port blocking for main process)
- **Windows Store / MSIX AppContainer loopback isolation** — adds `CheckNetIsolation LoopbackExempt`
- **Codex sandbox firewall/WFP rules** — removes `codex_sandbox_offline_block_*` rules
- **Codex sandbox users** — removes `CodexSandboxOffline` and `CodexSandboxOnline`
- **`config.toml`** — backs up and patches sandbox mode without breaking CC-Switch integrations
- **`netsh interface portproxy`** — for legacy elevated-sandbox setups requiring port 7897 forwarding
- **CC-Switch crash recovery** — detects provider loss and guides recovery

## Key Insight

The root cause is a deadlock between two systems:

1. **Codex elevated sandbox** blocks all loopback ports except 7897
2. **CC-Switch Live Takeover** forces `base_url` to `http://127.0.0.1:15721/v1`

The fix: set `sandbox = "unelevated"` — the main Codex process is no longer subject to WFP port filtering, so it can reach CC-Switch on 15721 directly. No portproxy needed.

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

```text
Use $ljh-codex-desktop-loopback-repair-skill to fix Codex Desktop reconnecting on Windows.
```

With CC-Switch context:

```text
I use CC-Switch on 127.0.0.1:15721. Use $ljh-codex-desktop-loopback-repair-skill to fix Codex Desktop reconnecting.
```

## Safety Notes

- Inspect current state before changing anything
- Stop Codex Desktop before cleaning sandbox users or firewall/WFP state
- Back up `%USERPROFILE%\.codex\config.toml` before edits
- Keep unrelated Codex config intact
- Resolve the current Codex `PackageFullName` dynamically — never hard-code it
- Delete only known Codex sandbox rules or exact sandbox user names
- Do NOT fight CC-Switch's base_url — use Strategy A instead

Some repair commands require Administrator PowerShell.

## Repository Layout

```
.
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   └── validate_skill.py
├── .github/
│   └── workflows/
│       └── validate.yml
├── README.md
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
