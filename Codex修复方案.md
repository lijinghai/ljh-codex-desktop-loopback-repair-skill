<!-- 算个文科生吧，联系方式WX：RabbitRobot2025 -->

# Codex Desktop 修复方案

> **问题全景**：Codex Desktop 可能出现四种故障：
> 1. `Reconnecting` / `stream disconnected` — 沙箱 WFP 阻断 15721，配置死锁
> 2. `413 Payload Too Large` — 会话上下文超过 CC-Switch 10 MB 请求体限制
> 3. CC-Switch 凭证丢失 — 崩溃后 provider 变 null 或报 "No credentials"
> 4. 上游 API 服务器宕机 — provider 有效但请求全部超时/失败
> 5. macOS Codex App plist 覆盖 — `com.openai.codex.plist` 的 `config_toml_base64` 把 GUI 强制指向旧上游
>
> **修复优先级**：先修沙箱（策略 A），再查 CC-Switch 健康（含上游连通性），最后处理 413。

---

## macOS Codex.app + CC-Switch relay 修复

macOS 版 Codex App 除了读取 `~/.codex/config.toml`，还可能从 `~/Library/Preferences/com.openai.codex.plist` 的 `config_toml_base64` 读取一份隐藏 TOML。这个隐藏配置会覆盖文件配置，导致 GUI 继续打到 `https://llm.slashrobot.top/v1/responses`，即使 `config.toml` 已经改成 `http://127.0.0.1:15721/v1`。

一键修复脚本：

```bash
python3 scripts/fix_macos_codex_ccswitch.py \
  --relay-base-url http://100.109.173.92:18081/v1 \
  --local-base-url http://127.0.0.1:15721/v1 \
  --model cx/gpt-5.5
```

脚本会备份并修复：

- `~/.codex/config.toml`
- `~/Library/Preferences/com.openai.codex.plist` 的 `config_toml_base64`
- `~/.cc-switch/cc-switch.db` 里的 `providers`、`provider_endpoints`、`proxy_live_backup`、`settings.common_config_codex`、`provider_health`
- `NO_PROXY` LaunchAgent，避免 Tailscale relay 被系统代理劫持
- plist guard LaunchAgent，防止 Codex App 或 CC-Switch 后续把配置改回旧上游

验证标准：CC-Switch `/status` 的 `last_error` 为 `null`，小请求 `/v1/responses` 返回 HTTP 200，`/Applications/Codex.app/Contents/Resources/codex doctor --json` 的 provider reachability 为 `ok`，`codex exec` 小提示词返回 `OK`。

---

## 策略 A（推荐）：unelevated 沙箱 + 直连 CC-Switch

简单可靠，不和 CC-Switch 冲突；CC-Switch 可以继续自动管理 `base_url`。

### 1. 关闭 Codex

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force
```

### 2. 诊断当前状态

```powershell
# 查看 sandbox 和 base_url
Get-Content "$env:USERPROFILE\.codex\config.toml" | Select-String 'sandbox|base_url|experimental_bearer_token'

# 查看 CC-Switch 状态
curl.exe -s http://127.0.0.1:15721/status

# 查看 loopback 豁免
CheckNetIsolation LoopbackExempt -s | Select-String 'codex|openai'

# 查看端口转发
netsh interface portproxy show all
```

### 3. 备份并修改 config.toml

```powershell
Copy-Item "$env:USERPROFILE\.codex\config.toml" "$env:USERPROFILE\.codex\config.toml.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

编辑 `C:\Users\lijinghai\.codex\config.toml`，**只改 `sandbox`，不要动 `base_url`**（CC-Switch 会接管它）：

```toml
[windows]
sandbox = "unelevated"   # 从 "elevated" 改为 "unelevated"
```

### 4. 添加 AppContainer loopback 豁免（管理员）

```powershell
$pkg = (Get-AppxPackage -Name '*OpenAI*').PackageFullName
if ($pkg) { CheckNetIsolation LoopbackExempt -a -n="$pkg" }
```

### 5. 清理沙箱状态（管理员）

```cmd
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_tcp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_udp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_outbound"

net user CodexSandboxOffline /delete
net user CodexSandboxOnline /delete
```

Codex 重启后会重建沙箱用户和规则，这是正常现象。`unelevated` 模式下它们不影响主进程直连代理。

### 6. 清理遗留端口转发（可选）

如果之前策略 B 配过 `7897→15721` 的端口转发，unelevated 下不再需要：

```cmd
netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1
```

---

## CC-Switch 故障恢复

Codex 突然重连通常是 CC-Switch 崩溃导致 provider 丢失或凭证丢失。

### 情况 1：provider 为 null 或报 "No credentials"

1. **等 30-60 秒**，让 CC-Switch 从备份自动恢复 Live 配置。
2. 观察控制台日志，出现 `Live 配置已从备份恢复` 说明恢复成功。
3. 如果等待后仍未恢复，重启 CC-Switch：

```powershell
# 找到 CC-Switch 并重启
$path = (Get-Process cc-switch -ErrorAction SilentlyContinue | Select-Object -First 1).Path
if (-not $path) {
    $common = @(
        "F:\CC\cc-switch.exe",
        "$env:LOCALAPPDATA\com.ccswitch.desktop\cc-switch.exe"
    )
    $path = $common | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($path) {
    Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    Start-Process $path -WindowStyle Hidden
}
```

4. 重启后等 10 秒，再次检查 `/status`。
5. 如果 60 秒后仍未恢复，打开 CC-Switch GUI 手动重新配置 provider 和 API 密钥。
6. **如果手动修改了数据库但重启后又被覆盖**：`proxy_live_backup` 表会在 CC-Switch 启动时恢复旧的认证 token。必须同时更新 `providers` 表和 `proxy_live_backup` 表（详见 "CC-Switch 上游 API 端点修复"）。

### 情况 2：CC-Switch 未运行

```powershell
# 尝试常见路径启动
$common = @(
    "F:\CC\cc-switch.exe",
    "$env:LOCALAPPDATA\com.ccswitch.desktop\cc-switch.exe"
)
$path = $common | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($path) { Start-Process $path -WindowStyle Hidden }
```

### 验证 CC-Switch 转发正常

```powershell
$body = @{
    model = "gpt-5.5"
    input = @(@{ role = "user"; content = "hi" })
    max_output_tokens = 10
} | ConvertTo-Json -Depth 5 -Compress

Invoke-RestMethod -Uri "http://127.0.0.1:15721/v1/responses" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 15 | ConvertTo-Json -Depth 5 -Compress
```

应该返回模型输出（如 `Hi! How can I help?`），而不是 `proxy_error`。

---

## 413 Payload Too Large 修复

**根因**：Codex 会话上下文（系统提示 + 工具定义 + 对话历史 + 文件内容）超过上游 API 的 10 MB 请求体限制。413 错误来自上游供应商（不是 CC-Switch 本身）—— 检查 CC-Switch `/status` 会看到 `"上游错误 (状态码 413)"`。**CC-Switch 是可以连通的**，只是请求体太大被上游拒绝。

### 自动修复

```powershell
# 1. 关闭 Codex
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force

# 2. 找出大于 5 MB 的会话
Write-Host "=== 大会话文件 ==="
Get-ChildItem "$env:USERPROFILE\.codex\sessions" -Recurse -Filter "*.jsonl" | Where-Object { $_.Length -gt 5MB } | Sort-Object Length -Descending | Select-Object Length, Name

# 3. 归档今天的会话，清零上下文
$today = Get-Date -Format "yyyy\MM\dd"
$src = "$env:USERPROFILE\.codex\sessions\$today"
$archive = "$env:USERPROFILE\.codex\archived_sessions"
if (Test-Path $src) {
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    Get-ChildItem $src -Filter "*.jsonl" -File | Move-Item -Destination $archive -Force
    Write-Host "已归档今天的会话 — 上下文已清零"
}

# 4. 确认已清空
Get-ChildItem "$env:USERPROFILE\.codex\sessions\$today" -ErrorAction SilentlyContinue
```

归档后启动 Codex，新会话上下文从零开始。

### 预防

如果一次对话中有大量工具调用或读取了大文件，上下文会快速增长超过 10 MB。长时间任务建议定期开新会话。

**注意**：413 不是沙箱/网络问题。如果 CC-Switch `/status` 正常且测试请求可以通过，但 Codex 报 413，那就是上下文太大，需要归档会话文件——不要改 sandbox 或网络设置。

---

## CC-Switch 上游 API 端点修复

CC-Switch 本身健康（运行中、provider 有效、无凭证错误），但所有请求都失败——上游 API 服务器不可达。

### 症状

- CC-Switch `/status` 返回 `"running":true`，`current_provider` 有值
- `success_rate` 为 0.0%，`failed_requests` 持续增长
- `last_error` 显示 `"所有供应商都失败"` 或 `"请求超时"`
- API 测试请求卡住 20 秒后超时

### 诊断

```powershell
# 1. 查看 CC-Switch 日志，找到上游 URL
Get-Content "$env:USERPROFILE\.cc-switch\logs\cc-switch.log" -Tail 5 | Select-String '>>> 请求 URL'

# 2. 直接测试上游 URL 连通性
curl.exe -v --max-time 10 "https://<UPSTREAM_HOST>/v1/responses" -H "Content-Type: application/json" -H "Authorization: Bearer <API_KEY>" -d '{"model":"gpt-5.5","input":[{"role":"user","content":"hi"}],"max_output_tokens":10}'
```

如果直接 curl 上游也超时或不返回，说明上游 API 服务器宕机或网络不通。

### 修复

**方案 1：CC-Switch GUI 切换供应商**（推荐）
1. 打开 CC-Switch GUI
2. 切换到有可用端点的供应商
3. 用 `/status` 确认 provider 已切换

**方案 2：直接修复数据库**（GUI 不可用时）

```powershell
# 必须先停止 CC-Switch
Get-Process cc-switch -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2

$db = "$env:USERPROFILE\.cc-switch\cc-switch.db"

# 查看所有供应商和端点
sqlite3 $db "SELECT id, app_type, name, is_current FROM providers;"
sqlite3 $db "SELECT * FROM provider_endpoints;"

# 方案 2a：切换到已有的可用供应商
sqlite3 $db "UPDATE providers SET is_current = 1 WHERE id = 'default' AND app_type = 'codex';"
sqlite3 $db "UPDATE providers SET is_current = 0 WHERE id = '<DEAD_PROVIDER_ID>' AND app_type = 'codex';"

# 方案 2b：修复当前供应商的上游 URL
# 修改 provider 的 settings_config（改 base_url 和 API key）
sqlite3 $db "UPDATE providers SET settings_config = replace(replace(settings_config, '<DEAD_HOST>', '<WORKING_HOST>'), '<OLD_KEY>', '<WORKING_KEY>') WHERE id = '<PROVIDER_ID>' AND app_type = 'codex';"

# ⚠️ 关键：必须同时更新 proxy_live_backup！
# CC-Switch 重启后会从这个备份恢复配置，覆盖你在 provider 表中的手动修改
sqlite3 $db "UPDATE proxy_live_backup SET original_config = replace(replace(original_config, '<DEAD_HOST>', '<WORKING_HOST>'), '<OLD_KEY>', '<WORKING_KEY>') WHERE app_type = 'codex';"

# 验证修改
sqlite3 $db "SELECT substr(original_config, 1, 300) FROM proxy_live_backup WHERE app_type = 'codex';"

# 重启 CC-Switch
Start-Process "F:\CC\cc-switch.exe" -WindowStyle Hidden
Start-Sleep 5
curl.exe -s http://127.0.0.1:15721/status
```

### proxy_live_backup 机制说明

CC-Switch 在数据库中维护一张 `proxy_live_backup` 表，存储 Codex config.toml 的快照：

- **备份时机**：CC-Switch Live Takeover 接管 Codex 配置时，会把当前 `config.toml` 完整备份到 `proxy_live_backup`
- **恢复时机**：CC-Switch 每次启动时，从 `proxy_live_backup` 恢复认证 token（`OPENAI_API_KEY`）到当前活跃供应商的 `settings_config`
- **副作用**：如果你手动修改了数据库中的 `settings_config`（改了 API key 或 base_url），CC-Switch 重启后会从 `proxy_live_backup` 恢复旧的 token，覆盖你的修改
- **正确做法**：同时更新 `providers.settings_config` 和 `proxy_live_backup.original_config`

### 验证

```powershell
# 测试修复后的连通性
$body = @{ model = "gpt-5.5"; input = @(@{ role = "user"; content = "hi" }); max_output_tokens = 10 } | ConvertTo-Json -Depth 5 -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:15721/v1/responses" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 20 | ConvertTo-Json -Depth 5 -Compress
```

预期返回流式模型输出（如 `Hi! How can I help?`），而不是超时或 proxy_error。

---

## 策略 B（遗留）：elevated 沙箱 + 端口转发

仅在必须使用 `elevated`，且 CC-Switch Live Takeover 已关闭时使用。

**警告**：CC-Switch Live Takeover 会把 `base_url` 写回 `15721`，导致此方案失效。

### 1. 关闭 Codex

同策略 A。

### 2. 修改 config.toml

```toml
[windows]
sandbox = "elevated"

[model_providers.custom]
base_url = "http://127.0.0.1:7897/v1"   # 7897 是沙箱唯一放行的端口
```

### 3. 设置端口转发（管理员）

```cmd
netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1
netsh interface portproxy add v4tov4 listenport=7897 listenaddress=127.0.0.1 connectport=15721 connectaddress=127.0.0.1
```

### 4. AppContainer loopback 豁免

同策略 A 第 4 步。

### 5. 防火墙和沙箱清理（管理员）

```cmd
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_tcp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_udp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_outbound"
netsh advfirewall firewall add rule name="Allow Codex Proxy 7897" dir=in action=allow protocol=tcp localport=7897

net user CodexSandboxOffline /delete
net user CodexSandboxOnline /delete
```

### 6. 按顺序启动

1. 启动 CC-Switch。
2. 用 `netsh interface portproxy show all` 验证端口转发。
3. 启动 Codex Desktop。

---

## 验证

```cmd
:: 检查配置
findstr sandbox %USERPROFILE%\.codex\config.toml
findstr base_url %USERPROFILE%\.codex\config.toml

:: 检查 CC-Switch 状态
curl -s http://127.0.0.1:15721/status

:: 测试 API 转发（PowerShell）
$body = @{ model = "gpt-5.5"; input = @(@{ role = "user"; content = "hi" }); max_output_tokens = 10 } | ConvertTo-Json -Depth 5 -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:15721/v1/responses" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 15 | ConvertTo-Json -Depth 5 -Compress

:: 检查 loopback 豁免
CheckNetIsolation LoopbackExempt -s | findstr codex

:: 检查端口转发
netsh interface portproxy show all
```

### 成功标准

| 检查项 | 策略 A 期望 | 策略 B 期望 |
|---|---|---|
| sandbox | `"unelevated"` | `"elevated"` |
| base_url | `http://127.0.0.1:15721/v1` | `http://127.0.0.1:7897/v1` |
| CC-Switch `/status` | `"running":true`, provider 不为 null | 同 |
| API 测试 | 模型正常输出 | 同（通过 7897） |
| Loopback | Codex 包名在列表中 | 同 |
| Portproxy | 空（或已清理） | `7897 → 15721` |
| Codex 行为 | 无重连，无 413 | 同 |

---

## 故障速查表

| 症状 | 可能原因 | 修复 |
|---|---|---|
| Codex 重连, sandbox=elevated | WFP 阻断 15721 | 策略 A：改为 unelevated |
| Codex 重连, sandbox=unelevated | CC-Switch provider 丢失或未运行 | CC-Switch 恢复 |
| `413 Payload Too Large` | 会话上下文 > 10 MB | 开新会话 |
| CC-Switch `/status` 正常但 API 失败 | provider 无活跃目标 | 检查 CC-Switch GUI |
| CC-Switch `/status` 连接被拒绝 | CC-Switch 未运行 | 启动 CC-Switch |
| CC-Switch 报 "No credentials" | 崩溃后凭证丢失 | 等 30s 自动恢复，或重启 CC-Switch |
| `base_url` 反复被改回 15721 | CC-Switch Live Takeover 活跃 | 正常行为，用策略 A |
| Codex 更新后 loopback 豁免丢失 | PackageFullName 变了 | 重新执行策略 A 第 4 步 |
| 端口转发重启后消失 | IP Helper 服务问题 | 重新执行 B3，检查 IP Helper 服务 |
| 之前能用突然重连 | CC-Switch 崩溃丢失 provider | 检查 `/status`，执行 CC-Switch 恢复 |
| Codex 模型名变成 `codex/cx/gpt-5.5` | CC-Switch 重写了 model 字段 | 正常，CC-Switch 加了 provider 前缀 |
| CC-Switch provider 有效但请求全超时 | 上游 API 服务器宕机 | 切换 supplier 或修复数据库（见 CC-Switch 上游 API 端点修复） |
| CC-Switch 显示 "所有供应商都失败" | 上游 endpoint 不可达或凭证无效 | 更新 endpoint URL 和 API key，同时更新 backup |
| 手动改了 DB 但重启后又被覆盖 | `proxy_live_backup` 覆盖了手动修改 | 必须同时更新 provider 表和 proxy_live_backup 表 |
| CC-Switch API 返回 `upstream_status: HTTP 404` + `No active credentials for provider: openai` | 第三方代理（如 `llm.slashrobot.top`）的上游凭证过期 — 服务端问题，本地无法修复 | 联系代理服务管理员，或在 CC-Switch GUI 切换供应商 |

---

## 紧凑修复流程（最常见场景）

Codex 出现 "Reconnecting" / "stream disconnected"，99% 是 CC-Switch 重启后把 `sandbox` 改回了 `elevated` 导致死锁。

**管理员 PowerShell** 三步修复：

```powershell
# 1. 停止 Codex
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force

# 2. 备份并修改 sandbox → unelevated
$config = "$env:USERPROFILE\.codex\config.toml"
Copy-Item $config "$config.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
$text = Get-Content $config -Raw -Encoding UTF8
$text = $text -replace 'sandbox\s*=\s*"[^\"]+"', 'sandbox = "unelevated"'
[IO.File]::WriteAllText($config, $text, [Text.UTF8Encoding]::new($false))

# 3. Loopback 豁免 + Portproxy
$pkg = (Get-AppxPackage -Name '*OpenAI*').PackageFullName
if ($pkg) { CheckNetIsolation LoopbackExempt -a -n="$pkg" }
netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1
netsh interface portproxy add v4tov4 listenport=7897 listenaddress=127.0.0.1 connectport=15721 connectaddress=127.0.0.1
```

安装 Sandbox 守护（防复发）：

```powershell
Copy-Item "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts\sandbox-guard.ps1" "$env:USERPROFILE\.codex\sandbox-guard.ps1" -Force
Copy-Item "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts\sandbox-guard.vbs" "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\sandbox-guard.vbs" -Force
Start-Process powershell.exe -ArgumentList '-WindowStyle Hidden -ExecutionPolicy Bypass -File', "$env:USERPROFILE\.codex\sandbox-guard.ps1" -WindowStyle Hidden
```

### 如果 CC-Switch API 测试返回上游凭证过期

```
upstream_status: HTTP 404
cause: No active credentials for provider: openai
```

这是第三方代理（如 `llm.slashrobot.top`）服务端问题，**本地无法修复**。需要联系代理管理员或切换供应商。

### 如果深度恢复后 CC-Switch 显示 provider=null

正常现象 — 启动 Codex Desktop，CC-Switch Live Takeover 会在几秒内自动初始化 provider。

---

## 注意事项

- 优先使用策略 A，即 `unelevated`，简单且不与 CC-Switch 冲突。
- CC-Switch Live Takeover 会自动写入 `base_url = "http://127.0.0.1:15721/v1"` 和 `experimental_bearer_token = "PROXY_MANAGED"`，这是正常的，不要改回去。
- CC-Switch 崩溃后等 30-60 秒，它会从备份自动恢复 Live 配置。如果等不及可以手动重启。
- Codex Store 更新后 `PackageFullName` 可能变化，需要重新添加 loopback 豁免。
- 每次 Codex 启动会重建沙箱用户和防火墙规则，`unelevated` 模式下它们不影响主进程。
- CC-Switch 保持 `15721` 端口不变。
- 413 错误不是网络问题 — CC-Switch 能连通，只是请求体太大。开新会话即可解决。
