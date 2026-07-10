# Agent Workstation 项目进展与协作交接

> 更新时间：2026-07-10
>
> 本文只记录当前项目事实和协作入口。详细架构见 `docs/architecture-overview.md`，任务排期见 `docs/project-todo.md`。

## 当前定位

Agent Workstation 是基于 AgentScope 2.0 + Electron + React 的桌面多 Agent 协作工作台。当前核心链路已经收束为 Chat 中选择单 Agent 或 Team，通过 ReAct Runtime 执行任务，并用 RunState / DisplaySnapshot 驱动前端主展示。新记忆系统 MVP 已部分接入：运行前注入 Markdown 摘要和最近 30 天历史记忆索引，运行后经过 `MemoryAdmissionGate` 后台提炼 Markdown 记忆并持久化历史记忆（底层 `memory_nodes`）。

旧记忆模块、旧 Plan Runtime、旧 Subagent Runtime 已下线。`memory*`、`planConfig`、`planRuns` 等字段只保留历史库启动兼容语义，不能继续作为新功能实现入口；旧 Subagent 字段、配置入口、事件兼容分支和前端入口已从当前 API、前端类型和运行模型中移除，遇到残留实现应删除。当前记忆能力只能沿 `docs/memory-system-design.md` 的新方案推进，不新增 `memory` runtime.kind。

## 已在线能力

- Agent CRUD、导入导出、模型配置、降级模型链、工具权限、执行后端配置。
- Team CRUD 与角色素材模板；每次请求由 Leader 动态创建临时 Member 和共享任务，不复用模板中的静态依赖或上轮成员。
- Chat 单 Agent / Team 运行、历史会话、Markdown、多模态图片、HITL、工具 / Skill 展示，以及与单 Agent `DisplaySnapshot` 隔离的 Team Console。
- 后台 Run：`/api/chat/send/start`、`/api/chat/resume/start`、Run Center、取消、重跑、SQLite 运行记录。
- RunState 主链路：`RUN_STATE` / `RUN_STATE_PATCH`、`displaySnapshot`、轻量工具摘要、刷新恢复。
- MCP、Skills、HTTP 工具管理与 Agent 注入。
- Trace：模型、工具、权限、Team、HITL、错误和 token/cache 事件审计。
- Electron 开发窗口、内测打包脚本、生产模式后端启动入口。
- Plan Mode middleware：单 Agent 下通过 `plan_enter` / `plan_write` / `todo_write` / `task_output` / `plan_exit` 生成计划；`plan_exit` 输出 finalized 计划作为最终答案，不走旧审批流。
- Subagent M1/M2 部分：已按 Claude Code sub-agents 设计方式落地 Markdown + YAML frontmatter 文件定义，项目级 `.agents/subagents/` 和用户级 `~/.agent-workstation/subagents/` 扫描，主 Agent / Team Leader 可通过 `DelegateTask` 前台 blocking 委托 fresh-context 子 Agent；工具策略按“继承父工具池 -> `disallowedTools` -> `tools`”裁剪且不放大父 guardrails；frontmatter `permissionMode` 已接入父权限上限合并，支持 `default`、`acceptEdits`、`auto`、`dontAsk`、`bypassPermissions`、`plan`。运行写入 `subagent_invocations` 和 `SUBAGENT_STARTED` / `SUBAGENT_CONTEXT` / `SUBAGENT_COMPLETED` / `SUBAGENT_FAILED`，旧 `callableAgents` / `maxDepth` / `subagentCalls` schema 启动时删除。Subagent 已支持 frontmatter `skills` 预加载已安装启用 Skill，并以 `subagent:{name}` 作为 agent memory owner 注入 user / agent / global Markdown 摘要和最近 30 天历史记忆索引；`SUBAGENT_CONTEXT` 记录 loadedSkills、missingSkillRefs、memoryAgentId、memoryInjected、注入错误和 permission requested / effective 审计。前端 Chat 已将 `DelegateTask` 专门渲染为 `Subagent · {name}` 委托卡片，只显示 Markdown 委托任务和摘要，不展示原始调用 JSON；Subagents 管理页已显示文件定义、解析错误、来源路径、工具策略和 invocation 审计历史。当前项目含 `.agents/subagents/explorer.md` 只读探索示例。
- 记忆系统 MVP：历史记忆底层 `memory_nodes` SQLite 主表、`VectorStoreBackend` 抽象、`disabled` / `zvec` provider、`memory_node_tree` / `memory_node_query` 工具、user / agent / global Markdown 记忆文件、运行前记忆注入、运行后 `MemoryAdmissionGate` 准入、后台反思提炼、历史记忆持久化、Memory Wiki 只读同步器和 Memory 页面管理入口首版。Memory 页面可编辑 Markdown 记忆、禁用 / 删除 / 恢复历史记忆、查看 `MEMORY_AUDIT`、查看召回质量摘要、对召回命中做相关性反馈、操作 Skill 自进化队列、查看注册失败原因、手动同步 Wiki、查看生成文件、打开本地文件夹或通过 Obsidian 深链打开 vault；Wiki 同步会生成轻量 `entities/` 实体索引和 `entities/graph.md` 共现图。
- 准入 / 记忆 / 召回质量首轮：`MemoryAdmissionGate` 已升级为规则评分 + 可选模型评分增强，输出 history / markdown / skill 三类评分、阈值、signals、模型评分、失败原因、脱敏摘要和脱敏类型统计；Memory 设置页可编辑准入阈值、模型评分开关和 Markdown 质量参数；Markdown 写入已增加忽略 source 差异的近似重复过滤、敏感信息过滤、冲突合并、失败重试和 `# Summary` 压缩摘要；历史记忆查询已增加向量候选 + SQLite 关键词结果合并、按查询词命中 / 向量分数 / 结构化反馈分 / 置顶 / 置信度 / 使用次数 / 日期的轻量重排，召回反馈写入 `memory_retrieval_feedback` 表并同步写 `MEMORY_AUDIT`，`memory_node_tree` / `memory_node_query` 工具召回会写入 `MEMORY_AUDIT` 并带 retrievalQuality 质量指标，运行前主 Agent / Team Leader / 临时 Member session 均注入 20 条摘要索引。
- Skill 自进化雏形：已有 `SkillEvolutionAgent`、`skill_draft_propose` 草稿工具、规则版 router gate、workflow evidence、颗粒度控制、变量流抽象、`skill_evolution_records` 候选持久化、标准 Skill 注册首版、相似 Skill 合并首版、标准包目录 `backend/data/skill_packages/{skill_name}/SKILL.md`、历史 Skill 包目录 backfill、Skill zip 导出、用户显式请求生成 Skill 的手动触发流、`MEMORY_AUDIT` Trace 审计首版和 Chat “Skills 生成中”轻量提醒；注册后不自动绑定 Agent。Memory 页面已能查看和操作 Skill 自进化队列。
- 前端非 Chat 组件化整改首轮：业务表单字段已基本收口到 `frontend/src/components/form/`，文件导入统一到 `FileImportButton`，MCP / Skills 等目录筛选复用 `CatalogFilterBar`；Agent、Team、MCP、Skills、Tools、Settings、Sandbox、Memory、Trace 等页面已优先使用 `@agentscope-ai/design` 和共享封装。`MemoryPage` 已拆为页面编排层 + `features/memory/*`，`TracePage` 已拆为页面编排层 + `features/traces/*`，`AgentEditor` 已拆出 sections 和选择弹窗。

## 核心入口

后端：

- `backend/src/server/main.py`：FastAPI 入口。
- `backend/src/server/routes/chat/handlers/send.py`：发送消息入口。
- `backend/src/server/routes/chat/readiness_gate.py`：运行前信息充足性判断。
- `backend/src/server/routes/chat/intent_router.py`：第一层 architecture 路由，当前只进入 `react`。
- `backend/src/server/routes/chat/react_runtime.py`：ReAct Runtime 执行入口。
- `backend/src/server/routes/chat/team_runtime/`：一次性 TeamRun、文件状态、Task List、Mailbox、隔离 Member session 和最终综合。
- `backend/src/server/routes/chat/runtime_builder.py`：运行时规格组装。
- `backend/src/server/routes/chat/leader_stream.py`：AgentScope 流事件转换。
- `backend/src/server/routes/chat/background_runs.py`：后台 Run 与 RunState。
- `backend/src/server/routes/chat/event_hub/projector.py`：原始事件到 RunState / DisplaySnapshot 投影。
- `backend/src/server/routes/chat/run_context.py`：组装运行上下文，并在 readiness 通过后注入 Skill prompt 和记忆上下文。
- `backend/src/server/routes/chat/completion.py`：成功收尾后触发 `MemoryAdmissionGate`、后台 Markdown 记忆提炼和历史记忆持久化。
- `backend/src/agents/factory.py`：AgentScope Agent / Model / Toolkit 唯一构造入口。
- `backend/src/storage/database.py`：SQLite schema 和存储访问。
- `backend/src/memory/retrieval.py`：生成运行前记忆注入文本。
- `backend/src/memory/reflection.py`：运行后 user / agent / global Markdown 记忆与 Skill 草稿提炼编排。
- `backend/src/memory/extraction_agents.py`：记忆提炼 Agent 和 SkillEvolutionAgent prompt / schema。
- `backend/src/memory/nodes.py`、`backend/src/memory/node_persist.py`：历史记忆底层服务和运行后持久化。
- `backend/src/memory/markdown_store.py`：Markdown 记忆文件存储。
- `backend/src/memory/wiki_vault.py`：Obsidian-compatible Memory Wiki 只读同步器。
- `backend/src/server/routes/skills.py`：Skills 注册、导入、导出和标准 Skill 包目录管理。
- `backend/src/subagents/runtime_context.py`：Subagent 运行前上下文构建，解析 frontmatter `skills` 并注入 `subagent:{name}` 维度记忆。
- `backend/src/subagents/permission_policy.py`：Subagent `permissionMode` 解析和父 guardrails 上限合并。
- `backend/src/vector_store/*`：向量存储抽象、策略和 Zvec / disabled provider。
- `backend/src/tools/memory_nodes.py`、`backend/src/tools/memory_reflection.py`：历史记忆查询工具和内部反思工具。

前端：

- `frontend/src/App.tsx`：页面映射、主题和健康检查。
- `frontend/src/pages/ChatPage.tsx`：Chat 主页面。
- `frontend/src/hooks/useEventStream.ts`：RunState 订阅。
- `frontend/src/types/runState.ts`：RunState 类型。
- `frontend/src/types/displayEvents.ts`：DisplaySnapshot / DisplayEvent 类型。
- `frontend/src/features/chat/*`：Chat 展示组件。
- `frontend/src/pages/AgentTeamPage.tsx`、`MCPPage.tsx`、`SkillsPage.tsx`、`ToolsPage.tsx`、`TracePage.tsx`、`RunsPage.tsx`、`SettingsPage.tsx`：主要页面。
- `frontend/src/pages/MemoryPage.tsx`：Memory 管理入口，当前只负责数据装配和 tab 编排；具体 Markdown 记忆、历史节点记忆、Skill 自进化队列和 Wiki Vault 面板在 `frontend/src/features/memory/`。
- `frontend/src/pages/TracePage.tsx`：运行轨迹入口，当前只负责 trace 列表、详情和事件分页的数据加载；列表、详情、事件时间线、消息卡和工具列表在 `frontend/src/features/traces/`。
- `frontend/src/pages/SubagentsPage.tsx`：Subagent 管理入口，展示 `.agents/subagents/` / 用户级目录加载结果、解析错误、来源路径、工具策略和 `subagent_invocations` 审计记录；列表组件位于 `frontend/src/features/subagents/`。
- `frontend/src/components/form/`：非 Chat 表单字段统一封装层，业务页面优先使用这里的 `TextField`、`SelectField`、`TextAreaField`、`DateField`、`ToggleField` 等组件。
- `frontend/src/components/FileImportButton.tsx`：基于 Spark Design `Upload` 的本地文件导入按钮，Agent / Team / Skill 导入优先复用。

## 启动与验证

首次启动：

```bash
cp backend/.env.example backend/.env
# 填入 AGENT_WORKSTATION_DEEPSEEK_API_KEY
cd backend && uv sync && cd ..
cd frontend && npm install && cd ..
./scripts/dev.sh
```

常用验证：

```bash
cd backend && uv run pytest
cd frontend && npm run build
cd frontend && npm run lint
```

前端整改专项扫描：

```bash
rg -n "<(Input|Select|DatePicker|Switch|Slider|InputNumber|textarea|input|select)\b|from ['\"]antd['\"]" \
  frontend/src/pages frontend/src/features frontend/src/components \
  -g '*.tsx' -g '!frontend/src/pages/ChatPage.tsx' -g '!frontend/src/features/chat/**'
```

当前非 Chat 预期只命中 `components/form/` 共享封装和 `FeedbackBridge` 的 AntD App 桥接。若命中业务页面，应优先继续抽取。

桌面开发：

```bash
cd frontend && npm run electron:dev
```

## 当前注意事项

- Chat SSE / RunState / DisplaySnapshot 新增字段前必须先更新 `docs/chat-sse-event-contract.md`。
- Team Runtime 不支持 Plan Mode。需要“先规划再 Team 执行”时，先用单 Agent 生成计划，再由用户下一轮切换到 Team 模式执行；当前所选 Agent 自动成为本轮 Leader，不选择持久 Team。
- `resume.py` 仍有部分旧式分支，后续应继续复用 `leader_stream.py`、`completion.py`、`orchestration.py`。
- 旧记忆表可能存在于开发库中，但当前运行时不触发旧 memory summarize，也不应扩展旧记忆模块。新记忆数据使用 `memory_nodes`、Markdown 文件和 `VectorStoreBackend`。
- 默认 Markdown 记忆文件为 `backend/data/memory/users/user_memory.md`、`backend/data/memory/agents/agent_memory.md`、`backend/data/memory/global/global_memory.md`；Memory 页面主入口只暴露唯一 user / global 和按 Agent ID 的 agent memory，不兼容读取旧 `default.md`。历史扩展路由仍保留，但不作为当前页面入口。
- Skill 自进化队列操作和注册失败原因可视化已接入 Memory 页面；后续重点是更强候选检索筛选、模型重跑策略和注册质量评估。
- Memory 模块进入收口稳定阶段：近期只补运行前注入 Trace payload、设置迁移 / 重建 / 风险提示和整体回归测试；Wiki 图谱成熟化、Wiki 反向合并、`memory_change_log`、复杂召回质量视图、反馈权重配置和模型评分评估暂不继续横向扩展。
- 前端非 Chat 页面已完成第一轮表单统一和大页面拆分；后续重点是继续拆 `AgentTeamPage`、`RunsPage`，并把重复的卡片式 `<button>`、tab 选择项和列表选择项沉淀为通用交互组件。
- Team Runtime 不读取或投影旧 Team 事件和旧 Team 历史字段；发现旧兼容分支时直接删除，不在新运行时上补适配。
- `CLAUDE.md` 只是兼容指针，当前事实以 `AGENTS.md` 为准。

## 文档入口

- 完整分类只维护在 `docs/README.md`，本文不复制第二份文档清单。
- 当前事实以 `AGENTS.md` 和 `docs/architecture-overview.md` 为准，待办以 `docs/project-todo.md` 为准。

## 文档维护规则

- 新增功能前，涉及 Chat SSE / RunState / DisplaySnapshot 的必须先更新 `docs/chat-sse-event-contract.md`。
- 新增入口级文档必须在 `docs/README.md` 中登记。
- `project-todo.md` 中标记为已完成的任务，对应专题文档可关闭或归档。
- 已完成的阶段草案直接合并结论并删除，不保留归档目录。
