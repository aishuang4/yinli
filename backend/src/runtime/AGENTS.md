# runtime

`backend/src/runtime/` 的共用运行时接缝。包：[../../AGENTS.md](../../AGENTS.md)。

- Chat、群聊和定时任务调用这个包，不互相调用对方的私有模块。
- `agent_spec` 放主 Agent 规格、缓存键和按规格创建。
- `governance` 放单 Agent、TeamRun 和群聊 Worker 共用的上下文 middleware、记忆注入和会话状态。
- `model_call_trace.py` 记录非单 Agent 路径的 `CONTEXT_COMPRESSED` 和 `CONTEXT_USAGE`。
- `user_note.py` 的 `append_user_note` 把附加说明拼到用户文本后面。
- 记忆召回审计在 `src/memory/trace.py` 的 `record_memory_recall_audit`。
- `chat_session` 查找或创建 Chat 会话，并读取进行中的 Run。
- `agent_resources` 应用 MCP、知识库工具和文件范围的交集。
- `scheduled_chat` 启动、读取、取消和对账定时 Chat Run。
- `agent_python` 是 Agent Bash 使用的隔离 Python。
- `routes/chat/runtime_governance.py` 只做转发。新调用方导入 `src.runtime`。
- `group_studio` 和 `scheduler` 不导入 `src.server.routes.chat`。
