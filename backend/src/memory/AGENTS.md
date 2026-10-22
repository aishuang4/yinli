# memory

`backend/src/memory/` 的当前记忆路径。包：[../../AGENTS.md](../../AGENTS.md)。

- 时间线就是 Chat、群聊和定时任务的原始消息。不要每轮注入。
- 自动召回审计写 `trace.record_memory_recall_audit`。
- `timeline_retrieve` 按工作区、项目、智能体、场合和时间取原文。`timeline_ingest` 只把这次任务需要的内容留在当前会话，然后从上下文去掉原文。
- 习惯在 `users/{user}/habits.md`。它属于这个人。每个 Agent 每轮注入全文。
- 用户消息落库后、回复前，仅当这句话像偏好、语气或人设时才跑习惯判断。寒暄不跑。判定后直接写。
- 专业区在 `agents/{agent}/professional.md`。它只属于该 Agent。每轮注入全文。不设长度上限。
- 该 Agent 这一轮落库后，专业区判断决定改写、删除或不动。
- Team Member 用自己的专业区文件。Subagent 的 owner id 是 `subagent:{name}`。
- 不恢复 Skill 自进化、候选队列、`SkillEvolutionAgent` 或 `skill_draft_propose`。
- 完成的 Chat 回合不生成 Skill。
- `user_memory.md`、`agent_memory.md`、`global_memory.md` 和 `memory_nodes` 只读。不注入它们，也不从 Chat 收尾写入。
- 旧三层 Markdown、摘要注入、自动召回和回合结束后的节点写入在 `legacy/`。记忆管理页仍可读取旧文件和节点。
- 习惯判断和专业区判断通过 `json_agent.py` 调用模型。
- 向量索引只覆盖 `memory_nodes`。`VECTOR_STORE_PROVIDER` 只在首次初始化时生效。之后启动读取已保存的 `VectorStorePolicy`。

见 [docs/memory-system-design.md](../../../docs/memory-system-design.md)。
