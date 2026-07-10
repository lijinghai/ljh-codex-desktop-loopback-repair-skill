# Security Policy

## Supported Versions

当前仅维护 `main` 分支上的最新版本。

## Reporting a Vulnerability

如果你发现这个 skill 中存在可能导致不安全系统修改、权限误用、误删规则或泄露敏感配置的内容，请通过 GitHub issue 或安全报告渠道反馈。

报告时请尽量包含：

- 触发问题的 prompt 或步骤
- 相关 Windows / Codex Desktop 环境
- 涉及的命令
- 可能造成的影响
- 建议的修复方向

## Safety Scope

这个 skill 可能指导 Codex 检查或修改 Windows 网络相关配置。任何会改变系统状态的命令都应在用户确认后执行，尤其是：

- `netsh`
- `CheckNetIsolation`
- `net user`
- 防火墙规则修改
- 停止 Codex Desktop 进程

不要在 issue 或 PR 中提交真实 API key、代理密钥、完整用户目录隐私信息或敏感日志。
