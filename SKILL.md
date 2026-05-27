---
name: ljh-codex-desktop-loopback-repair-skill
description: Diagnose and repair Codex Desktop for Windows reconnecting, stream disconnected, or local proxy connection failures involving 127.0.0.1, AppContainer loopback isolation, Codex sandbox firewall/WFP rules, sandbox users, config.toml model provider base_url ports, netsh portproxy, CheckNetIsolation LoopbackExempt, or local API proxies such as CC-Switch. Use when Codex Desktop cannot connect to a local OpenAI-compatible API proxy, especially when the app repeatedly shows Reconnecting or stream disconnected. 也用于修复 Windows 上 Codex Desktop 一直重连、流断开、本地 127.0.0.1 代理不可达、CC-Switch 代理不可用等问题。
---

<!--
Author: 算个文科生吧
Contact: lijinghailjh@163.com
Project: ljh_codex-desktop-loopback-repair_skill
-->

# Codex Desktop 本地代理连接修复

## 这个 Skill 解决什么问题

当 Codex Desktop 在 Windows 上无法连接本地 API 代理时，使用这个 skill。典型现象包括：

- Codex Desktop 一直显示 `Reconnecting`
- 出现 `stream disconnected`
- Codex 无法连接 `127.0.0.1` 或 `localhost` 上的本地 API 代理
- 使用 CC-Switch、本地 OpenAI 兼容代理，后端端口类似 `15721`
- Codex 配置里的 `base_url` 指向本机端口，但 Desktop 端始终连不上

常见根因是多层 Windows 网络隔离叠加：

- Windows Store / MSIX AppContainer 默认不能访问本机 loopback
- Codex sandbox 相关防火墙或 WFP 规则阻断本地连接
- Codex sandbox 用户状态异常
- `%USERPROFILE%\.codex\config.toml` 中 `sandbox` 或 `base_url` 配置不合适
- `netsh portproxy` 端口转发缺失或端口冲突
- AppContainer loopback 豁免缺失或 Codex 更新后包名变化

## Codex 出问题时怎么使用这个 Skill

当 Codex Desktop 再次出现 `Reconnecting` 或 `stream disconnected` 时，在新的 Codex 对话里直接输入：

```text
使用 $ljh-codex-desktop-loopback-repair-skill 帮我诊断并修复 Codex Desktop 在 Windows 上一直 Reconnecting 或 stream disconnected 的问题。
```

如果你知道自己使用的是 CC-Switch，可以这样写得更明确：

```text
我使用的是本地 CC-Switch 代理，后端端口是 15721。请使用 $ljh-codex-desktop-loopback-repair-skill 检查并修复 Codex Desktop 无法连接本地代理的问题。
```

如果希望 Codex 先说明命令再执行，可以使用下面这段：

```text
使用 $ljh-codex-desktop-loopback-repair-skill 帮我修复 Codex Desktop 在 Windows 上一直 Reconnecting / stream disconnected 的问题。

请先检查我的 .codex/config.toml、本地代理端口、AppContainer loopback 豁免、netsh portproxy、防火墙规则和 Codex sandbox 用户。

如果需要修改系统网络规则，请先说明要执行的命令，并在需要管理员权限时请求我批准。修复后请验证 127.0.0.1 本地代理和 Codex-facing 端口是否都能正常访问。
```

## 核心原则

先诊断，再修复。不要直接套用固定端口、固定包名或固定模型名。

必须从当前机器上发现真实值：

- 当前 Codex Desktop 的 `PackageFullName`
- 当前本地代理后端端口
- 当前 `config.toml` 里的 provider 和 `base_url`
- 当前 Windows sandbox 配置
- 当前已有的 `netsh portproxy` 规则
- 当前 loopback 豁免和防火墙规则

修改系统状态前要说明风险。涉及 `netsh`、`CheckNetIsolation`、`net user`、停止进程、防火墙修改等操作时，根据环境要求请求管理员权限或用户批准。

## 修复流程

### 1. 确认症状和环境

确认问题是否符合以下场景：

- 系统是 Windows
- Codex Desktop 是 Store / MSIX 应用
- Codex Desktop 显示 `Reconnecting` 或 `stream disconnected`
- 本地 provider 使用 `127.0.0.1` 或 `localhost`
- 本地代理是 OpenAI 兼容接口，例如 CC-Switch

### 2. 检查 Codex 配置

读取配置文件：

```text
%USERPROFILE%\.codex\config.toml
```

重点检查：

```toml
[windows]
sandbox = "unelevated"

[model_providers.custom]
base_url = "http://127.0.0.1:7897/v1"
```

处理规则：

- 如果 `[windows] sandbox = "elevated"`，而问题是本地 loopback / 代理访问失败，优先改成 `"unelevated"`
- 如果 Codex 直接访问 `15721` 被阻断，可以让 Codex 访问 `7897`，再由 `7897` 转发到 `15721`
- 不要整体覆盖 `config.toml`，只改必要字段
- 修改前先备份配置文件

备份示例：

```powershell
Copy-Item "$env:USERPROFILE\.codex\config.toml" "$env:USERPROFILE\.codex\config.toml.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

### 3. 检查本地代理是否正常

如果使用 CC-Switch，常见后端端口是 `15721`：

```cmd
curl http://127.0.0.1:15721/status --max-time 10
```

如果这个端口本身不通，先修复本地代理服务；如果后端端口正常，但 Codex Desktop 连不上，再继续检查 AppContainer、sandbox、防火墙和端口转发。

### 4. 查询 Codex Desktop 的包名

Codex Store / MSIX 更新后，`PackageFullName` 可能变化，所以不要使用旧包名。

查询命令：

```powershell
Get-AppxPackage -Name '*OpenAI*' | Select-Object PackageFullName
```

如果查不到，再扩大范围：

```powershell
Get-AppxPackage | Where-Object { $_.Name -match 'Codex|OpenAI' } | Select-Object Name, PackageFullName
```

### 5. 添加 AppContainer loopback 豁免

使用当前查到的 `PackageFullName`：

```powershell
CheckNetIsolation LoopbackExempt -a -n="<PackageFullName>"
```

检查是否添加成功：

```cmd
CheckNetIsolation LoopbackExempt -s
```

如果 Codex 更新过，需要重新执行这一项，因为包名可能已经变化。

### 6. 设置端口转发

当 Codex Desktop 不能直接访问后端端口时，可以设置一个 Codex-facing 端口，例如：

```text
Codex Desktop -> 127.0.0.1:7897 -> 127.0.0.1:15721 -> CC-Switch
```

添加端口转发：

```cmd
netsh interface portproxy add v4tov4 listenport=7897 listenaddress=127.0.0.1 connectport=15721 connectaddress=127.0.0.1
```

查看端口转发：

```cmd
netsh interface portproxy show all
```

如果 `7897` 已被占用，选择另一个未占用端口，并同步修改 `config.toml` 中的 `base_url`。

### 7. 添加防火墙允许规则

如果 Windows 防火墙阻断 Codex-facing 端口，添加允许规则：

```cmd
netsh advfirewall firewall add rule name="Allow Codex Proxy 7897" dir=in action=allow protocol=tcp localport=7897
```

不要按模糊匹配批量删除防火墙规则。只处理确认属于 Codex sandbox 或当前代理端口的规则。

### 8. 清理 Codex sandbox 阻断规则

仅当这些规则存在，并且确认它们正在阻断本地代理访问时，删除以下规则：

```cmd
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_tcp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_loopback_udp"
netsh advfirewall firewall delete rule name="codex_sandbox_offline_block_outbound"
```

### 9. 清理 Codex sandbox 用户

仅当用户批准，且账户确实存在时，删除 stale sandbox 用户：

```cmd
net user CodexSandboxOffline /delete
net user CodexSandboxOnline /delete
```

注意：Codex 启动时可能会重新创建 sandbox 用户。如果问题复发，需要重新检查这些账户和相关规则。

### 10. 重启 Codex Desktop

优先让用户手动关闭并重新打开 Codex Desktop。

如果用户明确要求自动结束进程，可以执行：

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force
```

这是中断性操作，执行前要提醒用户。

## 验证方法

### 验证后端代理

```cmd
curl http://127.0.0.1:15721/status --max-time 10
```

### 验证 Codex-facing 端口

把 `<configured-model>` 换成当前 `config.toml` 中实际可用的模型名：

```cmd
curl -X POST http://127.0.0.1:7897/v1/responses -H "Content-Type: application/json" -d "{\"model\":\"<configured-model>\",\"input\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_output_tokens\":10}" --max-time 10
```

### 验证 loopback 豁免

```cmd
CheckNetIsolation LoopbackExempt -s
```

确认输出里包含当前 Codex Desktop 的包名。

### 验证端口转发

```cmd
netsh interface portproxy show all
```

确认存在类似规则：

```text
127.0.0.1:7897 -> 127.0.0.1:15721
```

## 安全边界

- 不要固定使用旧的 `PackageFullName`
- 不要整体覆盖 `config.toml`
- 不要批量删除防火墙规则
- 不要按模糊匹配删除 Windows 用户
- 不要在未确认后端代理健康前直接修改大量系统网络配置
- 涉及管理员权限的命令必须让用户知道将要改变什么

## 常见判断

- `15721/status` 不通：优先修复 CC-Switch 或本地代理服务
- `15721/status` 通，但 `7897/v1/responses` 不通：检查 `portproxy` 和防火墙
- `7897/v1/responses` 通，但 Codex Desktop 仍重连：检查 `config.toml`、AppContainer loopback 豁免和 Codex 包名
- Codex 更新后问题复发：重新查询 `PackageFullName` 并重新添加 loopback 豁免
- `localhost` 表现不稳定：优先使用明确的 `127.0.0.1`



