# 记忆系统

> 状态：2026-10-09。这是现行契约。旧三层 Markdown 自动注入、`<recalled-memory>`、`memory_node_tree` / `memory_node_query` 和会话日 `memory_nodes` 不再是注入与回忆主路径。历史方案在 git 历史里，不要按那些段落改运行时。

## 注入

每轮 system prompt 只注入两份全文：

- `users/{user_id}/habits.md`：属于这个人，所有智能体都注入。
- `agents/{agent_id}/professional.md`：只属于该智能体，不设长度上限，不是 Skill。Team Member 用自己的专业全文。Subagent 的 owner 是 `subagent:{name}`。

不注入 30 天节点树，也不自动附 `<recalled-memory>`。

## 工具

普通 Agent、Team、群聊 Worker、Subagent 和定时任务执行者默认携带：

- `timeline_retrieve`：按工作区、项目、智能体、场合（`chat` / `group` / `job`）、时间和关键词读取原始对话。空过滤表示整条时间线，单次仍有笔数和字数上限。
- `timeline_ingest`：把这次任务需要的内容留在当前会话。不写回时间线、习惯或专业区。成功后检索原文从后续上下文抽掉。

时间线就是 chat、群聊和定时任务的原始消息，不另存一套摘要，也不每轮注入。

## 写入

- 习惯：用户消息落库后、回复前判断。寒暄不跑模型。像偏好、语气或人设时才跑，判定后直接写，不等确认。
- 专业区：该智能体一轮落库后判断，决定改写、删除或不改。
- 普通技能：成功收尾后仍走 Skill 自进化，不绑定智能体。手动触发只产生普通技能。

## 页面与旧数据

Memory 页主视图是习惯、按智能体的专业区和时间线。Skill 自进化队列、注册失败原因和 `MEMORY_AUDIT` 仍在。

`user_memory.md` / `agent_memory.md` / `global_memory.md` 与 `memory_nodes` 不再注入，也不再从 Chat 收尾写入。旧节点只读。Memory Wiki 已从页面和 API 移除。

向量库只索引历史 `memory_nodes`。`VECTOR_STORE_PROVIDER` 只在首次初始化时生效，之后读持久化 `VectorStorePolicy`。

## 禁止

- 不新增 `memory` Runtime 分支。
- 不恢复旧 memory runtime、自动 recalled-memory 或把节点树当每轮注入。
- 不把 Skill 自进化写成某个智能体的专业区。
