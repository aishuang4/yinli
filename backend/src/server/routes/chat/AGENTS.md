# chat

`backend/src/server/routes/chat/` 的 Chat 发送路径。包：[../../../../AGENTS.md](../../../../AGENTS.md)。根：[../../../../../AGENTS.md](../../../../../AGENTS.md)。

## 入口

- `handlers/send.py` 是唯一的 `POST /api/chat/send`。它保存用户消息、启动 Run 并输出流。
- `readiness_gate.py` 只判断这次输入能不能启动运行时。它不选架构、Team、Skill 或工具范围。
- `intent_router.py` 在 readiness 通过后运行。架构是 `react`。`mode=agent` 选 `single_agent`。`mode=team` 选 `team`，并沿用选中的 Agent 作为 Leader。
- 不传入 `team_id`、`team_name` 或 `team_members`。不恢复 Team、Member 或 Leader 选择器。
- `runtime_dispatcher.py` 只分发 `react`。缺事实时在运行内用 `AskUser` 暂停。
- `react_runtime.py` 跑一个 Agent；`team` 则新建一次 `TeamRunService`。
- `runtime_builder.py` 组装模型快照、缓存键和 `MainAgentRuntimeSpec`。Skill 选择在 `skill_selector.py`。工具范围在 `tool_scope_resolver.py`。
- `leader_stream.py` 消费 `reply_stream()`。`completion.py` 为单 Agent 这一轮保存助手消息和 Trace。
- Team 领域状态在 `team_runtime` 里收口。不经过 `completion.py`。
- 成功的 Chat 回合不生成 Skill。

## 会话

- 历史留在 AgentScope 的 `state.context`。进程重启后从 `chat_messages` 重建。
- 不把历史贴进 system prompt。
- 不把时钟写进 system prompt。时间来自注入提示。
- 不把 `SKILL.md` 正文放进 system prompt。截断后用 `<activated-skill>` 提示补回已激活的 Skill。
- Chat 输入框强制开启的 Skill 也走 `activate_skill`。
- 定时任务触发时，在第一次模型调用前把冻结的 Skill 正文写入 `<activated-skill>`。system prompt 只声明已经注入。

## Team

- 每一轮新建一份 Team 文件状态。不恢复 `teams` 表。
- 成员写入限制在 `<session workspace>/team/<run>/<member>/`。
- Team 活动投影为 `team`、`team-member`、`team-task`、`team-activity`、`team-mail` 块。
- 不恢复顶层 `TEAM_EVENT`、`teamSnapshot` 或 `TeamConsoleState`。
- 有未读邮件的成员不提交结果。综合期间收到新邮件就重新综合。
- 人数、任务数和轮次上限在 `team_runtime`。调用方不要自行提高。

## 上下文

- 上下文 middleware 挂在 `runtime_governance.py`。不要给 Leader、Member 或综合再复制一套。
- `ContextHygieneMiddleware` 把旧的大段工具输出和旧图片做成存根。Skill 正文留下。
- `CONTEXT_USAGE` 的 `estimatedTokens` 使用 AgentScope 的 `count_tokens`。
- 从 `session_context_state` 恢复 `state.summary` 和 `middle_context`。

## 测试

- `backend/test/test_chat_runtime_regressions.py`
- `backend/test/test_chat_forced_skills.py`
- `backend/test/test_event_stream_projection.py`

契约：[docs/chat-react-runtime.md](../../../../../docs/chat-react-runtime.md) 和 [docs/chat-sse-event-contract.md](../../../../../docs/chat-sse-event-contract.md)。
