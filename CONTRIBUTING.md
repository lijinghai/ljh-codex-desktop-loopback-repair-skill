<!--
Author: 算个文科生吧
Contact: lijinghailjh@163.com
Project: ljh_codex-desktop-loopback-repair_skill
-->

# Contributing

感谢你考虑贡献这个 Codex Skill。

## 贡献方式

- 提交 bug report：描述 Codex Desktop 的症状、Windows 版本、本地代理类型、端口、已尝试的命令和关键输出。
- 提交修复方案：尽量解释问题位于哪一层，例如 Codex 配置、AppContainer loopback、portproxy、防火墙或 sandbox 用户。
- 改进文档：让使用步骤更清晰，尤其是管理员权限、风险提示和验证步骤。

## Pull Request 要求

- 保持 `SKILL.md` 简洁，避免加入和修复流程无关的内容。
- 不要写死用户机器上的 `PackageFullName`、用户名、模型名或端口，除非明确标注为示例。
- 系统修改命令必须包含安全边界和验证步骤。
- 修改后运行：

```powershell
python .\scripts\validate_skill.py .
```

## 编写原则

- 先诊断，再修复。
- 使用当前机器发现到的真实值，不盲目套用固定值。
- 优先做最小必要修改。
- 对需要管理员权限或会改变系统状态的命令保持明确提示。

