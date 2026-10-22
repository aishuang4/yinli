# backend

`backend/` 里的 Python API。导入名是 `src`。上层：[../AGENTS.md](../AGENTS.md)。

## 先读

- [src/server/routes/chat/AGENTS.md](src/server/routes/chat/AGENTS.md) — Chat 发送、ReAct 和 TeamRun。
- [src/agents/AGENTS.md](src/agents/AGENTS.md) — 唯一的 AgentScope 构造入口。
- [src/runtime/AGENTS.md](src/runtime/AGENTS.md) — 共用规格、治理和定时 Chat。
- [src/storage/AGENTS.md](src/storage/AGENTS.md) — SQLite 和表结构。
- [src/memory/AGENTS.md](src/memory/AGENTS.md) — 习惯、专业区和时间线。
- [src/group_studio/AGENTS.md](src/group_studio/AGENTS.md) — 群聊工作室领域。
- [src/scheduler/AGENTS.md](src/scheduler/AGENTS.md) — 定时任务。
- [src/catalog/AGENTS.md](src/catalog/AGENTS.md) — profile、Skill、MCP 和 Job 文件。

## 职责

这棵目录提供 `/api`、运行 Agent、保存租户数据。它不渲染界面。

## 布局

- `src/server/routes/` — 按领域分的 HTTP 处理函数。处理函数读请求、写响应。
- `src/server/routes/chat/` — Chat SSE 和 ReAct。处理函数、运行时和 Team 代码分开放。
- `src/runtime/` — Chat、群聊和定时任务之间的公开接缝。
- `src/agents/factory.py` — AgentScope 构造。采样参数在 `model_options.py`，护栏在 `guardrails.py`，工具规格在 `tool_specs.py`，模型创建在 `model_factory.py`。`runtime_types.py` 只转发。
- `src/tools/` — 工具实现。不要把工具正文写进路由。
- `src/storage/` — `engine/` 执行器，`records/` 业务表，`config/` 配置，`control/` 控制面。
- `src/config/settings.py` — 从 `.env` 读取的启动配置。
- `test/` — pytest 模块。

路由调用 runtime 和 storage。storage 不导入路由。工具不导入路由模块。

## 命令

在 `backend/`：

```bash
uv sync
uv run python -m uvicorn src.server.main:app --reload --port 8765
uv run pytest
uv run pytest test/test_secret_store.py::test_memory_secret_store_round_trip
```

交出后端改动前跑 `uv run pytest`。改了 Agent 运行时、HITL、工具历史、Team 执行或记忆时，补回归测试。

## API 形状

- 路由按领域放在 `src/server/routes/`。
- 请求和响应用 Pydantic。线上字段是 camelCase 时用 alias。Python 字段用 snake_case。
- 给路由返回值、存储函数和运行时事件补类型。
- 不返回原始异常、token、header 或密钥。
- 用户能恢复的错误返回清晰的中文。技术细节留在日志。
- async 路由可以调用同步的 SQLite 封装。普通请求不要堵在一轮 Agent 上。这一轮用流式返回。
- 对外的 HTTP、MCP 和模型调用要有超时，并返回可恢复的错误。

## 各领域共用的运行时规则

- 只通过 `src/agents/factory.py` 创建 Agent。profile 变更后调用 `invalidate_agent()`。
- 工具、Skill 和 MCP 按 Agent 配置和本轮范围注入。不要在页面或路由里写死这套集合。
- HITL 暂停不是运行结束。同一轮继续到最终回复。
- 工具调用要让实时界面能看见、历史能回放、Trace 能审计。
- HITL、工具调用、模型降级和 Agent 失败写入 `run_events`。
- Chat 展示用 `RUNTIME_EVENT` 帧和 `DisplaySnapshot` V4。不恢复旧的 RunState 读取、第二份快照或双写。
- 新的展示块有自己的 `Block.kind` projector。不为它新增 RuntimeFrame op 或顶层 SSE 事件。
- 同一条 Run 流上的领域事件按 `stream_ordinal` 由一个 projection runner 顺序消费。projector 不保留自己的游标。
- 快照做周期 checkpoint，再重放尾部。不要在每个 token 上重写整棵快照 JSON。
- `run.completed` 之前通过领域完成屏障。projector 不改写已经结束的 Run。
- 前端渲染器不拥有 Block Tree。命令走 API，再等下一帧回来。

## 同步

- Chat SSE 改动同时更新 [docs/chat-sse-event-contract.md](../docs/chat-sse-event-contract.md)。
- 新的存储查询写进 `src/storage/records/`。规则见 [src/storage/AGENTS.md](src/storage/AGENTS.md)。
