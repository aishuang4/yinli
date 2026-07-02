# Chat 事件前端改造与后端对接交接

> 更新时间：2026-07-01
>
> 本文记录单 Agent Chat 前端事件收口后的实现边界，以及后端中间层需要遵循的对接规范。稳定 SSE / RunState 契约仍以 `docs/chat-sse-event-contract.md` 为准。

## 1. 本轮前端边界

本轮只固定单 Agent 前端展示链路。Team 展示仍隔离在 `TeamMessageBubbles.tsx`，后续独立改造。

单 Agent 前端不再消费旧 `displayEvents[]`，也不再由组件扫描 `runtimeEvents` 拼主界面。当前链路为：

```text
RUN_STATE.displaySnapshot
RUN_STATE_PATCH.displayEvent
  -> displayEventReducer
  -> StreamState.displaySnapshot
  -> chatDisplayProjector
  -> ChatDisplayMessage.blocks
  -> ChatDisplayRenderer
  -> UI 组件
```

核心文件：

- `frontend/src/types/displayEvents.ts`：统一维护 block、event、op、scope 常量和 `DisplayEvent` / `DisplaySnapshot` 类型。
- `frontend/src/utils/displayEventReducer.ts`：唯一负责把单个 `displayEvent` 聚合进 `displaySnapshot`。
- `frontend/src/hooks/useEventStream.ts`：只在 `RUN_STATE` 读取完整 `displaySnapshot`，在 `RUN_STATE_PATCH` 应用单个 `displayEvent`。
- `frontend/src/features/chat/chatDisplayProjector.ts`：唯一负责把 `displaySnapshot.blocks` 转成 UI 渲染 block。
- `frontend/src/features/chat/ChatDisplayRenderer.tsx`：只按归一化后的 block 分发组件。
- `frontend/src/features/chat/ProcessDisplayBlock.tsx` / `ProcessTimeline.tsx`：只渲染 process block 的有序 `items[]`。
- `frontend/src/features/chat/PlanDisplayBlock.tsx`：渲染 Plan block 和审批入口。
- `frontend/src/features/chat/HumanFeedbackDisplayBlock.tsx`：渲染当前等待用户处理的 feedback block。
- `frontend/src/features/chat/TeamMessageBubbles.tsx`：Team legacy 展示隔离区。

## 2. 后端必须输出的字段

### RUN_STATE

`RUN_STATE` 是刷新、断线恢复和首包状态，必须返回完整快照：

```ts
displaySnapshot: {
  version: 2;
  blocks: DisplayBlock[];
}
```

### RUN_STATE_PATCH

`RUN_STATE_PATCH` 是流式增量，只返回一个展示事件：

```ts
displayEvent: {
  id: string;
  runId: string;
  sessionId: string;
  seq: number;
  createdAt: string;
  scope: "single_agent";
  block: "process" | "plan" | "feedback" | "answer" | "files" | "error";
  event: string;
  op: "append" | "upsert" | "replace" | "remove" | "clear";
  targetId?: string;
  status?: "pending" | "running" | "waiting" | "completed" | "failed" | "cancelled";
  data?: Record<string, unknown>;
}
```

后端不要在 patch 中返回整个 `process` 或完整 `displaySnapshot`；只有恢复态 `RUN_STATE` 返回完整快照。

普通 Runtime Event 属于后端内部、Trace 或原始兼容流，不属于单 Agent Chat 主展示契约。`RUN_STATE_PATCH` 不得再返回 `kind: "runtime_event"` 或 `runtimeEvent` 字段；需要展示到 Chat 的内容必须先投影成 `displayEvent`。

如果 Agent 流自然结束但没有产生最终答案，后端必须按失败处理，发送 `RUN_FAILED` 和 `ERROR`，并投影成 `error.raised` 展示事件。不要把 reasoning 兜底成 final answer，也不要静默标记为 completed。

## 3. DisplaySnapshot 结构

前端当前使用通用 block / item 结构，避免后端输出组件形状：

```ts
type DisplayBlock = {
  id: string;
  block: "process" | "plan" | "feedback" | "answer" | "files" | "error";
  status: string;
  updatedAt: string;
  items: DisplayItem[];
  data: Record<string, unknown>;
};

type DisplayItem = {
  id: string;
  event: string;
  targetId: string;
  status?: string;
  createdAt?: string;
  updatedAt: string;
  data: Record<string, unknown>;
};
```

约定：

- `process` 使用 `items[]` 表达 reasoning、thinking、tool、skill 的有序过程。
- `plan`、`feedback`、`answer`、`files`、`error` 使用 `data` 表达当前块状态。
- `feedback` 只放当前等待项，不保留历史已完成项。
- Team 暂不进入 `displaySnapshot`。

## 4. 当前前端消费的事件

事件常量在 `frontend/src/types/displayEvents.ts` 集中维护。当前已登记：

- process：`reasoning.delta`、`thinking.delta`、`tool.started`、`tool.input.delta`、`tool.output.delta`、`tool.completed`、`tool.failed`、`skill.started`、`skill.updated`、`skill.completed`、`skill.failed`
- answer：`answer.started`、`answer.delta`、`answer.completed`
- plan：`plan.waiting`、`plan.updated`、`plan.completed`、`plan.failed`
- feedback：`feedback.waiting`、`feedback.resolved`
- files：`file.created`、`file.updated`
- error：`error.raised`

新增事件时先扩展 `displayEvents.ts`，再补 projector 和文档。

## 5. 推荐事件示例

reasoning 流式增量：

```json
{
  "id": "evt-1",
  "runId": "run-1",
  "sessionId": "session-1",
  "seq": 1,
  "createdAt": "2026-07-01T01:20:00Z",
  "scope": "single_agent",
  "block": "process",
  "event": "reasoning.delta",
  "op": "append",
  "targetId": "reasoning-1",
  "status": "running",
  "data": { "text": "正在分析需求..." }
}
```

连续 reasoning delta 使用同一个 `targetId` 追加；如果中间出现 tool / skill，再出现 reasoning 时必须生成新的 `targetId`，例如 `reasoning-5`。这样前端才能按 `reasoning -> tool -> reasoning` 的顺序渲染时间线。

工具调用开始 / 完成：

```json
{
  "id": "evt-2",
  "runId": "run-1",
  "sessionId": "session-1",
  "seq": 2,
  "createdAt": "2026-07-01T01:20:01Z",
  "scope": "single_agent",
  "block": "process",
  "event": "tool.started",
  "op": "upsert",
  "targetId": "tool-read-1",
  "status": "running",
  "data": { "name": "Read", "inputPreview": "{\"path\":\"frontend/src/App.tsx\"}" }
}
```

```json
{
  "id": "evt-3",
  "runId": "run-1",
  "sessionId": "session-1",
  "seq": 3,
  "createdAt": "2026-07-01T01:20:02Z",
  "scope": "single_agent",
  "block": "process",
  "event": "tool.completed",
  "op": "upsert",
  "targetId": "tool-read-1",
  "status": "completed",
  "data": { "outputPreview": "读取完成", "outputSize": 1200, "outputTruncated": false }
}
```

最终答案流式增量：

```json
{
  "id": "evt-4",
  "runId": "run-1",
  "sessionId": "session-1",
  "seq": 4,
  "createdAt": "2026-07-01T01:20:03Z",
  "scope": "single_agent",
  "block": "answer",
  "event": "answer.delta",
  "op": "append",
  "targetId": "final-answer",
  "status": "running",
  "data": { "content": "这是最终结果..." }
}
```

Plan 等待审批：

```json
{
  "id": "evt-5",
  "runId": "run-1",
  "sessionId": "session-1",
  "seq": 5,
  "createdAt": "2026-07-01T01:20:04Z",
  "scope": "single_agent",
  "block": "plan",
  "event": "plan.waiting",
  "op": "replace",
  "targetId": "plan-1",
  "status": "waiting",
  "data": {
    "planId": "plan-1",
    "planFile": "docs/plan.md",
    "markdown": "...",
    "steps": []
  }
}
```

人工反馈等待项：

```json
{
  "id": "evt-6",
  "runId": "run-1",
  "sessionId": "session-1",
  "seq": 6,
  "createdAt": "2026-07-01T01:20:05Z",
  "scope": "single_agent",
  "block": "feedback",
  "event": "feedback.waiting",
  "op": "replace",
  "targetId": "interaction-1",
  "status": "waiting",
  "data": {
    "approval": {
      "interactionId": "interaction-1",
      "kind": "clarification",
      "type": "clarification",
      "status": "waiting",
      "agentId": "default",
      "agentName": "Assistant",
      "toolCalls": [],
      "title": "需要补充信息",
      "description": "",
      "questions": []
    }
  }
}
```

## 6. 前端约束

- UI 组件不得直接消费 `displayEvent`。
- 所有事件消费只能在 `displayEventReducer.ts` 发生。
- 所有 snapshot 到 UI block 的转换只能在 `chatDisplayProjector.ts` 发生。
- 不再使用 `modelCaches`。
- 不再使用旧 `displayEvents[]`。
- 不在单 Agent 主链路扫描 `runtimeEvents`。
- 不在单 Agent 主链路接收 `runtimeEvent` patch。
