# ljh-codex-desktop-loopback-repair-skill

A Codex Skill for diagnosing and repairing Codex Desktop loopback proxy failures on Windows.

It targets the common case where Codex Desktop keeps showing `Reconnecting` or `stream disconnected` even though a local OpenAI-compatible proxy such as CC-Switch is running on `127.0.0.1`.

## What This Skill Handles

- Windows Store / MSIX AppContainer loopback isolation
- Codex sandbox firewall/WFP rules that block local loopback ports
- Codex sandbox users such as `CodexSandboxOffline` and `CodexSandboxOnline`
- `config.toml` provider `base_url` issues
- `netsh interface portproxy` forwarding problems
- Clash / Clash Verge port conflicts with Codex
- CC-Switch style local backend proxies, commonly on `127.0.0.1:15721`

## Key Finding

On affected Codex Desktop Windows setups, the Codex sandbox log can show:

```text
RemotePorts=1-7896,7898-65535
```

That means the sandbox blocks loopback ports `1-7896` and `7898-65535`, leaving `7897` as the usable Codex-facing port.

When this pattern is present, use this layout:

| Port | Purpose |
| --- | --- |
| `7897` | Codex-facing port, forwarded to the backend proxy |
| `15721` | CC-Switch or local OpenAI-compatible backend |
| `7890` | Clash mixed-port after moving Clash away from `7897` |

Recommended request flow:

```text
Codex Desktop -> 127.0.0.1:7897 -> netsh portproxy -> 127.0.0.1:15721 -> CC-Switch
```

## Installation

Copy this repository folder into your Codex skills directory:

```powershell
Copy-Item -Recurse -Force .\ljh-codex-desktop-loopback-repair-skill "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill"
```

Then restart Codex Desktop or open a new Codex session so the skill metadata is reloaded.

## Usage

Ask Codex:

```text
Use $ljh-codex-desktop-loopback-repair-skill to diagnose and repair Codex Desktop reconnecting or stream disconnected errors on Windows.
```

For the CC-Switch case:

```text
I use CC-Switch on 127.0.0.1:15721. Use $ljh-codex-desktop-loopback-repair-skill to fix Codex Desktop reconnecting on Windows, including AppContainer loopback, sandbox WFP rules, portproxy, config.toml, and Clash port conflicts.
```

## Safety Notes

This skill is intentionally conservative:

- Inspect current state before changing anything.
- Stop Codex Desktop before cleaning sandbox users or firewall/WFP state.
- Back up `%USERPROFILE%\.codex\config.toml` before edits.
- Keep unrelated Codex config intact.
- Resolve the current Codex `PackageFullName` dynamically instead of hard-coding it.
- If the sandbox log proves only `7897` is allowed, reserve `7897` for Codex and move Clash to another port such as `7890`.
- Delete only known Codex sandbox rules or exact sandbox user names.

Some repair commands require Administrator PowerShell, especially `netsh`, firewall changes, `CheckNetIsolation`, and `net user` operations.

## Repository Layout

```text
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

Run the bundled validator before publishing or opening a pull request:

```powershell
python .\scripts\validate_skill.py .
```

The GitHub Actions workflow runs the same validation on push and pull request.

## License

MIT License. See [LICENSE](LICENSE).

## Author

- Author: 算个文科生吧
- Contact: lijinghailjh@163.com
