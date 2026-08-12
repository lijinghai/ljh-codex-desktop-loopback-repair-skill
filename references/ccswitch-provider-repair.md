# CC-Switch Codex Provider Repair

Use this reference when the local loopback path is healthy but CC-Switch cannot forward Codex requests, especially after a user provides a known API key and upstream URL.

## Key Lessons

- Do not assume the Codex model is the bare name. Query the upstream `/v1/models`; if it lists `cx/gpt-5.5`, configure Codex/CC-Switch with exactly `cx/gpt-5.5`, not `gpt-5.5`.
- `HTTP 404` with `No active credentials for provider: openai` can mean either a server-side upstream credential failure or a local model/provider mismatch. Test the upstream directly with the provided key before declaring it server-side.
- Update all CC-Switch persistence locations together: `providers.settings_config`, `provider_endpoints`, `proxy_live_backup.original_config`, and usually `settings.common_config_codex`. Otherwise CC-Switch can restore stale config on restart.
- Never print or commit real API keys. Pass keys through `CODEX_PROVIDER_KEY`, `--api-key-file`, or another transient secret channel.
- CC-Switch started from a non-interactive SSH session can exit when the session ends. Start it through an interactive scheduled task for the logged-in Windows user.
- When testing JSON bodies from PowerShell, prefer `ConvertTo-Json` plus `curl.exe --data-binary @file`. Inline quoting can corrupt JSON and produce misleading `Invalid JSON body` errors.

## Direct Upstream Test

Run this before editing the database. Replace placeholders only on the target machine; do not write real keys into the skill or repo.

```powershell
$env:CODEX_PROVIDER_KEY = '<API_KEY>'
$baseUrl = 'https://YOUR-UPSTREAM.example/v1'

curl.exe -sS "$baseUrl/models" -H "Authorization: Bearer $env:CODEX_PROVIDER_KEY"

$bodyPath = New-TemporaryFile
$body = @{ model = 'cx/gpt-5.5'; input = @(@{ role = 'user'; content = 'Reply with exactly OK.' }); max_output_tokens = 16 } |
    ConvertTo-Json -Depth 8 -Compress
Set-Content -Path $bodyPath -Value $body -Encoding ASCII -NoNewline
curl.exe -sS -w "`nHTTP_STATUS:%{http_code}`n" "$baseUrl/responses" \
    -H "Authorization: Bearer $env:CODEX_PROVIDER_KEY" \
    -H 'Content-Type: application/json' \
    --data-binary "@$bodyPath"
Remove-Item $bodyPath -Force
Remove-Item Env:CODEX_PROVIDER_KEY
```

Expected success: HTTP 200 with output text such as `OK`. If `/models` succeeds but `/responses` fails with the bare model, retry with a provider-scoped model returned by `/models`.

## Configure CC-Switch Database

Stop Codex and CC-Switch first. Use the helper from the skill directory:

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force

$env:CODEX_PROVIDER_KEY = '<API_KEY>'
python .\scripts\configure_ccswitch_codex_provider.py `
    --db "$env:USERPROFILE\.cc-switch\cc-switch.db" `
    --base-url 'https://YOUR-UPSTREAM.example/v1' `
    --model 'cx/gpt-5.5' `
    --require-api-key
Remove-Item Env:CODEX_PROVIDER_KEY
```

The helper backs up `cc-switch.db`, updates only the selected/current Codex provider, replaces the provider endpoint, updates `proxy_live_backup`, updates `settings.common_config_codex` when present, and resets provider health. Its JSON output reports whether a key was present, but never prints the key.

## Node.js Quick Helper

If Python is unavailable on the target machine but Node.js 24+ is installed, use the lightweight helper to repair a known model prefix and upstream URL:

```powershell
$env:CODEX_PROVIDER_KEY = '<API_KEY>'
node .\scripts\fix_model_prefix.mjs `
    --db "$env:USERPROFILE\.cc-switch\cc-switch.db" `
    --base-url 'https://YOUR-UPSTREAM.example/v1' `
    --model 'cx/gpt-5.5'
Remove-Item Env:CODEX_PROVIDER_KEY
```

`configure_ccswitch_codex_provider.py` remains preferred for full provider normalization. `fix_model_prefix.mjs` is the quick path for machines that already have Node 24+ but lack Python; it updates the current/selected Codex provider, `provider_endpoints`, `proxy_live_backup`, `settings.common_config_codex`, `provider_health`, and local `config.toml` model without printing the API key. It leaves local `config.toml` `base_url` alone unless `--config-base-url` is passed, because CC-Switch Live Takeover normally manages that value.

## Start CC-Switch Reliably From SSH

After DB repair, start CC-Switch through the logged-in user's interactive token:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-ccswitch-interactive-task.ps1 -Restart
```

This registers `StartCCSwitchInteractive`, starts it immediately, and verifies `http://127.0.0.1:15721/status`.

## Verify Local Proxy

```powershell
$bodyPath = New-TemporaryFile
$body = @{ model = 'cx/gpt-5.5'; input = @(@{ role = 'user'; content = 'Reply with exactly OK.' }); max_output_tokens = 16 } |
    ConvertTo-Json -Depth 8 -Compress
Set-Content -Path $bodyPath -Value $body -Encoding ASCII -NoNewline
curl.exe -sS -w "`nHTTP_STATUS:%{http_code}`n" 'http://127.0.0.1:15721/v1/responses' \
    -H 'Content-Type: application/json' \
    --data-binary "@$bodyPath"
Remove-Item $bodyPath -Force
curl.exe -s http://127.0.0.1:15721/status --max-time 8
```

Success criteria: HTTP 200, output text `OK`, `/status` shows the expected provider and success rate above 0.

## Stronger Sandbox Guard

If a Startup-folder guard is killed by session lifetime or does not target the right user profile, install the SYSTEM scheduled-task guard:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-sandbox-guard-task.ps1
```

The installer writes an absolute-path guard script under the user's `.codex` directory and registers `CodexSandboxGuard` as SYSTEM. Test it by temporarily setting `sandbox = "elevated"`; it should revert to `unelevated` within 10 seconds and append a log entry to `sandbox-guard.log`.
