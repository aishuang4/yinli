# Dynamic Workflow 事件契约

本文档定义 Dynamic Workflow 独立模块的稳定事件。Workflow 事件不属于 Chat SSE 主链路，不复用 `/api/chat/send` 事件。

## 事件流

- Endpoint: `GET /api/workflows/{run_id}/events`
- Media Type: `text/event-stream`
- 每个事件同时写入 SQLite `workflow_events`，前端刷新后可通过 `GET /api/workflows/{run_id}` 恢复。

## 稳定事件

| Event | 说明 |
| --- | --- |
| `WORKFLOW_CREATED` | Workflow Run 已创建，初始节点和边已落库 |
| `WORKFLOW_STARTED` | Workflow Run 开始执行 |
| `NODE_STARTED` | 某个节点开始执行 |
| `NODE_COMPLETED` | 某个节点执行完成 |
| `NODE_FAILED` | 某个节点执行失败，Workflow 暂停在失败状态 |
| `WORKFLOW_COMPLETED` | Workflow Run 全部完成 |

## 事件数据

```json
{
  "id": "workflow-event-...",
  "runId": "workflow-...",
  "nodeId": "workflow-node-...",
  "type": "NODE_STARTED",
  "status": "running",
  "title": "扫描上下文",
  "content": "开始读取任务上下文",
  "payload": {},
  "createdAt": "2026-07-08T10:00:00+00:00",
  "sortOrder": 1
}
```

## 设计约束

- SQLite 是事实来源，SSE 只负责实时推送。
- 前端只依赖稳定事件和节点状态，不依赖 LangGraph 或 AgentScope 的内部事件。
- 后续接入 LangGraph 时，编排层需要把内部状态转换成本文档事件。
