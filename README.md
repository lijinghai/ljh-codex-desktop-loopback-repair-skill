<!-- 算个文科生吧，联系方式WX：RabbitRobot2025 -->

# ljh-codex-desktop-loopback-repair-skill

[English](#english) | [安装](#安装) | [使用方法](#使用方法) | [修复流程](#修复流程)

一个用于修复 Windows 上 Codex Desktop 本地代理/回环访问问题的 Codex Skill。把常见故障的诊断和修复流程整理成 Skill + 本地 Web 控制台，遇到问题时点按钮或让 AI 自动处理。

## 适用场景

| # | 症状 | 根因 | 修复方向 |
|---|------|------|----------|
| 1 | 一直 Reconnecting / stream disconnected | CC-Switch 重启后把 `sandbox` 写回 `elevated`，WFP 防火墙阻断 15721 | 切到 `unelevated` + 安装守护 |
| 2 | 413 Payload Too Large | 会话上下文超过上游 10 MB 限制 | 归档 session，开新会话 |
| 3 | CC-Switch provider 丢失 / No credentials | CC-Switch 崩溃后凭据未恢复 | 等待 30s 自动恢复，或重启 CC-Switch |
| 4 | 上游 API 全部超时 | 上游端点不可达（如 `llm.slashrobot.top` 宕机） | 切换供应商或修复数据库 |
| 5 | CC-Switch 返回 `No active credentials for provider: openai` | **第三方代理服务端凭证过期，本地无法修复** | 联系代理管理员或换供应商 |
| 6 | Codex Provider 缺少 base_url 配置 | CC-Switch Codex provider SQLite ???? upstream `base_url` | ?? `scripts/fix_codex_provider_base_url.py` ?? provider + `proxy_live_backup` |

## 安装

把本仓库复制到 Codex skills 目录：

```powershell
Copy-Item -Recurse -Force .\ljh-codex-desktop-loopback-repair-skill "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill"
```

重启 Codex Desktop 生效。

## 使用方法

### 方式一：Web 控制台（推荐）

```bat
start-repair-web.bat
```

打开 `http://127.0.0.1:8765/`，点击 **推荐一键修复**。需要清理防火墙/Sandbox/Portproxy 时请以管理员权限启动。

### 方式二：让 AI 自动修复

在 Codex 会话中说：

```
用 ljh-codex-desktop-loopback-repair-skill 修复我的 Codex
```

AI 会自动诊断并执行修复。

### 方式三：手动紧凑修复（管理员 PowerShell）

最常见场景 — CC-Switch 重启导致 sandbox 死锁，三步解决：

```powershell
# 1. 停止 Codex
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force

# 2. 修改 sandbox → unelevated
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

安装 Sandbox 守护防止复发：

```powershell
$scripts = "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill\scripts"
Copy-Item "$scripts\sandbox-guard.ps1" "$env:USERPROFILE\.codex\sandbox-guard.ps1" -Force
Copy-Item "$scripts\sandbox-guard.vbs" "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\sandbox-guard.vbs" -Force
Start-Process powershell.exe -ArgumentList '-WindowStyle Hidden -ExecutionPolicy Bypass -File', "$env:USERPROFILE\.codex\sandbox-guard.ps1" -WindowStyle Hidden
```

## 修复流程

```
停止 Codex → 诊断 (sandbox, CC-Switch, loopback, portproxy, upstream, session)
    ↓
sandbox=elevated? → 策略 A：unelevated + portproxy 7897→15721
    ↓
CC-Switch 正常? → 不正常：等 30s 自动恢复，或重启
    ↓
上游可达? → 不可达：切换供应商或修复数据库
    ↓
413? → 开新会话（上下文太大）
    ↓
启动 Codex → 验证
```

## 两种修复策略

| 策略 | Sandbox | Portproxy | 兼容 CC-Switch | 复杂度 |
|------|---------|-----------|---------------|--------|
| **A（推荐）** | unelevated | 7897→15721 | 是 | 低 |
| B（遗留） | elevated | 7897→15721 | 否（冲突） | 高 |

> Codex Desktop v26.x 强制使用 7897 端口 — 即使 unelevated 也需要 portproxy。

## Web 控制台按钮

| 按钮 | 功能 |
|------|------|
| 刷新状态 | 读取 config.toml、CC-Switch、Loopback、Portproxy、代理变量、大 session |
| 完整诊断 | 输出诊断结果和修复建议 |
| **推荐一键修复** | 停止 Codex → 删代理变量 → unelevated → Loopback → 清 Sandbox → Portproxy → 重启 CC-Switch |
| 切到 Strategy A | 仅应用 unelevated 策略，不含 CC-Switch 恢复 |
| 安装 Sandbox 守护 | 安装守护脚本并加入开机启动 |
| 修复 413 上下文 | 归档当天 session 文件 |
| 删除 HTTP_PROXY | 清除可能劫持 CC-Switch 出站的代理变量 |
| 重启 CC-Switch | 停止并重启 CC-Switch |
| 测试 CC-Switch API | 发送小请求验证转发 |
| 检查上游连通 | 直接测试上游 API 端点 |
| CC-Switch 深度恢复 | 停止 CC-Switch → 清 backup → 切换供应商 → 重启 |

## 验证成功标准

```powershell
Get-Content "$env:USERPROFILE\.codex\config.toml" | Select-String 'sandbox|base_url'
curl.exe -s http://127.0.0.1:15721/status --max-time 5
netsh interface portproxy show all
```

| 检查项 | 期望值 |
|--------|--------|
| sandbox | `unelevated` |
| base_url | `http://127.0.0.1:15721/v1` |
| CC-Switch | running，provider 非 null |
| Portproxy | `7897 → 15721` |
| Codex 行为 | 无重连，无 413 |

## 安全提示

- 修改 `config.toml` 前自动备份
- 优先策略 A，不跟 CC-Switch 的 `base_url` 对抗
- CC-Switch Live Takeover 写回 `base_url = "http://127.0.0.1:15721/v1"` 是**正常行为**
- 413 不是网络问题，优先清理 session 或开新会话
- 清理防火墙/Sandbox/Portproxy 需要管理员权限

## 仓库结构

```
.
├── SKILL.md              # Skill 定义（Codex 加载此文件）
├── Codex修复方案.md        # 中文详细修复指南
├── README.md             # 本文件
├── agents/
│   └── openai.yaml       # Agent 接口配置
├── scripts/
│   ├── start-repair-web.ps1  # Web 控制台后端
│   ├── sandbox-guard.ps1     # Sandbox 守护脚本
│   ├── sandbox-guard.vbs     # 守护启动器（隐藏窗口）
│   ├── start-codex.bat       # 一键启动器
│   └── validate_skill.py     # Skill 校验
├── web/
│   └── repair.html       # Web 控制台前端
└── .github/workflows/    # CI 校验
```

## English

A Codex Skill for diagnosing and repairing Codex Desktop loopback proxy failures on Windows.

### Quick Fix (Admin PowerShell)

Most failures follow one pattern: CC-Switch restarts → writes `sandbox = "elevated"` → WFP blocks port 15721 → deadlock.

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*codex*' } | Stop-Process -Force
$config = "$env:USERPROFILE\.codex\config.toml"
Copy-Item $config "$config.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
$text = Get-Content $config -Raw -Encoding UTF8
$text = $text -replace 'sandbox\s*=\s*"[^\"]+"', 'sandbox = "unelevated"'
[IO.File]::WriteAllText($config, $text, [Text.UTF8Encoding]::new($false))
$pkg = (Get-AppxPackage -Name '*OpenAI*').PackageFullName
if ($pkg) { CheckNetIsolation LoopbackExempt -a -n="$pkg" }
netsh interface portproxy delete v4tov4 listenport=7897 listenaddress=127.0.0.1
netsh interface portproxy add v4tov4 listenport=7897 listenaddress=127.0.0.1 connectport=15721 connectaddress=127.0.0.1
```

### Web Panel

```bat
start-repair-web.bat
```

Open `http://127.0.0.1:8765/`, click **推荐一键修复**.

### AI Auto-Repair

In a Codex session: `Use ljh-codex-desktop-loopback-repair-skill to fix my Codex.`

### Five Failure Modes

1. **Sandbox deadlock** — elevated sandbox WFP blocks 15721, CC-Switch forces base_url to 15721 → Reconnecting loop
2. **413 Payload Too Large** — context > 10 MB → archive sessions
3. **CC-Switch crash** — provider becomes null or "No credentials" → wait 30s or restart
4. **Upstream API down** — endpoint unreachable → switch provider or repair DB
5. **Upstream credential expired** — third-party proxy returns `No active credentials for provider: openai` → ***server-side, NOT locally fixable***, contact proxy admin

#Additional repair helper: `scripts/fix_codex_provider_base_url.py` repairs CC-Switch Codex provider configs that lost upstream `base_url`.

## License

MIT. See [LICENSE](LICENSE).

Author: 算个文科生吧 | lijinghailjh@163.com | WeChat: RabbitRobot2025
