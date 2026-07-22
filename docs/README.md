# 引力 YINLI 文档索引

> 状态：2026-07-14 生效。本文是 `docs/` 的唯一索引。

## 事实与规范

首次参与项目时按以下顺序阅读：

1. `AGENTS.md`：当前项目事实、架构约束和开发代理规范。
2. `docs/architecture-overview.md`：当前仍在线的系统架构。
3. `docs/development-guidelines.md`：前后端、Runtime、SQLite、测试和文档规范。
4. `docs/project-progress-handoff.md`：当前完成度、验证结果和已知缺口。
5. `docs/project-todo.md`：按优先级维护的后续任务。

## 稳定契约

- `docs/chat-sse-event-contract.md`：Chat SSE、RunState、DisplaySnapshot、TeamSnapshot 稳定契约。变更事件前必须先更新。
- `docs/workflow-event-contract.md`：Dynamic Workflow 独立事件契约，不属于 Chat SSE。

## 在线领域设计

- `docs/team-runtime-redesign.md`：一次性 TeamRun、动态 Member、Task List、Mailbox 和 file-first 状态。
- `docs/subagent-delegation-design.md`：Subagent 文件定义、`DelegateTask`、权限裁剪和 invocation 审计。
- `docs/memory-system-design.md`：新记忆系统、Markdown 记忆、`memory_nodes`、向量检索和 Skill 自进化。

## 待实施设计

- `docs/agentscope-native-skills-migration.md`：将应用层 Skill 全文注入迁移到 AgentScope 原生 `LocalSkillLoader`、统一 `Skill` viewer 和渐进式披露的第一版设计。
- `docs/subagent-task-runtime-redesign.md`：将 Subagent 从 blocking 委托升级为适合单进程桌面应用的持久任务实体，覆盖 transcript、Supervisor、通信、分级恢复和前端事件投影。

## 后续路线

- `docs/workspace-project-design.md`：Workspace / Project 归属、资源绑定、RuntimeContext 和前端上下文切换设计；Phase 0 至 Phase 3 已实现。
- `docs/dynamic-workflows-plan.md`：Dynamic Workflow 的 Script Review、Runtime Cockpit 和 AgentScope Bridge 路线。
- `docs/electron-packaging-plan.md`：桌面正式打包、签名、公证、数据目录和 Windows 验证。

## 维护规则

- 当前事实只写入 `AGENTS.md`、`architecture-overview.md` 和 `project-progress-handoff.md`。
- 任务只写入 `project-todo.md`，专题文档不复制完整 TODO。
- 事件字段只在对应稳定契约中定义，其他文档只引用。
- 已完成的阶段草案合并结论后删除，不设置归档目录。
- 旧独立 Plan Runtime（`runtime.kind=plan`）、旧独立 Subagent Runtime（`runtime.kind=subagent`）、旧 Memory Runtime 和持久 Team 管理不得恢复。当前 Plan Mode middleware 与 `DelegateTask` Subagent 委托属于 ReAct 内部在线能力，不在删除范围内。
