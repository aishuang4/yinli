# Agent Workstation 文档索引

> 状态：2026-07-08 更新后生效。本文是 `docs/` 的入口，避免旧设计文档继续误导开发。

## 必读文档

- `AGENTS.md`：代理协作规范和当前项目事实的唯一真实入口。
- `docs/development-guidelines.md`：开发规范，覆盖前端、后端、SQLite、Agent Runtime、测试和文档要求。
- `docs/architecture-overview.md`：当前架构总览，只描述仍在线的核心链路。
- `docs/chat-sse-event-contract.md`：Chat / RunState / DisplaySnapshot 稳定契约。新增或变更 Chat 事件前必须先改这里。
- `docs/memory-system-design.md`：新记忆系统设计与实现进度，覆盖历史记忆（底层 `memory_nodes`）、Markdown 记忆、向量存储、Memory 页面和 Skill 自进化后续约束。
- `docs/subagent-delegation-design.md`：新版 Subagent 委托能力设计，定义 Markdown + YAML frontmatter 文件定义、ReAct 内 `DelegateTask` 工具、invocation 审计、`SUBAGENT_*` 事件和分期边界。
- `docs/team-runtime-redesign.md`：新版 Team Runtime 设计草案，定义 TeamRun、TeammateSession、Task List、Mailbox、file-first 状态和旧 Team Runtime 禁用边界。
- `docs/dynamic-workflows-plan.md`：Dynamic Workflow 决策版改造设计，定义独立工作空间、Script Review、Runtime Cockpit、AgentScope Bridge 和后续 LangGraph 接入边界。
- `docs/project-progress-handoff.md`：当前项目状态、启动验证方式和协作入口。
- `docs/project-todo.md`：后续任务队列。

## 暂保留的专题文档

- `docs/electron-packaging-plan.md`：桌面端正式打包、签名、公证、内置后端运行时仍有未完成项。
- `docs/workflow-event-contract.md`：Dynamic Workflow 独立 SSE 事件契约。该契约不属于 Chat SSE。

## 已清理的文档类型

- 旧 Plan Runtime / Subagent Runtime 文档：对应 Runtime 已下线，后续需要重新设计时新建文档。
- 旧三层记忆设计文档：旧记忆模块已删除，当前只保留数据库兼容字段，不能作为运行时能力引用；新记忆能力以 `docs/memory-system-design.md` 和当前代码为准。
- 已完成或被 `chat-sse-event-contract.md` 覆盖的临时执行计划：避免多份契约并存。
- 空 README、Vite 模板 README：根目录 README 已覆盖项目启动信息。
