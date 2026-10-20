# Proposal

> 状态：占位。只有 proposal；specs / design / tasks 在 `restructure-code-layering` W4 完成、且"loop 由谁拥有"的决定做出之后再写。

## Why

同一个运行时事实（比如"工具调用开始"）在后端有四层手写表示：AgentScope 事件 → SourceEvent → RuntimeEvent → DomainEvent → RuntimeFrame，中间三层各有自己的事件名（仅"工具调用开始"就有 `TOOL_CALL_START`、`TOOL_CALL_STARTED`、`tool.call.started`、kind `"tool"` 四种写法）、自己的 `dict[str, Any]` 负载和自己的持久化表（三份日志）。翻译层用 82 处 `event_type ==` 字符串比较和两个圈复杂度超过 100 的函数（`observe_source_event`、`_team_event`）把它们连起来。任何新增一种事件都要改四处，漏改一处就是运行时故障。

同时，`stream_leader_agent_reply`（620 行、17 个参数）把 HITL、Plan、Skill、Subagent、Team、恢复、持久化、投影全部写在同一个循环里，这些能力通过共享上下文对象互相影响。`restructure-code-layering` W7b 的拆分只降低了单函数复杂度，没有解除耦合。

## What Changes

- 合并 SourceEvent / RuntimeEvent / DomainEvent 为单一的类型化 `RunEvent`（每种事件一个 Pydantic 模型，字符串事件名只在 `RunEvent.type` 一处定义）；三份持久化日志收成一份 `run_events` 表，观察记录、Trace、Snapshot 都从它派生。
- loop 只做驱动与状态机：从 Agent 收事件、写 `RunEvent`、广播给订阅者。投影、持久化、Trace、Team outbox、Skill 生命周期、记忆写入各自成为并行订阅者，互相不可见。
- 删除 `observe_source_event` 与 `_team_event` 及其 C901 豁免；`restructure-code-layering` W7a 的金标测试和 `define-runtime-contracts` 的端到端金标在改造前后必须逐字节一致。
- RuntimeFrame / DisplaySnapshot 契约不变（`contracts/` 无改动）；前端不感知。
- 待决定（design 阶段）：loop 由本仓库继续拥有（现状，重写为小驱动器），还是交给 AgentScope `ReActAgent` + hooks（参考 DeerFlow 把 loop 交给 LangGraph、自己只做 StreamBridge + RunJournal 订阅者的做法）。这个决定影响 HITL 中断/恢复的实现方式，必须在写 specs 前用一个 spike 验证 AgentScope 2.0 的 hooks 能否表达现有全部 HITL 场景。

## Capabilities

### New Capabilities

- `run-event-model`: 单一类型化运行时事件与单份持久化日志。
- `runtime-subscribers`: loop 与投影/持久化/Trace/Team 等能力之间的订阅者契约。

### Modified Capabilities

- `runtime-pipeline-golden`（由 `define-runtime-contracts` 引入）：金标不变，增加"改造前后逐字节一致"的场景。

## Impact

- 前置：`restructure-code-layering` W4 完成（引擎已在 `src/runtime/chat/`）；`define-runtime-contracts` 已归档；loop 所有权 spike 完成并记录结论。可与 `split-storage-by-aggregate` 并行（`run_events` 表由本变更拥有，那边不碰）。
- 涉及：`src/runtime/chat/` 下事件归一、投影、持久化、Team outbox；`storage` 中三张日志表的读写；前端无改动。
- 不改变：HTTP、SSE Frame、DisplaySnapshot、用户可见行为。
