<!--
Author: 算个文科生吧
Contact: lijinghailjh@163.com
Project: ljh_codex-desktop-loopback-repair_skill
-->

# ljh_codex-desktop-loopback-repair_skill

一个面向 Windows 用户的开源 Codex Skill，用来诊断和修复 Codex Desktop 无法连接本地 API 代理的问题。

如果你的 Codex Desktop 一直显示 `Reconnecting`、`stream disconnected`，或者无法访问 `127.0.0.1` / `localhost` 上的本地 OpenAI 兼容代理，这个 skill 会引导 Codex 按层排查：Codex 配置、Windows AppContainer loopback 豁免、端口转发、防火墙规则、sandbox 用户和本地代理健康状态。

## 作者信息

- 作者：算个文科生吧
- 联系方式：lijinghailjh@163.com
- 项目类型：Codex Skill / Windows 本地代理连接修复方案
- 适用平台：Windows + Codex Desktop

## 适用场景

- Codex Desktop 在 Windows 上无法连接本地 API 代理
- Codex Desktop 显示 `Reconnecting` 或 `stream disconnected`
- 本地代理是 CC-Switch 或其他 OpenAI 兼容服务
- 本地代理后端端口类似 `15721`，Codex-facing 端口类似 `7897`
- Windows Store / MSIX AppContainer loopback 隔离导致本机回环地址不可达
- Codex sandbox 防火墙/WFP 规则影响本地连接
- Codex 更新后，原本可用的本地代理连接突然失效

## 它解决的核心问题

Windows Store / MSIX 应用默认存在 AppContainer 网络隔离，可能无法直接访问 `127.0.0.1`。Codex Desktop 又可能叠加 sandbox 防火墙/WFP 规则，导致本地代理明明正常运行，但 Codex Desktop 仍然无法连上。

这个 skill 的处理思路是：

1. 先确认本地代理本身是否健康
2. 再检查 Codex 的 `config.toml`
3. 然后检查 AppContainer loopback 豁免
4. 再检查 `netsh portproxy` 和防火墙规则
5. 最后处理 Codex sandbox 用户和阻断规则

## 仓库结构

```text
ljh_codex-desktop-loopback-repair_skill/
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

## 安装方式

把整个项目文件夹复制到你的 Codex skills 目录。

推荐安装路径：

```text
%USERPROFILE%\.codex\skills\ljh-codex-desktop-loopback-repair-skill
```

PowerShell 示例：

```powershell
Copy-Item -Recurse -Force .\ljh_codex-desktop-loopback-repair_skill "$env:USERPROFILE\.codex\skills\ljh-codex-desktop-loopback-repair-skill"
```

安装后重启 Codex Desktop，或重新打开一个 Codex 会话，让 skill 元数据重新加载。

## 使用方式

当 Codex Desktop 出现本地代理连接问题时，在新的 Codex 对话里输入：

```text
使用 $ljh-codex-desktop-loopback-repair-skill 帮我诊断并修复 Codex Desktop 在 Windows 上一直 Reconnecting 或 stream disconnected 的问题。
```

如果你使用的是 CC-Switch，可以写得更具体：

```text
我使用的是本地 CC-Switch 代理，后端端口是 15721。请使用 $ljh-codex-desktop-loopback-repair-skill 检查并修复 Codex Desktop 无法连接本地代理的问题。
```

如果你希望 Codex 在修改系统网络规则前先说明风险并请求确认，可以使用：

```text
使用 $ljh-codex-desktop-loopback-repair-skill 帮我修复 Codex Desktop 在 Windows 上一直 Reconnecting / stream disconnected 的问题。

请先检查我的 .codex/config.toml、本地代理端口、AppContainer loopback 豁免、netsh portproxy、防火墙规则和 Codex sandbox 用户。

如果需要修改系统网络规则，请先说明要执行的命令，并在需要管理员权限时请求我批准。修复后请验证 127.0.0.1 本地代理和 Codex-facing 端口是否都能正常访问。
```

## Skill 会检查什么

- `%USERPROFILE%\.codex\config.toml`
- `[windows] sandbox` 配置
- `[model_providers.*].base_url`
- 本地代理健康状态，例如 `http://127.0.0.1:15721/status`
- Codex Desktop 当前 `PackageFullName`
- `CheckNetIsolation LoopbackExempt` 配置
- `netsh interface portproxy` 端口转发规则
- Windows 防火墙中 Codex sandbox 相关规则
- `CodexSandboxOffline` / `CodexSandboxOnline` sandbox 用户

## 安全原则

这个项目不会鼓励盲目执行固定命令。它的目标是让 Codex 做最小必要修复。

- 修改 `config.toml` 前先备份
- 不固定使用旧的 `PackageFullName`
- 不整体覆盖用户配置
- 不按模糊匹配删除防火墙规则
- 不按模糊匹配删除 Windows 用户
- 涉及 `netsh`、`CheckNetIsolation`、`net user`、停止进程等操作时，应说明影响并按环境要求请求管理员权限或用户批准

## 本地校验

运行仓库内置的轻量校验脚本：

```powershell
python .\scripts\validate_skill.py .
```

校验内容包括：

- `SKILL.md` 是否存在
- frontmatter 是否包含 `name` 和 `description`
- skill 名称是否符合 lowercase hyphen-case
- `agents/openai.yaml` 是否存在
- 是否仍残留 TODO 占位符

## 贡献

欢迎提交 issue 和 pull request。提交前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，并尽量提供可复现信息。

## 许可证

本项目使用 [MIT License](LICENSE)。

