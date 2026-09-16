# 引力 YINLI 文档索引

> 状态：2026-09-16 整理。本文是 `docs/` 的唯一索引。已落地结论进架构总览后，阶段草案删除，不设归档目录。

## 事实与规范

首次参与项目时按以下顺序阅读：

1. `AGENTS.md`：当前项目事实、架构约束和开发代理规范。
2. `docs/architecture-overview.md`：当前仍在线的系统架构。
3. `docs/development-guidelines.md`：前后端、Runtime、SQLite、测试和文档规范。
4. `docs/project-progress-handoff.md`：当前完成度、验证结果和已知缺口。
5. `docs/project-todo.md`：按优先级维护的后续任务。

## 稳定契约

变更协议前必须先改对应文件。前端只依赖契约中的稳定事件。

- `docs/chat-sse-event-contract.md`：Chat `RUNTIME_EVENT: RuntimeFrame` 与 DisplaySnapshot V4。
- `docs/workflow-event-contract.md`：Dynamic Workflow 独立事件，不属于 Chat SSE。
- `docs/group-studio-event-contract.md`：群聊工作室独立 SSE，不属于 Chat SSE / Workflow。

## 在线领域设计

这些是现行行为的专题合同，不是待做路线图。

- `docs/team-runtime-redesign.md`：一次性 TeamRun、动态 Member、Task List、Mailbox、file-first 状态。
- `docs/subagent-delegation-design.md`：Subagent 文件定义、attached fork/join、wait/read、权限裁剪。
- `docs/memory-system-design.md`：新记忆系统、Markdown 记忆、`memory_nodes`、向量检索和 Skill 自进化。
- `docs/file-catalog-v1.md`：系统定义文件化。Agent / Skill / MCP / HTTP / Job 以约定目录为权威。
- `docs/chat-model-selection-v1.md`：Chat / Sandbox 选模型，`models.json` 唯一配置源。
- `docs/scheduled-jobs-v2-agent-activation.md`：Job Agent 能力合同、创建前门禁、到期按合同装配 toolkit。
- `docs/notifications.md`：通知通道、应用内 inbox、完成提醒；不经过 Chat SSE。

群聊现行阶段机、Event Stream V3、AgentScope `2.0.5` 基线和定时任务控制面已写进 `docs/architecture-overview.md`。系统默认 Job Agent `system-websearch-job` 的现网规则也在总览，不再另留实施计划。

## 后续路线

只放尚未落地、仍准备做的方案。

- `docs/group-studio-v2.md`：群聊改成「使用者组的群」。房间留下、主持流程拆掉。现行实现仍以架构总览为准。
- `docs/team-member-lifecycle-v2.md`：Team Member 长生命周期、TaskAttempt、Mailbox 唤醒和 Reviewer 闭环。在线基线仍是 `team-runtime-redesign.md`。
- `docs/dynamic-workflows-plan.md`：Dynamic Workflow 的 Script Review、Runtime Cockpit 和 AgentScope Bridge。
- `docs/electron-packaging-plan.md`：桌面正式打包、签名、公证、数据目录和双平台验证。

## 发布与验收

- `docs/beta-release-checklist.md`：`0.1.0-beta.*` 桌面内测发布前检查。
- `docs/windows-data-uninstall-and-settings-v1.md`：Windows 卸载保留/删除用户数据、设置页数据目录入口。代码已落地，待 Windows 真机验收。
- `docs/windows-data-uninstall-test-case.md`：上述能力的可执行测试用例。

## 维护规则

- 当前事实只写入 `AGENTS.md`、`architecture-overview.md` 和 `project-progress-handoff.md`。
- 任务只写入 `project-todo.md`，专题文档不复制完整 TODO。
- 事件字段只在对应稳定契约中定义，其他文档只引用。
- 已完成的阶段草案在结论并入当前文档后删除，不设置归档目录。
- 旧独立 Plan Runtime（`runtime.kind=plan`）、旧独立 Subagent Runtime（`runtime.kind=subagent`）、旧 Memory Runtime 和持久 Team 管理不得恢复。当前 Plan Mode middleware 与 `DelegateTask` Subagent 委托属于 ReAct 内部在线能力，不在删除范围内。
