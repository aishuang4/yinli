# runtime-stream

`frontend/src/features/runtime-stream/` 的 Chat 展示流。上层：[../../../AGENTS.md](../../../AGENTS.md)。

- 在这里解码 `RuntimeFrame`。页面自己不解析 SSE 载荷。
- 渲染器读取 Block Tree。它不写这棵树。
- 命令通过对应 API 提交。不要把命令响应当成界面状态。等下一帧。
- 新的展示能力注册为自己的 `Block.kind` 模块和 projector。
- 不新增 RuntimeFrame op、顶层 SSE 事件或通用 reducer 分支。
- Team 和 Subagent 活动是嵌套块。
- Trace 页面不连接 Chat SSE。它读已保存的 observation。

契约：[docs/chat-sse-event-contract.md](../../../../docs/chat-sse-event-contract.md)。
