# 引力 YINLI 文档索引

> 状态：2026-08-05 生效。本文是 `docs/` 的唯一索引。

## 事实与规范

首次参与项目时按以下顺序阅读：

1. `AGENTS.md`：当前项目事实、架构约束和开发代理规范。
2. `docs/architecture-overview.md`：当前仍在线的系统架构。
3. `docs/development-guidelines.md`：前后端、Runtime、SQLite、测试和文档规范。
4. `docs/project-progress-handoff.md`：当前完成度、验证结果和已知缺口。
5. `docs/project-todo.md`：按优先级维护的后续任务。

## 稳定契约

- `docs/chat-sse-event-contract.md`：Chat `RUNTIME_EVENT: RuntimeFrame` 与 DisplaySnapshot V4 稳定契约。变更协议前必须先更新。
- `docs/workflow-event-contract.md`：Dynamic Workflow 独立事件契约，不属于 Chat SSE。
- `docs/group-studio-event-contract.md`：群聊工作室独立 SSE 契约，不属于 Chat SSE / Workflow。前端只依赖该契约中的稳定事件。

## 在线领域设计

- `docs/group-chat-studio-design.md`：Agent 群聊工作室（Group Chat Studio）设计实现方案。P0 已落地；当前架构事实以 `AGENTS.md` 与 `docs/architecture-overview.md` 为准。独立于 Chat Team Runtime 与 Chat SSE。
- `docs/group-studio-intake-and-workboard.md`：群聊工作室增量方案（评审通过，待实现）。`drafting` 改为用户 ↔ 主持人 intake 对谈，人类确认密度后才开会；接手后用 FIFO 点名队列 + `group_work_items` 认领，不做真并行。实现前必须先改 `docs/group-studio-event-contract.md`。
- `docs/team-runtime-redesign.md`：一次性 TeamRun、动态 Member、Task List、Mailbox 和 file-first 状态。
- `docs/subagent-delegation-design.md`：Subagent 文件定义、attached fork/join、wait/read、父结果消费、权限裁剪和任务恢复边界。
- `docs/memory-system-design.md`：新记忆系统、Markdown 记忆、`memory_nodes`、向量检索和 Skill 自进化。
- `docs/notifications.md`：通知通道抽象、应用内 inbox、完成时提醒框 / 系统通知、独立 SSE 与外部通道扩展方式。不经过 Chat SSE。
- `docs/scheduled-jobs-design.md`：对话登记定时任务、待确认后启用、到期执行与通知。
- `docs/scheduled-jobs-v2-agent-activation.md`：Job Agent、创建前能力门禁、必注入 / 可选注入工具分层。
- `docs/default-websearch-job-agent-plan.md`：已实施的系统默认 WebSearch Job Agent 初始化、跨 Workspace 可用、系统资源保护与能力边界。

## 已实施设计

- `docs/event-stream-architecture-redesign.md`：已实施的通用 Event Stream V3 前后端设计；后端采用全局有序 Domain/Trace、单 ProjectionRunner、per-kind projector、Block 生命周期协调器、周期 Snapshot 和原子 projection store，前端采用 `open/delta/close + seq`、DisplaySnapshot V4 和 Block module registry。

## 后续路线

- `docs/debian-http-deployment-v1.md`：Debian 单机 HTTP 临时部署指南，覆盖 Nginx、systemd、持久化、更新与回滚；不属于生产安全基线。
- `docs/team-member-lifecycle-v2.md`：Team Member 长生命周期、TaskAttempt、多轮返工、Mailbox 唤醒和 Reviewer 闭环方案。
- `docs/agentscope-2.0.5-upgrade-plan.md`：AgentScope `2.0.3 -> 2.0.5` Runtime 基线迁移实施记录、兼容改造、验收结果、发布门禁与回滚方案。
- `docs/agentscope-2.0.5-manual-test-script.md`：AgentScope `2.0.5` 人工验收步骤、精确提示词、预期结果和发布阻断条件。
- `docs/dynamic-workflows-plan.md`：Dynamic Workflow 的 Script Review、Runtime Cockpit 和 AgentScope Bridge 路线。
- `docs/electron-packaging-plan.md`：桌面正式打包、签名、公证、数据目录和 Windows 验证。
- `docs/windows-data-uninstall-and-settings-v1.md`：Windows 卸载时保留/删除用户数据、系统设置目录入口和实现验收。
- `docs/windows-data-uninstall-test-case.md`：Windows 用户数据与卸载的可执行测试用例。

## 维护规则

- 当前事实只写入 `AGENTS.md`、`architecture-overview.md` 和 `project-progress-handoff.md`。
- 任务只写入 `project-todo.md`，专题文档不复制完整 TODO。
- 事件字段只在对应稳定契约中定义，其他文档只引用。
- 审计记录和已完成的阶段草案在结论并入当前文档后删除，不设置归档目录。
- 旧独立 Plan Runtime（`runtime.kind=plan`）、旧独立 Subagent Runtime（`runtime.kind=subagent`）、旧 Memory Runtime 和持久 Team 管理不得恢复。当前 Plan Mode middleware 与 `DelegateTask` Subagent 委托属于 ReAct 内部在线能力，不在删除范围内。
