# Codex Desktop 修复方案

> 问题：Codex Desktop 一直显示 `Reconnecting` / `stream disconnected`，无法连接 CC-Switch (`127.0.0.1:15721`)。
> 根因：`elevated` 沙箱 WFP 只放行 `7897` 端口，加上 CC-Switch Live Takeover 强制把 `base_url` 写回 `15721`，形成配置死锁。
> 修复：优先把 sandbox 从 `elevated` 改为 `unelevated`，让 Codex 主进程不受 WFP 端口过滤，直接连接 `15721`。

---

## 策略 A（推荐）：unelevated 沙箱 + 直连 CC-Switch

简单可靠，不和 CC-Switch 冲突；CC-Switch 可以继续自动管理 `base_url`。

### 1. 关闭 Codex

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force
```

### 2. 备份并修改 config.toml

编辑 `C:\Users\lijinghai\.codex\config.toml`，只改 `sandbox`，不要动 `base_url`，因为 CC-Switch 会接管它：

```toml
[windows]
sandbox = "unelevated"   # 从 "elevated" 改为 "unelevated"
```

备份命令：

```powershell
Copy-Item "$env:USERPROFILE\.codex\config.toml" "$env:USERPROFILE\.codex\config.toml.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

### 3. 确保 CC-Switch 正常运行

```cmd
curl http://127.0.0.1:15721/status
```

如果返回里有 `"current_provider":null`，先等 30 秒让 CC-Switch 从备份恢复。如果仍未恢复，启动 CC-Switch。常见路径：

```text
F:\CC\cc-switch.exe
%LOCALAPPDATA%\com.ccswitch.desktop\cc-switch.exe
```

验证转发正常：

```cmd
curl -X POST http://127.0.0.1:15721/v1/responses -H "Content-Type: application/json" -d "{\"model\":\"gpt-5.5\",\"input\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_output_tokens\":10}" --max-time 15
```

应该返回正常模型输出，而不是 `proxy_error`。

### 4. 添加 AppContainer loopback 豁免（管理员）

先查 Codex 包名，更新后可能变化：

```powershell
Get-AppxPackage -Name '*OpenAI*' | Select-Object PackageFullName
```

用查到的包名添加豁免：

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

### 6. 按顺序启动

1. 先确保 CC-Switch 正常运行。
2. 再启动 Codex Desktop。
3. 等几秒让 CC-Switch Live Takeover 接管配置。

---

## 策略 B（遗留）：elevated 沙箱 + 端口转发

仅在必须使用 `elevated`，且 CC-Switch Live Takeover 已关闭时使用。

注意：CC-Switch Live Takeover 会把 `base_url` 写回 `15721`，导致此方案失效。

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

同步骤 A 的第 4 步。

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

## CC-Switch 故障恢复

Codex 突然重连通常是 CC-Switch 崩溃导致 provider 丢失。

1. 等 30-60 秒，让 CC-Switch 从备份自动恢复。
2. 如果没有恢复，找到 exe 并重启：

```powershell
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

---

## 验证

```cmd
:: 检查 CC-Switch 状态
curl http://127.0.0.1:15721/status

:: 检查 loopback 豁免
CheckNetIsolation LoopbackExempt -s | findstr codex

:: 检查 sandbox 配置
findstr sandbox %USERPROFILE%\.codex\config.toml
```

---

## 注意事项

- 优先使用策略 A，即 `unelevated`，简单且不与 CC-Switch 冲突。
- CC-Switch Live Takeover 会自动写入 `base_url = "http://127.0.0.1:15721/v1"` 和 `experimental_bearer_token = "PROXY_MANAGED"`，这是正常的，不要改回去。
- Codex Store 更新后 `PackageFullName` 可能变化，需要重新添加 loopback 豁免。
- 每次 Codex 启动会重建沙箱用户和防火墙规则，`unelevated` 模式下它们不影响主进程。
- CC-Switch 保持 `15721` 端口不变。
