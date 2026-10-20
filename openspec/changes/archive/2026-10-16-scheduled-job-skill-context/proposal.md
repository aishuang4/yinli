# Proposal

## Why

定时 Job 现在能冻结 Skill ID 和工具合同，但 scheduled firing 只把 Skill 放进运行目录，没有把对应的 `SKILL.md` 稳定地装入本轮 Agent Context。模型因此仍要临时发现并调用 `Skill` 工具，导致同一个 Job 在不同 firing 中执行路径不稳定，尤其在无人值守场景下可能跳过关键步骤。

需要把 Job 的 Context 组装从“只解析能力目录”升级为“确认时冻结、到期时校验、首轮调用前预加载”的明确策略，同时保留现有 AgentScope Context Governance、Project 资源白名单和权限边界。

## What Changes

- 为 Job 增加明确的 required Skill 语义：Job 只使用创建/重新确认时明确冻结的 Skill，不因 `availableToAllAgents` 或 Agent 当前配置变化而自动追加。
- 在 Job 确认时保存每个 required Skill 的可验证版本信息（至少包括 Skill ID、包路径/来源和 `SKILL.md` 内容 hash），并在每次 firing 前重新校验安装、启用、Project binding、Job Agent 可用性和 hash。
- 在 scheduled firing Context 中合并并校验 `job.skill_ids` 与 capability contract 中声明的 Skill，避免可行性检查认领了 Skill 但实际执行遗漏。
- 在首个模型调用前将 required Skill 的 `SKILL.md` 正文写入 AgentScope 会话 Context 的 `<activated-skill>` HintBlock，并通过现有 Skill Context middleware 在压缩、截断和实例重建后恢复；正文不写入全局 system prompt。
- 增加 Job Context policy，默认每次 firing 使用 fresh session；如未来需要跨次传递，只允许显式的上一轮结果或滚动摘要，禁止无限继承完整历史对话。
- 为单 Skill、总 Skill Context 和模型可用 Context 设置预算；超限必须失败并进入 firing 错误/通知，或要求拆分 Skill，不能静默截断成“成功”。`references/` 与 `scripts/` 仍按需读取/执行。
- Skill 删除、停用、解绑、权限变化或 hash 变化时，firing 必须失败或暂停并给出可操作原因，要求重新确认；禁止静默降级为不带 Skill 执行。
- 预加载 Skill 不能绕过 Job 的 Bash、HTTP、MCP、知识库、filesystem scope、审批策略或无人值守 denylist；Job 仍不能注入 `AskUser`、Plan、DelegateTask 或 Job 管理工具。
- 为 Skill Context 装配、版本校验、Context budget、fresh/previous-result policy 和失败恢复增加 firing audit/trace、通知语义及单元/集成测试。
- 更新 Job 能力合同和 Skill 规范，明确 Job Runtime 与合格 Chat Runtime 的 Skill 注入边界。

## Capabilities

### New Capabilities

- `scheduled-job-skill-context`: 定义 Job required Skill 的冻结、版本校验、scheduled firing Context 预加载、Context policy、预算、审计和失败恢复行为。

### Modified Capabilities

- `skills`: 补充 Job Runtime 的 Skill 规则：`availableToAllAgents` 不自动作用于 `role=job`；只有 Job 明确冻结的 required Skill 才能预加载；正文进入 scheduled firing Context 而不是全局 system prompt；预加载不能绕过 Project binding、Agent 权限和工具 scope。

## Impact

- 后端领域模型和存储：`scheduler.models.ScheduledJob`、能力合同及 scheduled job/firing 持久化需要保存 required Skill 版本信息和 Context policy，并兼容现有存量 Job。
- scheduled Context 装配：`run_context.py`、`scheduled_launch.py`、`firing.py` 需要在运行前完成 Skill 解析、hash 校验、forced skill 预激活和错误收口。
- Runtime 集成：复用 `skill_context_middleware.py`、`activate_forced_skills()` 和现有 `runtime_governance`，不新增独立 Runtime，不把 Skill 正文永久拼到 system prompt。
- 能力检查与资源裁剪：`capability_contract.py`、feasibility 逻辑和 Project resource access 需要使用同一份冻结 Skill 集合。
- 可观测性与通知：Skill 校验失败、预算超限和版本漂移要落到 firing error/trace，并沿现有 scheduler notification 通道通知用户。
- 文档与测试：更新 Job/Skill 契约、架构说明、迁移说明以及 scheduled firing、压缩恢复、权限边界和存量数据兼容测试。
