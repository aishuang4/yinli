# 记忆系统设计与实现进度

> 状态：MVP 在线并进入稳定化阶段，2026-07-14。本文记录新记忆系统的设计、已实现边界和后续约束。不复用旧三层记忆模块、旧 `memory_*` 事件或旧运行时补丁。

## 当前实现快照

已完成：

- 旧记忆模块清理，不再创建或读写旧 `memory_facts` / `memory_events` / `memory_candidates` 语义。
- 新增 `memory_nodes` SQLite 主表和 `/api/memory/nodes*` 路由。
- 新增全局 `VectorStoreBackend` 抽象，支持 `disabled` 和 `zvec` provider；`VECTOR_STORE_PROVIDER` 只在首次初始化时写入 `VectorStorePolicy`。
- Zvec provider 已可落盘、创建 / 打开 collection、写入文档、删除和查询；没有 embedding 时降级到标量字段与 BM25 / 稀疏检索。
- 新增 `memory_node_tree` / `memory_node_query` 普通 Agent 工具。
- 新增 user / agent / global Markdown 记忆文件存储和 API，默认文件名为 `user_memory.md`、`agent_memory.md`、`global_memory.md`，不兼容旧 `default.md`。
- 运行前在 readiness 通过后注入 Skill prompt 之后的记忆上下文：user / agent / global Markdown 摘要、最近 30 天历史记忆索引和查询工具提示。
- 运行后在 `completion.py` 成功收尾后后台执行记忆提炼，不阻塞主对话。
- 新增并行 `UserMemoryReflectionAgent`、`AgentMemoryReflectionAgent`、`GlobalMemoryReflectionAgent`，三者分别绑定唯一 Markdown 文件，并使用 `memory_read` / `memory_add` / `memory_update` / `memory_delete`。
- 三层 Markdown 提炼提示词已区分职责：user 记用户偏好和画像，agent 记当前 Agent 的解决方法 / 反面案例 / 行为边界，global 记所有 Agent 共享规则。
- 新增规则版 `SkillEvolutionRouterGate` 和 `WorkflowEvidenceBuilder` 首版，Skill 提取会先过 gate，再基于成功工具链、有价值失败、变量流、改动文件和验证结果摘要进行判断。
- 新增 `SkillEvolutionAgent` 与 `skill_draft_propose` 草稿工具，已持久化 Skill 自进化候选记录。
- SkillEvolutionAgent schema 已加入 `granularity`、`requiresWorkflow`、`suggestedSplit` 和 `variableFlow`，只有 `granularity=atomic` 且不需要 Workflow 的候选才会被保留。
- 原子、高置信、通过安全校验且未发现相似已安装 Skill 的候选会注册为标准 Skill，不自动绑定 Agent。
- 相似 Skill 合并首版已实现：注册前综合名称、描述、触发条件、标签、正文步骤和关键概念 token 判断相似能力；命中时把候选记录关联到已有 Skill，不创建重复 Skill。
- 用户显式说“把这个生成 Skill / 把刚才流程生成 skills”时，会读取同一会话上一轮完整 user / assistant 对话作为 Skill 提炼输入，不把短命令本身当作 Skill 内容。
- 自动生成 / 手动注册 Skill 会写入 `backend/data/skill_packages/{skill_name}/SKILL.md`，并可从 Skills 页面按 Skill 名称导出 zip。
- 历史 registry 中缺少 `resourcePath/resources` 的用户 Skill 在加载时会自动 backfill 标准 `SKILL.md` 包目录。
- 历史记忆持久化、后台反思和 Agent 主动调用 `memory_node_tree` / `memory_node_query` 的召回行为会写入 `MEMORY_AUDIT` Trace 审计；Chat 页面只展示 “Skills 生成中” 轻量提示。
- 运行后历史记忆持久化只保存用户输入和 Agent 最终输出，不保存工具调用过程。
- 新增前端 Memory 页面首版：导航入口已接入，支持唯一用户记忆、唯一全局记忆、每个 Agent 一份 Agent Markdown 记忆的查看 / 编辑 / 保存；历史记忆按“Agent -> 时间节点 -> 具体记忆”查看，底层使用 `memory_nodes`。
- Memory 页面已接入 Skill 自进化队列只读视图：可按状态和 Agent 查看候选记录、router gate、workflow evidence、草稿描述、风险提示、变量流和注册状态。
- Memory 设置页已增加最近召回质量摘要：基于 `MEMORY_AUDIT.retrievalQuality` 统计召回次数、空结果率、平均 top score 和命中来源。
- Memory 审计详情已增加召回命中反馈首版：用户可对某次 `memory_node_query` 命中的 node 标记相关 / 不相关，反馈写入结构化 `memory_retrieval_feedback` 表，并同步写 `MEMORY_AUDIT.memory_retrieval_feedback` 作为追溯记录。
- 召回反馈已接入轻量排序权重：查询时聚合最近结构化反馈，相关反馈加权，不相关反馈降权，并在 `retrievalQuality.resultQuality[].feedbackScore` 中展示。
- 新增轻量规则评分版 `MemoryAdmissionGate`：运行后先判断空内容、敏感内容、低信号礼貌回复、长期意图、实质工作信号和手动 Skill 信号，再输出 history / markdown / skill 三类评分，分别决定是否进入历史记忆、Markdown 反思和 Skill 自进化。
- `MemoryAdmissionGate` 审计 payload 已包含准入原因、signals、三类评分、阈值、模型评分结果、模型失败原因、脱敏后的用户 / Agent 文本摘要、脱敏命中次数和脱敏类型统计；Memory 设置页支持编辑准入阈值、模型评分开关、模型评分权重、脱敏策略和 Markdown 质量参数。
- `MemoryAdmissionScoringAgent` 已接入后台反思链路：开启模型评分后会对 history / markdown / skill 进行语义评分，失败时自动回落规则评分，不影响主对话。
- Markdown 写入已增强近似重复过滤、敏感信息过滤、同类冲突合并、写入失败重试和 `# Summary` 自动压缩摘要，降低长期 Markdown 记忆被相同规则或冲突偏好刷脏的概率。
- 历史记忆查询已增加轻量 hybrid fusion、重排和工具召回审计：向量候选会与 SQLite 关键词结果合并去重，再按查询词命中、向量分数、置顶、置信度、使用次数和日期排序；Agent 通过 `memory_node_tree` / `memory_node_query` 查询时记录 query、filters、resultCount 和命中 nodeId；运行前注入从 30 条收敛到 20 条摘要索引，详情仍要求通过 `memory_node_query` 查询。
- 新增 Obsidian-compatible Memory Wiki 同步器首版：`MemoryWikiVaultSyncer` 可从三类 Markdown 记忆、`memory_nodes` 和已注册 Skills 生成 `backend/data/memory/wiki/` 只读视图，并通过 `/api/memory/wiki/sync` 手动触发。
- 新增 `/api/memory/wiki/graph` 结构化图谱接口：直接从权威记忆源生成实体节点、共现关系、权重和引用文档，不依赖先同步文件，也不写回权威存储。
- Memory 页面已增加 Wiki Vault tab，可手动同步 wiki 视图、查看图谱、查看生成统计、vault 路径和生成文件列表；Electron 桌面端可打开 wiki 文件夹或通过 `obsidian://open?path=...` 打开 Obsidian vault。
- Memory Wiki 已支持轻量实体索引：同步时从 Markdown 记忆、历史记忆和 Skill 文档中提取稳定实体，生成 `entities/{kind}/{name}.md`、Obsidian 双链和 `entities/graph.md` 共现图。
- 记忆提炼 Agent 提示词已要求重要稳定实体使用 `[[类型:名称]]` 链接，例如 `[[规则:禁止恢复旧 Plan Runtime]]`，并避免给普通词过度加链接。
- Wiki 图谱当前先暂停继续推进：现有版本只是共现图 MVP，用于验证实体提取、图谱接口和前端展示链路，不代表成熟知识图谱能力。

主干收口前仍需完成：

- Memory 设置模型仍缺显式迁移、重建和策略变更风险提示。
- 运行前记忆注入 Trace payload。
- 当前 Memory 主干需要一次完整回归、冒烟测试和稳定版本提交。

暂停增强分支：

- Markdown 记忆的模型级冲突判断和摘要质量评估。
- `MemoryAdmissionGate` 的模型评分效果评估、脱敏策略分级展示和模型评分回归集。
- 召回质量的复杂质量评估视图、反馈权重配置、空结果 / 低分结果诊断和召回样本回放。
- Memory Wiki 的自动同步、反向编辑策略、实体提取增强和图谱成熟化。
- `memory_change_log` 与 Git-backed vault diff。

下一阶段推进重点：

- **固主干**：补齐运行前注入 Trace payload、设置迁移 / 重建 / 风险提示，并完成回归测试和稳定版本提交。
- **控分支**：Wiki 图谱、反馈权重配置、复杂召回评估、`memory_change_log` 和模型评分评估暂不继续横向扩展。

## 目标

新记忆系统要解决的是“Agent 如何稳定记住对后续任务有帮助的信息”，而不是把聊天历史无差别塞回上下文。

核心目标：

- 用户可理解：记住了什么、为什么记住、会用在哪里，都能看见和管理。
- 用户可控制：MVP 不做审批队列，记忆自动写入后支持查看、编辑、删除、禁用和追溯来源。
- 运行时可审计：每次检索、注入、自动写入、更新和删除都有 Trace / Runtime Event 记录。
- 注入可控：按 Workspace / Agent / Team / Project / User 作用域检索，按 token budget 精简注入。
- 接入轻量：记忆能力只通过 AgentScope hooks / middleware hooks 和工具接入，不新增独立 Runtime 分支。
- 架构可扩展：先支持标量过滤 + BM25 / 稀疏检索；用户配置 embedding 后再启用稠密向量召回，不把 embedding 模型作为 MVP 前置条件。

## 非目标

- 不恢复旧记忆模块和旧 summarize 链路。
- 不直接复用旧 `memory_facts` / `memory_events` / `memory_candidates` 表作为新语义源。
- 不把所有历史消息当成长记忆。
- 不让模型静默写入长期记忆。
- 不把记忆能力塞进 `intent_router.py`，也不让记忆系统决定 Runtime architecture 或 Team Member 组建。
- 不新增 `memory` runtime.kind，也不恢复旧 Plan / Subagent 式分支；记忆只能作为 ReAct 周边增强能力存在。

## 产品语义

记忆系统按“历史记忆 + Markdown 长期记忆”双轨设计。向量存储只保存历史记忆底层 `memory_nodes`；用户记忆、Agent 迭代记忆和全局偏好记忆以 Markdown 文件存储，并通过压缩摘要控制上下文体积。

### Obsidian-compatible Memory Vault 借鉴方向

参考 OpenHuman 的 Obsidian Wiki / Memory Tree 方案后，本项目后续将把 Markdown 记忆增强为 **Obsidian-compatible Memory Vault 表现层**。该层用于让用户和 Agent 都能读取、编辑、链接和导出记忆，但不替代当前权威存储：

- **权威存储仍是 SQLite + Markdown + VectorStoreBackend**：`memory_nodes`、`skill_evolution_records`、三类 Markdown 长期记忆和向量库策略继续作为运行时事实来源。
- **Vault 是可读表现层**：生成 `backend/data/memory/wiki/`，按 `users/`、`agents/`、`global/`、`history/`、`skills/`、`entities/` 组织 Markdown 文件。当前首版为手动同步的只读生成视图。
- **Wiki 链接不承担事务语义**：`[[项目:Agent Workstation]]`、`[[规则:禁止恢复旧 Plan Runtime]]`、`[[Skill:xxx]]` 只作为可视化和人工维护入口，真实检索仍通过工具、索引和数据库完成。
- **实体索引先只读生成**：当前同步器会生成 `entities/` 实体页和 `entities/graph.md` 共现关系；服务端同时提供 `/api/memory/wiki/graph` 给前端展示结构化图谱。后续再评估是否新增 `memory_entities`、`memory_entity_links`、`memory_entity_edges` 等结构化表，把用户、项目、Agent、Skill、工具、规则、文件和错误类型连成可检索关系图。
- **记忆准入先于 Wiki 同步**：`MemoryAdmissionGate` 控制哪些内容只进入历史记忆、哪些进入长期 Markdown、哪些进入 Skill 自进化，避免把低信号聊天无差别写入 Vault。
- **差异追踪暂停推进**：`memory_change_log` 和 Git-backed vault diff 先从 MVP 中移出，等主干稳定后再评估。

#### Wiki 图谱 MVP 现状与暂缓项

当前 Wiki 图谱只适合作为链路验证版本，问题和边界如下：

- **语义层仍是共现图**：两个实体只要出现在同一份 Markdown 记忆、历史记忆或 Skill 文档中就会连边，不能表达“依赖、属于、禁止、解决、替代”等真实关系。
- **高频结构节点会污染视图**：`agent:default`、日期、`workspace:default`、`tools`、`Plan Runtime` 等实体出现频率高，会把图谱拉成一团，看起来像噪声网络。
- **实体提取仍偏规则化**：当前主要依赖固定 pattern 和 `[[类型:名称]]` 链接，缺少 LLM 实体归一、关系分类、同义词合并和低价值实体过滤。
- **前端只是轻量 SVG 布局**：圆环布局可以验证接口，但节点多时不具备专业图谱所需的拖拽、缩放、搜索、聚类和力导向布局。

后续恢复推进时，优先级建议：

1. 默认隐藏或弱化 `date`、`agent:default`、`workspace:default` 等结构节点。
2. 默认只展示高置信关系，例如 `weight >= 2`，并支持按实体类型过滤。
3. 把图谱分成“知识图谱”和“历史时间图”：知识图谱展示规则、模块、Skill、主题关系；历史时间图展示 Agent、日期和记忆文件。
4. 给边增加关系类型和证据说明，至少区分 `co_occurs`、`mentions`、`uses`、`forbids`、`derived_from` 等。
5. 再考虑引入 Cytoscape.js 等专业图谱组件，替换当前轻量 SVG。

### Memory 页面管理闭环现状

Memory 页面“用户可控制、可追溯”的首版闭环已完成，先不继续扩展图谱：

- **设置区**：展示当前记忆系统关键策略，包括向量库 provider、准入阈值、模型评分开关、模型评分权重、脱敏策略、Markdown 质量参数、Markdown 记忆文件路径、Wiki 图谱是否仍为 MVP。向量库 provider 和 schema 仍只读，准入与 Markdown 质量策略可热更新。
- **审计详情**：按记忆相关 Trace 事件查看 `MEMORY_AUDIT`，重点展示 memory type、action、run/session/message 引用、状态、错误原因和 payload 摘要。
- **历史记忆删除 / 禁用**：对 `memory_nodes` 做软状态变更，不物理删除；支持 `disabled`、`deleted` 和恢复为 `active`，并写入审计事件。向量索引首版跟随删除或标记失效，失败时不影响 SQLite 状态。
- **Skill 队列操作**：对 Skill 自进化候选支持跳过、恢复草稿、标记失败和必要的重新注册入口。首版提供状态管理和失败原因可视化，避免自动重跑模型。
- **注册失败原因可视化**：前端详情中突出展示 `status=failed` 的原因、风险提示、相似 Skill 命中、颗粒度拒绝、校验失败等字段，让用户能判断是模型输出问题、重复 Skill、风险问题还是注册链路错误。

推荐目录形态：

```text
backend/data/memory/wiki/
  README.md
  entities/
    project/
    topic/
    module/
    rule/
    graph.md
  users/user_memory.md
  agents/{agent_id}/agent_memory.md
  global/global_memory.md
  history/{agent_id}/{date}.md
  skills/{skill_name}.md
```

这个方向的边界是：Vault 解决“用户可读、可修正、可导出、可图谱化”，不解决向量检索、事务一致性和运行时准入；这些仍由服务层和存储层负责。

### 记忆层与技能自进化产物

1. **历史记忆（底层 memory nodes）**：按 Agent + 日期聚合当天协作过程，只记录用户输入、Agent 最终输出和压缩摘要，不记录工具调用过程。底层 `memory_nodes` 进入向量库，用于按日期、范围和语义检索。
2. **用户单独记忆（user memory）**：记录当前用户的长期信息、偏好、协作习惯、常用项目和表达方式。以 Markdown 文件存储，并定期压缩摘要。
3. **Agent 迭代记忆（agent memory）**：记录某个 Agent 自己在协作中沉淀出的经验、被纠正过的规则、能力边界、工作准则和 persona 演进。以 Markdown 文件存储，并只影响对应 Agent。
4. **全局偏好记忆（global preference memory）**：记录整个工作台或 Workspace 的默认偏好和协作规范，例如输出语言、文档风格、通用开发约定、废弃能力禁用规则。以 Markdown 文件存储。
5. **技能自进化产物（skill evolution artifact）**：Agent 与人完成一整个工作后，由系统判断该工作是否有复用价值；如果可复用，就自动生成一个标准 Skill，让它进入现有 Skills 系统并像普通 Skill 一样被编辑、禁用、删除和统计。它不是普通 memory item，也不注入长期记忆上下文。系统只负责注册 Skill，不自动绑定到任何 Agent；用户需要进入 Agent 配置手动添加该 Skill。

### 生命周期状态

- **工作上下文**：本轮 Run 和当前 Session 内的上下文，由 Chat 历史和 RunState 负责，不属于长期记忆。
- **历史记忆写入**：每天或每个聚合窗口把用户输入与 Agent 最终输出压缩成历史记忆，写入 SQLite `memory_nodes` 主表并同步到向量库。
- **Markdown 长期记忆**：用户、Agent、全局偏好记忆以 Markdown 文件保存，通过压缩摘要注入上下文。
- **Skill 生成记录 / Skill 草稿**：技能自进化链路的中间产物，最终应注册为标准 Skill，而不是作为普通 memory item 注入上下文。

建议首批 Markdown 记忆类型：

- `preference`：用户偏好，例如输出语言、风格、工具偏好。
- `profile`：用户或团队稳定信息，例如角色、职责、常用技术栈。
- `project`：当前项目事实，例如架构约定、目录约束、已废弃能力。
- `workflow`：反复使用的工作方式，例如提交前验证步骤。
- `correction`：用户纠正过的事实或规则，例如“不要再使用旧 Plan Runtime”。
- `negative`：负记忆，记录明确禁止或应避免的做法，例如“不要自动绑定 Skill 到 Agent”。

### 双轨存储模型

- **向量存储只保存历史记忆**：底层 `memory_nodes` 是“某个 Agent 某一天发生了什么”的摘要索引，不保存工具调用过程。
- **Markdown 文件保存长期偏好与迭代记忆**：user memory、agent memory、global preference memory 以 `.md` 文件存储，定期压缩摘要。
- **Agent 默认只看到最近 30 天节点树**：上下文只注入最近 30 天的节点树，不注入节点详情。
- **详情必须通过工具查询**：如果 Agent 需要知道某一天或某个日期范围发生了什么，必须调用记忆查询工具。
- **Agent 只知道有工具可查**：默认上下文告诉 Agent 可用的记忆查询工具和最近 30 天节点树，不直接展开历史对话。

## 整体架构

记忆系统是 ReAct Runtime 的外围增强层，由 hooks、tools、服务层、存储层和前端管理面共同组成。

```text
Chat / Run / AgentScope ReAct
  |
  | hooks
  v
Memory Runtime Adapter
  |-- MemoryRetrievalHook
  |-- MemoryReflectionHook
  |-- MemoryNodePersistHook
  |-- AgentIterationHook
  |-- SkillEvolutionHook
  |-- MemoryUsageAuditHook
  |
  | services
  v
Memory Service Layer
  |-- MemoryRetriever
  |-- MemoryNodePipeline
  |-- AgentIterationPipeline
  |-- SkillEvolutionPipeline
  |-- MemoryPolicyEngine
  |-- MemorySanitizer
  |
  | storage / integration
  v
SQLite / json_store / Skills System / Trace / RunState
  |
  | UI
  v
Memory Page / Skills Page / Agent Editor / Trace Page
```

### 模块边界

- **Runtime Adapter**：只负责把 hooks 和 tools 接到现有 ReAct 链路，不决定记忆业务语义。
- **Memory Service Layer**：承载节点聚合、节点检索、Markdown 摘要压缩、冲突处理、脱敏、安全校验和 Skill 自进化判断。
- **Storage**：保存历史记忆、Markdown 记忆文件、Skill 自进化生成记录、使用审计和设置。
- **Skills System Integration**：只接收通过 `skill_register` 注册的标准 Skill，不负责长期记忆检索。
- **Frontend**：Memory 页面管理长期记忆，Skills 页面管理已注册 Skill，Agent Editor 决定某个 Agent 是否添加某个 Skill。
- **Trace / RunState**：Trace 记录完整审计；RunState 只展示轻量提示和本轮使用摘要，不承载完整记忆内容。

### Hook 生命周期

记忆模块固定三个 hook 节点：

1. **模型运行前：查询与注入**  
   查询 user memory、agent memory、global preference memory 的 Markdown 摘要，生成最近 30 天历史记忆索引，并注入 Agent system prompt。

2. **Agent 完成运行后：反思与提炼**  
   基于用户输入、Agent 最终输出和用户反馈，判断是否需要提炼 user memory、agent memory，并判断是否可以提炼 Skill。该阶段不写节点向量库。

3. **Agent 运行完后：历史记忆持久化**
   把本轮用户输入和 Agent 最终输出聚合到当天 Agent 历史记忆，持久化到 SQLite `memory_nodes` 并同步到向量库。该阶段不记录工具调用过程。

### 主链路一：运行前查询与注入

```text
用户发送消息
-> readiness / intent_router 正常完成
-> runtime_builder 组装 Agent / Team / ToolScope / Skill context
-> MemoryRetrievalHook 调用 MemoryRetriever
-> 生成最近 30 天历史记忆索引
-> 读取 user / agent / global Markdown 摘要
-> MemoryPolicyEngine 排序、裁剪、处理冲突
-> MemorySanitizer 过滤敏感内容和过期内容
-> 注入 Agent system prompt 的 MemoryTree + Markdown 摘要段落
-> MemoryUsageAuditHook 写入 Trace / memory_usage_events
-> ReAct 正常执行
```

注入只改变上下文，不改变 Runtime architecture。记忆检索失败时必须降级为空结果，不能阻断 Chat。

### 主链路二：运行后反思与提炼

```text
Run 成功完成
-> MemoryReflectionHook 读取用户消息、Agent 最终输出、用户反馈
-> 注入摘要级现有 user / agent / global Markdown 记忆用于去重和冲突判断
-> 判断是否提炼 user memory Markdown
-> AgentIterationPipeline 判断是否提炼 agent memory Markdown
-> SkillEvolutionPipeline 判断是否可以生成标准 Skill
-> MemoryPolicyEngine 判断是否值得写入
-> MemorySanitizer 脱敏和裁剪
-> 写入 user / agent Markdown 文件，或注册标准 Skill
-> 发送 MEMORY_MARKDOWN_UPDATED / MEMORY_SKILL_* 事件
```

失败、取消、clarification-only Run 默认不提炼 Markdown 记忆或 Skill。反思失败只记录 Trace，不影响 Run 成功收尾。

### 主链路三：运行后历史记忆持久化

```text
Agent 运行完后
-> MemoryNodePersistHook 读取用户输入、Agent 最终输出、必要 Run 元数据
-> MemoryNodePipeline 不读取完整工具调用过程，不读取 tool observation
-> 聚合到当天 Agent 历史记忆
-> 写入 SQLite memory_nodes
-> 同步 upsert 到 VectorStoreBackend 的 memory_nodes collection
-> 发送 MEMORY_NODE_UPDATED / MEMORY_NODE_IGNORED
```

历史记忆持久化只关心“用户问了什么、Agent 最终答了什么、当天摘要是什么”。它不负责提炼用户偏好、Agent 迭代经验或 Skill。

### 主链路四：Skill 自进化

```text
一整个工作完成
-> SkillEvolutionRouterGate 先判断本轮是否值得进入 Skill 自进化
-> WorkflowEvidenceBuilder 从 RunState / Trace 提取工具调用链路摘要
-> SkillEvolutionHook 读取用户目标、最终产出和 workflow evidence
-> SkillEvolutionAgent 判断是否可复用、是否原子化、是否需要拆分为 Workflow
-> SkillEvolutionPipeline 生成 Skill 生成记录
-> skill_draft_create 生成标准 Skill 草稿
-> MemorySanitizer / Skill 安全校验
-> skill_register 写入现有 Skills 系统
-> Skills 页面出现新 Skill
-> 用户进入 Agent Editor 手动添加到某个 Agent
-> 该 Agent 后续运行时才可能注入和选择这个 Skill
```

Skill 自进化的输出是标准 Skill，不是普通 memory item。系统不得自动把新 Skill 绑定到任何 Agent。

Skill 自进化必须先经过 router gate，再进入提炼 Agent。router gate 只做是否值得继续分析的粗筛，不生成 Skill 内容，不决定注册结果。它至少需要确认：

- 本轮 Run 已成功完成，不是失败、取消、clarification-only 或用户中断。
- 本轮存在可复用的工作成果，例如实现流程、排查方法、设计方案、迁移步骤、测试策略或稳定操作规范。
- 本轮不是一次性闲聊、纯偏好记忆、短问答、临时路径处理或高度私有客户上下文。
- 本轮没有明显敏感信息、密钥、token、客户隐私或不可脱敏内容。
- 本轮具备足够过程证据；如果缺少工具链或产出证据，必须降低置信度或跳过 Skill 自进化。

router gate 通过后，必须构建 `workflowEvidence` 再调用 SkillEvolutionAgent。`workflowEvidence` 只保存摘要，不保存原始 tool observation、chain-of-thought、长文件内容或敏感值。建议结构包括：

- `successfulToolChain`：最终有效的成功工具链路，按执行顺序记录工具名、目的、输入摘要、输出变量摘要和结果摘要。
- `usefulFailures`：少量有复用价值的失败教训，例如稳定约束、错误 API 参数、无效方案或未来容易踩坑的反面案例。
- `discardedFailures`：只记录被丢弃失败的数量和原因分类，不保存具体噪音。
- `changedFiles` / `artifacts` / `tests`：可选记录改动文件、产物和验证命令摘要。
- `variableFlow`：记录工具之间的上下文依赖，例如 `search_user.user_id -> send_email.user_id`。

失败工具调用默认不进入 Skill 主步骤。只有失败暴露稳定约束、帮助形成最终正确方案、被用户明确纠正，或未来复用时很容易再次踩坑时，才允许进入 `usefulFailures` 并转写为反面案例 / 注意事项。

Skill 颗粒度必须受控。SkillEvolutionAgent 需要判断 `granularity`：

- `too_small`：只是一次命令、一次局部文件修改、一次临时修复，不生成 Skill。
- `atomic`：单一目标、单一触发条件、输入输出边界清晰，可以生成标准 Skill。
- `too_broad`：包含多个目标、多个业务阶段或多个独立能力，不生成超级 Skill，只给出 `suggestedSplit` 和 `requiresWorkflow=true`。

系统禁止生成 `do_everything_*` 这类超级 Skill。一次工作如果包含多个可复用能力，应该拆成多个原子 Skill 候选；跨阶段组合由未来 Workflow 编排器负责。

Skill 草稿必须正确抽象上下文依赖，禁止硬编码本轮具体返回值。工具链中如果存在“先查询 ID，再用 ID 调用下一步”的依赖，Skill 内容必须写成变量传递：

```text
1. 调用 search_user 查找目标用户，保存返回的 user_id。
2. 将上一步返回的 user_id 作为 send_email 的 user_id 参数。
```

不得写成：

```text
1. 调用 search_user 查找目标用户。
2. 使用 user_id=12345 调用 send_email。
```

需要参数化的典型值包括 session id、message id、user id、数据库主键、临时 URL、本地临时路径、运行时返回值、token、一次性文件名和环境特定配置。无法确认变量来源时，SkillEvolutionAgent 必须降低 confidence 或拒绝生成 Skill。

### 冲突与优先级

记忆注入前必须做冲突处理。建议优先级：

```text
本轮用户明确指令
> 系统安全和工具权限
> negative / correction 负记忆
> Agent 迭代记忆
> Project / Workspace 事实
> 用户单独记忆
> 全局偏好记忆
> Skill 使用经验和低置信自动记忆
```

负记忆和纠错记忆必须优先于普通偏好记忆。任何长期记忆都不能覆盖本轮用户明确要求。

### 可扩展层

首版以历史记忆、三类 Markdown 记忆和 Skill 自进化产物为主，同时在 schema 和 UI 筛选上预留扩展：

- **Project / Workspace 记忆**：记录某个项目或工作区的架构事实、命令、流程和禁用规则。
- **Team 记忆**：记录 Team 协作模式、临时 Member 角色组合和 Leader 分派经验，后续可作为 Leader 动态组队的参考材料。
- **Tool 使用记忆**：记录 MCP / HTTP / Bash / Skill 的使用经验、失败原因和参数模板，但必须脱敏。
- **Artifact 记忆**：索引可复用产物、模板、报告和脚本，后续与 `ARTIFACT_*` 生命周期联动。

## MVP 冻结细则

本节用于冻结第一版实现边界。未列入本节的复杂能力默认不进入 MVP。

### 反思提炼标准

`MemoryReflectionHook` 只处理已经成功完成的 Run。失败、取消、clarification-only 的 Run 不提炼 Markdown 记忆，也不生成 Skill。

写入 user memory 的条件：

- 用户明确表达长期偏好，例如输出语言、交付风格、常用技术栈。
- 用户明确说“以后”“下次”“默认”“记住”等长期意图。
- 用户信息对多个 Agent 都有帮助，且不绑定某个 Agent 的工作方式。

写入 agent memory 的条件：

- 用户纠正了当前 Agent 的工作方式。
- 当前 Agent 暴露出稳定能力边界或反复犯错点。
- 用户希望这个 Agent 保持某种 persona、语气、检查流程或交付标准。
- 某个流程只对当前 Agent 有意义，不应提升为全局偏好。

写入 global preference memory 的条件：

- 规则适用于整个工作台或 Workspace。
- 规则能影响多个 Agent、Team 或未来项目。
- 规则属于安全、流程、语言、文档风格、废弃能力禁用等全局约定。

生成 Skill 的条件：

- 本次工作有完整目标、输入、步骤和输出。
- 未来相似任务可能复用。
- 可以抽象成 Skill 描述、触发条件、执行步骤、禁用场景和输出格式。
- 不依赖本次私有上下文、密钥、客户数据或一次性路径。
- 先通过 `SkillEvolutionRouterGate`，确认值得进入 Skill 自进化链路。
- 具备摘要化 `workflowEvidence`，能说明最终成功路径、验证结果和必要变量流。
- 颗粒度为 `atomic`：只解决一个稳定目标，有单一触发条件和清晰输入输出边界。
- 如果颗粒度为 `too_broad`，只能记录拆分建议和 `requiresWorkflow=true`，不能注册为单个超级 Skill。
- 工具调用依赖必须抽象为变量传递，不能把本轮具体 ID、路径、URL 或返回值硬编码进 Skill。

忽略条件：

- 临时上下文、闲聊、一次性路径、临时 token、密钥、隐私数据。
- 与已有 Markdown 记忆重复且没有新增信息。
- 置信度低或模型无法说明来源。
- 缺少工具链 / 产出 / 验证证据，无法确认工作真的完成。
- 只能形成过大 Workflow 或多个独立能力，且无法拆出稳定原子 Skill。

### Markdown 文件模板

user / agent / global Markdown 文件使用统一结构：

```md
# Summary

# Stable Preferences

# Corrections

# Negative Rules

# Source References
```

运行时只注入 `Summary`、`Negative Rules` 和必要的高优先级条目，不注入完整 Markdown。

### 节点树格式

模型运行前默认注入最近 30 天节点树。节点树只用于让 Agent 判断“是否需要查询详情”，不得包含完整节点内容。

建议格式：

```text
Memory node tree, recent 30 days:
- 2026-07-03
  - agent: code-assistant
    nodeId: mn_20260703_code
    title: 记忆模块设计讨论
    summary: 讨论了双轨存储、Zvec provider、三个 hook 节点。
    tags: memory,zvec,hooks
```

超过预算时按日期从近到远裁剪；同一天多个节点时按 Agent 分组。

### 记忆查询工具格式

`memory_node_tree` 输入：

```json
{
  "agentId": "optional",
  "dateFrom": "2026-07-01",
  "dateTo": "2026-07-03",
  "limit": 30
}
```

`memory_node_tree` 输出：

```json
{
  "nodes": [
    {
      "nodeId": "mn_20260703_code",
      "date": "2026-07-03",
      "agentId": "code-assistant",
      "title": "记忆模块设计讨论",
      "summary": "讨论了双轨存储、Zvec provider、三个 hook 节点。",
      "tags": ["memory", "zvec", "hooks"]
    }
  ]
}
```

`memory_node_query` 输入：

```json
{
  "agentId": "optional",
  "dateFrom": "2026-07-01",
  "dateTo": "2026-07-03",
  "query": "hook 节点怎么设计",
  "limit": 10
}
```

`memory_node_query` 输出：

```json
{
  "results": [
    {
      "nodeId": "mn_20260703_code",
      "date": "2026-07-03",
      "agentId": "code-assistant",
      "userInputs": ["..."],
      "agentFinalOutputs": ["..."],
      "summary": "...",
      "sourceRunIds": ["..."]
    }
  ],
  "meta": {
    "queryTermCount": 2,
    "vectorCandidateCount": 3,
    "lexicalCandidateCount": 2,
    "mergedCandidateCount": 4,
    "returnedCount": 4,
    "topScore": 1.25,
    "empty": false,
    "retrievalSources": ["lexical", "vector"],
    "resultQuality": [
      {
        "nodeId": "mn_20260703_code",
        "rankScore": 1.25,
        "vectorScore": 0.9,
        "matchedTerms": ["hook"],
        "sources": ["lexical", "vector"]
      }
    ]
  }
}
```

工具输出不得包含工具调用过程、tool observation 或模型中间推理。

### 压缩摘要策略

- 每次 Markdown 写入后更新对应文件的 `Summary`。
- 原始条目超过阈值后压缩，MVP 建议阈值为 8,000 字符。
- 最近 20 条短原文可保留在对应章节，其余归入 `Summary`。
- 压缩失败时保留原文，不影响 Run 成功收尾。
- 压缩模型必须是系统基础设施 Agent：无默认工具、无 MCP、无 Skills、无 Bash/HTTP。

### 隐私与脱敏

MemorySanitizer 必须在写入节点、Markdown 和 Skill 前执行。

必须跳过或脱敏：

- API key、token、password、secret、cookie、private key。
- 大段文件内容、日志、命令输出。
- 用户明确说“不要记”的内容。
- 客户数据、个人敏感信息，除非后续有明确配置允许。
- 一次性路径、临时 URL、短期验证码。

历史记忆只保存用户输入和 Agent 最终输出的压缩摘要。即便最终输出包含敏感信息，也必须先脱敏再写入。

### Node 与 Markdown 的关系

- `memory_nodes` 是历史发生事实的摘要索引。
- Markdown 记忆是长期偏好、纠正和行为规则的事实源。
- 同一信息可以同时出现在节点摘要和 Markdown 中，但注入时 Markdown 优先。
- 如果 Markdown 和节点历史冲突，以最新 Markdown 规则为准；本轮用户明确指令优先于二者。

### 失败与降级

- `MemoryRetrievalHook` 失败：不注入记忆，Run 继续。
- `MemoryReflectionHook` 失败：不更新 Markdown，不生成 Skill，Run 继续。
- `MemoryNodePersistHook` 失败：Run 继续，Trace 记录错误，后续可重试。
- `VectorStoreBackend` 写入失败：SQLite `memory_nodes` 仍保存，标记待重建；后续 rebuild 同步。
- Markdown 写入失败：不影响历史记忆持久化，Trace 记录错误。
- Skill 生成失败：不影响 Markdown 和历史记忆持久化。

## 作用域模型

每条记忆都必须有明确作用域，避免跨项目污染。

建议作用域字段：

- `layer`：`node|user|agent|global_preference|skill_evolution`。
- `workspaceId`：未来多 Workspace 的归属；MVP 可用默认值。
- `projectId`：当前项目或仓库；MVP 可从 workspace path 生成稳定 ID。
- `agentId`：只对某个 Agent 生效时填写。
- `teamId`：只对某个 Team 生效时填写。
- `skillId`：技能自进化或 Skill 使用经验关联的 Skill。
- `userId`：桌面单用户 MVP 可用默认用户。
- `visibility`：`private|workspace|project|agent|team|skill`。

检索优先级建议：

1. 当前 Agent / Team 明确绑定的记忆。
2. 当前 Project 记忆。
3. 当前 Workspace 记忆。
4. 用户全局偏好。

同类冲突时，作用域越近优先级越高，用户 pin 的记忆优先级最高。用户本轮明确指令永远高于任何长期记忆。

## 接入原则：hooks + tools

记忆模块全部通过 hooks 和 tools 实现，不新增 Runtime 分支。

### Hooks

Hooks 负责被动生命周期事件，只做“运行前查询注入、运行后反思提炼、运行后历史记忆持久化、审计”，不改变 Chat architecture。

- `MemoryRetrievalHook`：模型运行前，查询 user / agent / global Markdown 摘要和最近 30 天历史记忆索引，并生成注入片段。
- `MemoryUsageAuditHook`：在 Run 开始、注入、结束时记录本轮使用了哪些记忆。
- `MemoryReflectionHook`：Agent 完成运行后，并行调用 `UserMemoryReflectionAgent`、`AgentMemoryReflectionAgent`、`GlobalMemoryReflectionAgent`，分别提炼 user memory、agent memory 和 global memory。
- `AgentIterationHook`：由 `MemoryReflectionHook` 调用，识别针对某个 Agent 的纠正、偏好和能力边界，写入 agent memory Markdown。
- `SkillEvolutionHook`：由 `MemoryReflectionHook` 调用，在一整个工作完成后判断该工作是否可复用；若可复用，生成 Skill 草稿或直接注册标准 Skill。
- `MemoryNodePersistHook`：Agent 运行完后，把用户输入和 Agent 最终输出聚合为当天历史记忆并持久化到 `memory_nodes` 与向量库。

### 提炼 Agent 与记忆操作

`MemoryReflectionHook` 使用三个专用基础设施 Agent 并行完成 Markdown 记忆提炼：

- `UserMemoryReflectionAgent`：只处理 user memory，绑定当前用户 Markdown 文件。
- `AgentMemoryReflectionAgent`：只处理 agent memory，绑定当前 Agent Markdown 文件。
- `GlobalMemoryReflectionAgent`：只处理 global preference memory，绑定当前 Workspace 全局 Markdown 文件。

运行时给每个 Agent 配置专用内部工具，并把工具绑定到它自己的具体 Markdown 记忆文件；模型不能传入任意路径，也不能越权操作其他记忆文件。

三路 Markdown 提炼 Agent 在 Chat 成功收尾后以后台任务启动，不阻塞 assistant message 持久化、`RUN_COMPLETED` 输出或主对话线路。后台提炼失败只影响本次记忆写入，不得把已成功完成的 Chat Run 改成失败。

- `memory_read`：读取当前绑定记忆文件的完整 Markdown 内容。
- `memory_add`：向当前绑定记忆文件的指定章节添加一条记忆。
- `memory_update`：精确替换当前绑定记忆文件中的某一条或某一段文本。
- `memory_delete`：精确删除当前绑定记忆文件中的某一条或某一段文本。
- `skill_draft_propose`：提交标准 Skill 草稿候选；工具本身不注册 Skill，也不绑定 Agent。

这些工具只给系统提炼 Agent 使用，不默认暴露给普通业务 Agent。后端在启动提炼 Agent 前选择本次允许操作的 memory scope，并注入该 scope 对应的文件地址和绑定工具。user / agent / global 三个 Markdown 文件由三个不同 Agent 并行处理，而不是让单个模型自由决定跨文件写入。

工具操作仍需遵循受控记忆操作语义：

```json
{
  "operation": "append | update | delete | skip",
  "scope": "user | agent | global_preference",
  "section": "Stable Preferences | Stable Behaviors | Profile | Workspace Rules | Corrections | Negative Rules",
  "note": "一条简短长期记忆",
  "confidence": 0.9
}
```

后续若要支持精确 `update`、`disable`、`delete`，Markdown 条目需要先拥有稳定条目 ID，避免模型靠文本匹配误改或误删。

### Tools

Tools 负责主动操作，让用户或 Agent 可以明确读写、请求记住、忘记、沉淀 Skill。

建议工具：

- `memory_search`：按层级、作用域、关键词检索记忆。
- `memory_node_tree`：查看最近 30 天或指定范围的记忆节点树。
- `memory_node_query`：按某一天或日期范围查询历史记忆详情。
- `memory_remember`：显式创建长期记忆。
- `memory_forget`：请求删除、归档或禁用某条记忆。
- `memory_update`：更新已有记忆内容、标签、作用域或 pin 状态。
- `agent_memory_note`：Agent 记录自己的迭代经验。
- `skill_evolution_evaluate`：判断一次完成的工作是否值得沉淀为 Skill。
- `skill_draft_create`：从一次工作过程生成标准 Skill 草稿。
- `skill_register`：把通过校验的 Skill 草稿写入现有 Skills 系统。注册后不自动添加到任何 Agent。

普通业务 Agent 默认不应拥有所有写入类 memory tools。首版建议只暴露只读 `memory_search` 和显式用户授权的写入工具；系统基础设施 Agent 可使用内部 tools 完成自动写入和 Skill 注册。

## 数据模型草案

新表建议使用新命名，避免和旧表语义混淆：

## 全局向量存储基础设施

向量数据库不是记忆模块私有能力，而是全局基础设施。MVP 默认选择 **Zvec** 作为嵌入式向量存储 provider，由 `VectorStoreBackend` 抽象统一管理。记忆模块只是使用其中一个 collection，后续 Knowledge、Artifact、Skill、Tool 使用经验等模块都可以创建自己的 collection。

SQLite 继续作为业务事实主库；向量数据库只保存可重建的检索文档副本、全文索引、向量索引和用于过滤的标量字段。

首次初始化时，实际启用的向量数据库通过环境变量选择，业务代码不得写死 Zvec：

```bash
VECTOR_STORE_PROVIDER=zvec
```

`VECTOR_STORE_PROVIDER` 只在向量存储第一次初始化时生效。初始化完成后，系统必须把最终策略持久化为 `VectorStorePolicy`；后续启动只能读取持久化策略，不能再被环境变量静默改变。

首批允许值：

- `zvec`：默认值，专业本地向量存储。
- `sqlite_vec`：轻量 SQLite extension 方案，作为备用实现。
- `lancedb`：后续可选升级实现。
- `disabled`：关闭向量存储，仅保留 SQLite 结构化过滤和关键词降级检索。

### 检索能力分级

向量存储 provider 和 embedding model 分开配置。客户没有 embedding model 时，不关闭 Zvec，只降级为标量过滤 + BM25 / 稀疏检索。

```bash
VECTOR_STORE_PROVIDER=zvec
EMBEDDING_PROVIDER=disabled
```

检索分级：

- **Level 0：无向量存储**  
  `VECTOR_STORE_PROVIDER=disabled`。只使用 SQLite 结构化过滤和关键词降级检索。

- **Level 1：稀疏检索 / BM25**  
  `VECTOR_STORE_PROVIDER=zvec` 且 `EMBEDDING_PROVIDER=disabled`。写入 Zvec 文档、标量字段和全文索引；不生成 dense embedding。检索使用标量过滤 + BM25 / 稀疏召回 + 业务重排。

- **Level 2：混合检索**  
  `VECTOR_STORE_PROVIDER=zvec` 且配置了 embedding provider。写入全文索引、稀疏检索字段和 dense embedding。检索使用标量过滤 + BM25 / 稀疏召回 + 稠密向量召回 + 业务重排。

embedding 配置建议：

```bash
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_BASE_URL=...
EMBEDDING_API_KEY=...
EMBEDDING_DIMENSIONS=1536
```

`EMBEDDING_PROVIDER=disabled` 是默认安全降级值。embedding 初始化失败时，不应阻止记忆模块启动；系统应降级到 Level 1，并在 Settings / Trace 中提示。

选择理由：

- Zvec 是 in-process vector database，不需要独立 server / daemon，符合 Electron 桌面端本地运行和离线使用的要求。
- Zvec 支持标量字段、向量检索、全文索引、BM25 和中文分词 tokenizer；没有 embedding model 时也能提供有用的稀疏检索。
- 记忆检索需要结构化过滤 + BM25 / 稀疏召回 + 可选稠密向量召回 + 重排，Zvec 可以承载 MemoryIndex 的混合检索能力。
- SQLite 可以继续专注业务事实、审计、设置和事务；Zvec 只做检索索引，职责更清楚。
- 后续如需替换为 sqlite-vec、LanceDB、Qdrant 等，只要保持 `VectorStoreBackend` 抽象不变。

需要验证的风险：

- Electron / PyInstaller 打包时必须验证 Zvec native wheel 在目标平台可用。
- SQLite 与 Zvec 是两套本地存储，必须设计一致性策略和索引重建能力。
- Zvec 不是事实源；任何索引损坏或版本升级，都必须能从 SQLite `memory_nodes` 全量重建。

暂不选择：

- **sqlite-vec + SQLite FTS5**：更轻，但更像在 SQLite 旁边补向量扩展，专业记忆检索能力较弱。
- **LanceDB**：适合作为后续升级路径，尤其适合更大的多模态数据和独立向量表。
- **Chroma**：Python persistent client 可用，但更像完整向量数据库应用层，容易和现有 SQLite 结构化存储重复。
- **Qdrant / Milvus**：能力强，但对桌面 MVP 偏重，更适合服务端或大规模部署。

### 向量库抽象与 collection

所有模块必须通过 `VectorStoreBackend` 抽象访问向量数据库，避免业务代码绑定 Zvec 或任何具体实现。所有向量数据库实现都必须实现这一套接口，例如 `ZvecVectorStoreBackend`、`LanceVectorStoreBackend`、`SqliteVecVectorStoreBackend`、`DisabledVectorStoreBackend`。

```python
class VectorStoreBackend:
    def ensure_collection(self, collection: VectorCollectionSpec) -> None: ...
    def upsert_document(self, collection: str, document: VectorDocument) -> None: ...
    def delete_document(self, collection: str, document_id: str) -> None: ...
    def rebuild_collection(self, collection: str, documents: list[VectorDocument]) -> None: ...
    def search(self, collection: str, query: VectorSearchQuery) -> list[VectorSearchHit]: ...
```

`MemoryRetriever`、`MemoryNodePipeline`、Memory Page 和其他业务层不得直接 import Zvec SDK，只能依赖 `VectorStoreBackend`。

首批 collection 建议：

- `memory_nodes`：历史记忆检索文档。向量存储只保存这类历史记忆索引。
- `skill_documents`：Skill 描述、触发条件和使用经验，后续辅助 Skill selector。
- `artifact_documents`：生成文件、报告、模板等可复用产物索引。
- `knowledge_documents`：未来知识库文档索引。
- `tool_usage_documents`：MCP / HTTP / Bash / Skill 使用经验，必须严格脱敏。

每个 collection 必须有独立 schema、稀疏检索字段、可选 embedding 模型、维度和重建策略。不同业务模块不得混用 collection。

### 初始化策略锁定

向量库结构一旦初始化就视为稳定策略，不能在普通启动过程中被环境变量改变。

建议持久化位置：`json_store` namespace `vector_store_settings`，key `policy`。

`VectorStorePolicy` 建议字段：

- `provider`
- `policy_version`
- `initialized_at`
- `collections`
- `embedding_provider`
- `embedding_model`
- `embedding_dimensions`
- `sparse_enabled`
- `dense_enabled`
- `schema_version`

启动规则：

1. 如果不存在 `VectorStorePolicy`，读取 `VECTOR_STORE_PROVIDER` 和 embedding 环境变量，初始化 provider、collection schema 和检索能力分级，然后写入 policy。
2. 如果已存在 `VectorStorePolicy`，忽略 `VECTOR_STORE_PROVIDER` 的变化，只按持久化 policy 创建 backend。
3. 如果环境变量和持久化 policy 不一致，在日志、Settings 或 Trace 中提示“当前向量策略已锁定”，但不得自动切换。
4. 如需更换 provider、embedding 维度、collection schema，必须走显式迁移 / rebuild 流程。

不允许自动变更的内容：

- provider，例如 `zvec` 改成 `lancedb`。
- collection schema。
- embedding dimensions。
- 同一 collection 内的 embedding 模型维度。
- 稠密向量字段是否存在。

允许普通设置调整的内容：

- topK。
- 重排权重。
- 是否启用 dense 召回，前提是 policy 已有 dense 字段。
- 最终注入 token budget。
- 失败时是否降级使用 sparse / BM25。

`VectorStoreBackend` 由配置层统一创建，建议入口为 `config/vector_store_settings.py`：

- 首次初始化时读取 `VECTOR_STORE_PROVIDER`。
- 初始化后读取持久化 `VectorStorePolicy`。
- 校验 provider 和 schema 是否受支持。
- 根据持久化 policy 创建对应 backend。
- provider 不可用或初始化失败时，按设置决定启动失败或降级到 `disabled`。

记忆模块使用 `memory_nodes` collection。`MemoryRetriever` 的顺序固定为：

1. SQLite 读取当前运行上下文和权限边界，确认可访问 scope。
2. 生成最近 30 天的历史记忆索引，默认只注入索引结构而不是详情。
3. 如 Agent 调用查询工具，则在 `memory_nodes` collection 内过滤：agent、日期范围、scope、status 等标量字段。
4. Zvec 召回：全文 / BM25 / 稀疏召回；如已配置 embedding，再追加稠密向量语义召回。
5. MemoryPolicyEngine 重排：日期范围、Agent、scope 距离、置信度、最近使用。
6. token budget 裁剪。

### 历史记忆 collection 文档建议

SQLite 的 `memory_nodes` 是历史记忆主表。向量数据库的 `memory_nodes` collection 存储可重建的历史记忆索引副本：

- `MemoryNodeIndexDocument`
  - `memory_node_id`
  - `workspace_id`
  - `project_id`
  - `agent_id`
  - `user_id`
  - `node_date`
  - `date_range_start`
  - `date_range_end`
  - `title`
  - `summary`
  - `conversation_digest`
  - `status`
  - `pinned`
  - `confidence`
  - `search_text`
  - `sparse_terms`
  - `embedding`
  - `embedding_model`
  - `embedding_dimensions`
  - `content_hash`
  - `updated_at`

一致性策略：

- 写入或更新 `memory_nodes` 后，同步 upsert Zvec 文档。
- 删除或禁用 `memory_nodes` 后，同步删除或标记 Zvec 文档。
- 启动时可做轻量一致性检查。
- 提供全量 rebuild：从 SQLite `memory_nodes` 重建 Zvec MemoryIndex。
- 没有 embedding model 时，只写入标量字段、`search_text` 和稀疏检索字段。
- 更换 provider、collection schema 或 embedding 维度时，必须走显式迁移 / rebuild，不允许普通启动自动修改。

### `memory_nodes`

历史记忆主表。一个记录通常表示某个 Agent 某一天的协作摘要；只保存用户输入、Agent 最终输出和压缩摘要，不保存工具调用过程。

- `id`
- `workspace_id`
- `project_id`
- `agent_id`
- `user_id`
- `node_date`
- `date_range_start`
- `date_range_end`
- `title`
- `summary`
- `conversation_digest`
- `search_text`
- `tags`
- `source`
- `source_session_id`
- `source_run_id`
- `source_message_id`
- `confidence`
- `status`：`active|disabled|archived|deleted`
- `pinned`
- `created_at`
- `updated_at`
- `last_used_at`
- `use_count`

### Markdown 记忆文件

用户记忆、Agent 迭代记忆和全局偏好记忆以 Markdown 文件保存，便于人类阅读、版本管理和直接编辑。

当前实现路径：

- 默认用户记忆：`backend/data/memory/users/user_memory.md`
- 默认 Agent 记忆：`backend/data/memory/agents/agent_memory.md`
- 默认全局记忆：`backend/data/memory/global/global_memory.md`
- 指定用户记忆：`backend/data/memory/users/{user_id}/user_memory.md`
- 指定 Agent 记忆：`backend/data/memory/agents/{agent_id}/agent_memory.md`
- 指定 Workspace 全局记忆：`backend/data/memory/global/{workspace_id}/global_memory.md`

系统不兼容读取旧 `default.md`。如本地仍有旧文件，应删除或手动迁移到上述明确命名文件。

Markdown 文件采用懒创建策略：

- Agent 创建时不创建 `backend/data/memory/agents/{agent_id}/agent_memory.md`。
- 首次读取不存在的 Agent 记忆时返回默认空模板，但不落盘。
- 首次保存或自动写入 Agent 记忆时才创建文件。
- Agent 删除时 MVP 不自动删除 Markdown 记忆，后续通过归档或孤儿记忆清理处理。

每个 Markdown 文件包含：

- 原始长期记忆条目。
- 压缩摘要。
- 更新时间。
- 来源 Run / Session 引用。
- 禁用或负记忆段落。

运行时只注入压缩摘要和必要的高优先级规则，不注入完整 Markdown。

### `memory_usage_events`

审计表。

- `id`
- `memory_node_id`
- `layer`
- `run_id`
- `session_id`
- `agent_id`
- `team_id`
- `skill_id`
- `event_type`：`retrieved|injected|ignored|created|updated|deleted|disabled|enabled`
- `reason`
- `created_at`

### `skill_evolution_records`

Skill 自进化生成记录表。最终产物必须注册为标准 Skill；本表只记录从协作过程到 Skill 的草稿、注册和审计状态。

- `id`
- `workspace_id`
- `project_id`
- `agent_id`
- `agent_name`
- `team_id`
- `user_id`
- `source_session_id`
- `source_run_id`
- `source_message_id`
- `router_gate`
- `workflow_evidence`
- `reusable`
- `reusable_reason`
- `trigger_description`
- `inputs_schema`
- `draft_skill_name`
- `draft_skill_description`
- `draft_skill_content`
- `draft_tags`
- `granularity`
- `requires_workflow`
- `suggested_split`
- `variable_flow`
- `risk_notes`
- `confidence`
- `status`：`draft|registered|skipped|failed`
- `registered_skill_id`
- `created_at`
- `updated_at`

### `memory_settings`

可放入 `json_store`，namespace 建议为 `memory_settings`。

- 是否启用记忆。
- 单轮最大注入条数。
- 单轮最大注入 token budget。
- Team 临时 Member session 运行前按 single agent 同策略注入本轮已解析记忆。
- 自动写入最低置信度。

## 运行时接入点

### 检索与注入

建议通过 `MemoryRetrievalHook` 在 system prompt 组装阶段执行检索。工程上可以由 `runtime_builder.py` 完成 Skill / ToolScope / Team context 后挂载 hook，hook 内部调用独立的 `memory_retriever.py`。

输出 `MemoryContext`：

- `items`：本轮选中的记忆。
- `injectionText`：可直接拼入 Agent system prompt 的精简文本。
- `tracePayload`：用于 Trace 审计，不直接驱动 Chat 主界面。

记忆过程审计使用独立 Trace event：`eventType=MEMORY_AUDIT`，写入 `run_events`，payload 固定包含 `type=memory` 和 `memoryType`。Agent 主动召回历史记忆时，`memory_node_tree` / `memory_node_query` 工具会记录 `query`、`filters`、`resultCount`、`resultNodeIds`、`retrievalSource=agent_tool`、`retrievalQuality` 和当前 `sourceRunId`。`retrievalQuality` 包含 query term 数、向量候选数、关键词候选数、合并后候选数、top score、空结果标记、命中来源和每条结果的 rank score / vector score / feedback score / matched terms。用户对召回命中的相关 / 不相关反馈以 `memory_retrieval_feedback` 结构化表作为排序事实源，并同步写入 `MEMORY_AUDIT.memory_retrieval_feedback` 便于审计追溯。Chat 页面不展示记忆检索、提炼、历史记忆持久化等过程；只允许通过 `MEMORY_SKILL_GENERATING` 展示“Skills 生成中”轻量提示，不展示 Skill 内容、生成结果或记忆过程。

注入位置：

- 单 Agent：注入主 Agent system prompt 的独立 `Memory` 段落。
- Team Leader：注入 Leader system prompt 的独立 `Memory` 段落。
- Team 临时 Member：按 single agent 同策略注入动态 Member system prompt；使用本轮 Team Leader 已解析的 user / agent / global Markdown 摘要和最近历史记忆索引，需要详情时仍通过 `memory_node_tree` / `memory_node_query` 主动查询。

注入格式必须短、可溯源、可覆盖：

```text
可用长期记忆：
- [project][preference][m_123] 用户偏好中文说明，代码标识符保持英文。
- [agent][correction][m_456] 不要恢复旧 Plan Runtime；Plan Mode 通过 middleware 接入。

如当前用户明确给出相反要求，以本轮用户消息为准。
```

### 节点聚合与 Markdown 压缩

建议通过 `MemoryNodePersistHook` 在 Agent 运行完后异步触发，不阻塞最终答案：

- 入口放在 `completion.py` 收尾之后的独立 `memory_node_pipeline.py`。
- 输入只包括用户消息、Agent 最终输出、必要的 Run 元数据和本轮已注入记忆 ID。
- 不读取完整工具调用过程，不把工具 observation 写入节点。
- 输出当天 Agent 节点摘要，写入或更新 `memory_nodes`。
- 对 user / agent / global Markdown 记忆，使用单独压缩摘要流程更新 `.md` 文件。
- 对失败、取消、clarification-only 的 Run 默认不写入节点。
- 低置信、敏感、一次性、重复或无法脱敏的内容直接忽略，并记录忽略原因。

自动写入模型应是系统基础设施 Agent：

- 不带默认工具。
- 不带 MCP / Skills / Bash / HTTP。
- 只输出结构化 JSON。
- 失败时只记录 Trace，不影响 Chat 成功收尾。

### Agent 迭代记忆

Agent 迭代记忆通过 `AgentIterationHook` 和 `agent_memory_note` tool 生成，重点记录“这个 Agent 下次应该如何变得更好”。

适合沉淀为 agent memory 的内容：

- 用户纠正了这个 Agent 的工作方式。
- 这个 Agent 在某类任务中形成了稳定流程。
- 这个 Agent 暴露出需要避免的能力边界。
- 用户希望这个 Agent 以后保持某种 persona、语气或交付标准。

Agent memory 默认只注入给对应 Agent；Team 临时 Member 使用本轮 Leader 解析后的记忆上下文，不把模板角色误当成持久 Agent session。

### Skill 自进化

Skill 自进化不是把经验写成一条普通记忆，而是把一次完成的协作沉淀成标准 Skill。

触发链路：

```text
Run / 工作完成
-> SkillEvolutionRouterGate 判断是否值得进入 Skill 自进化
-> WorkflowEvidenceBuilder 汇总成功工具链、有价值失败、变量流和验证结果
-> SkillEvolutionHook 观察摘要化完整过程
-> SkillEvolutionAgent 判断是否可复用、是否原子化、是否需要拆分
-> skill_draft_create 生成 Skill 草稿
-> skill_register 注册为标准 Skill
-> 用户进入 Agent 配置手动添加该 Skill
-> 后续该 Agent 的 Skill selector 才能正常选择
```

是否可复用的判断标准：

- 本次工作有明确目标、输入、步骤和输出。
- 过程不是一次性闲聊或高度私有的临时上下文。
- 方法对未来相似任务有复用价值。
- 可以抽象出触发描述、使用边界、执行步骤和产物格式。
- 草稿不会泄漏密钥、隐私、客户数据或项目敏感内容。
- router gate 已确认本轮值得进入 Skill 自进化；SkillEvolutionAgent 不能绕过 gate。
- 工具链证据显示工作已完成，并能区分成功路径和有价值失败教训。
- Skill 颗粒度必须是原子化能力；一个 Skill 只解决一个稳定目标。
- 如果一次工作包含多个目标，例如查机票、订酒店、打车和做攻略，不能生成 `do_everything_for_travel` 这类超级 Skill，只能建议拆成 `search_flights`、`book_hotel` 等原子 Skill，并交给 Workflow 编排器组合。
- 工具调用上下文依赖必须被参数化，例如把 `search_user` 返回的 `user_id` 作为变量传给 `send_email`，不能硬编码本轮具体数字。

SkillEvolutionAgent 建议输出字段：

- `reusable`：是否有复用价值。
- `confidence`：置信度。
- `granularity`：`too_small | atomic | too_broad`。
- `requiresWorkflow`：是否应该由 Workflow 编排器组合多个原子 Skill。
- `reason`：中文判断理由。
- `draft`：仅当 `granularity=atomic` 且通过安全校验时生成。
- `suggestedSplit`：当 `granularity=too_broad` 时给出原子 Skill 拆分建议。
- `variableFlow`：记录需要在 Skill 中保留的变量传递关系。

Skill 草稿内容必须包含：

- 单一触发条件。
- 输入变量和来源。
- 成功工具链抽象后的步骤。
- 失败教训 / 注意事项，且只保留有复用价值的失败。
- 禁止硬编码的运行时值列表。
- 输出格式或完成判定。

注册后的 Skill 必须和手动创建的 Skill 一样：

- 存入现有 Skills 系统。
- 出现在 Skills 页面。
- 可编辑、禁用、删除、导入导出。
- 不自动绑定到任何 Agent。
- 只有用户进入 Agent 配置手动添加后，才可被该 Agent 注入。
- 只有进入该 Agent 的 Skill 范围后，才可被 Skill selector 选择。
- 有来源 Run、创建 Agent、版本、使用次数和成功率审计。

首版策略建议：自动生成 Skill 草稿并注册为标准 Skill。注册完成后只进入 Skills 库，不自动启用到任何 Agent；是否让某个 Agent 使用它，由用户进入 Agent 配置手动添加。

## 写入与管理策略

MVP 不做记忆审批流，也不做记忆审批候选队列。Skill 自进化队列保留在 Memory 页面，用于查看从协作过程沉淀到标准 Skill 的中间态、注册状态和失败原因。记忆写入策略是：

- 明确、稳定、低风险的历史摘要直接写入 `memory_nodes`。
- user / agent / global 长期偏好写入 Markdown 文件，并维护压缩摘要。
- 不确定、低置信、重复、敏感或一次性的记忆内容直接忽略，不进入记忆审批队列。
- Chat 不展示“已更新记忆”等记忆过程提示；Skill 自进化只展示“Skills 生成中”轻量提示。
- Memory 页面只负责查看历史记忆、查询详情、编辑 Markdown 记忆、查看 Skill 自进化队列、删除、禁用和审计，不负责审批。当前首版已支持“Agent -> 时间节点 -> 具体记忆”的历史记忆查看、Markdown 编辑和 Skill 自进化队列查看；删除、禁用、设置和审计详情留到下一阶段。
- 删除历史记忆使用软删除，保留审计事件。

后续如确实需要审批流，必须单独设计生命周期、RunState、前端入口和 API，不在 MVP 中预留半套 approve / reject 状态。

## Runtime Event 草案

正式实现前必须把以下事件补入 `docs/chat-sse-event-contract.md`、后端 schema、前端类型和 projector。

建议事件：

- `MEMORY_RETRIEVAL_STARTED`
- `MEMORY_RETRIEVAL_COMPLETED`
- `MEMORY_INJECTED`
- `MEMORY_NODE_CREATED`
- `MEMORY_NODE_UPDATED`
- `MEMORY_NODE_IGNORED`
- `MEMORY_NODE_DELETED`
- `MEMORY_MARKDOWN_UPDATED`
- `MEMORY_AGENT_ITERATION_UPDATED`
- `MEMORY_SKILL_EVOLUTION_STARTED`
- `MEMORY_SKILL_DRAFT_CREATED`
- `MEMORY_SKILL_REGISTERED`
- `MEMORY_SKILL_EVOLUTION_SKIPPED`

Chat 主界面 MVP 不依赖完整记忆事件，不展示记忆提炼或历史记忆持久化过程。完整审计进入 Trace / Memory 页面；Skill 自进化只展示“Skills 生成中”轻量提示。

## API 草案

- `GET /api/memory/nodes/tree`
- `GET /api/memory/nodes/query`
- `GET /api/memory/nodes/{id}`
- `PATCH /api/memory/nodes/{id}`
- `DELETE /api/memory/nodes/{id}`
- `GET /api/memory/markdown/user`
- `PUT /api/memory/markdown/user`
- `GET /api/memory/markdown/global`
- `PUT /api/memory/markdown/global`
- `GET /api/memory/markdown/agent/{agent_id}`
- `PUT /api/memory/markdown/agent/{agent_id}`
- `GET /api/memory/settings`
- `PUT /api/memory/settings`

Memory 页面主入口只使用唯一 user / global 的 canonical 路由，不暴露 userId 或 workspaceId；Agent 记忆按 Agent ID 选择。所有接口必须按作用域过滤，后续接 Workspace 时不能泄漏其他 Workspace 记忆。

## 前端页面草案

新增 Memory 页面，首版包含：

- 历史记忆：先选择 Agent，再选择时间节点，最后查看具体记忆。
- 历史查询：按某一天或日期范围查询历史记忆详情。
- Markdown 记忆编辑：用户记忆、Agent 迭代记忆、全局偏好记忆。

首版已实现导航入口、Markdown 记忆编辑和历史记忆三段式查看。用户记忆和全局记忆在 UI 中各只有一个入口；Agent 记忆通过 Agent 下拉选择，每个 Agent 只对应一个 `agent_memory.md`。

后续补齐：

- 记忆详情抽屉：来源 Run / Session、使用次数、最近使用、审计记录。
- Skill 自进化队列：展示自动生成的 Skill 草稿、复用理由、风险提示、router gate、workflow evidence 和注册状态。
- 设置区：启用开关、自动写入最低置信度、注入预算和召回质量摘要。
- 历史记忆删除 / 禁用操作：底层对 `memory_nodes` 使用软删除并写审计。

旧 Agent 编辑页中的 `memoryEnabled` 已从当前代码移除。新方案如需 Agent 级配置，应定义更明确的 `memoryPolicy` / `memoryScope`，不要恢复旧布尔开关。

## 旧表处理策略

历史开发库可能存在：

- `memory_facts`
- `memory_events`
- `memory_candidates`
- `agents.memory_enabled`

建议策略：

1. 当前代码不再创建、读取或写入旧表与旧字段。
2. 新系统使用新表和新 API。
3. MVP 不迁移旧表数据到新记忆；后续如需迁移，必须提供只读预览和明确导入策略。
4. 已存在于本地开发库中的旧表不自动 `DROP`，避免破坏历史数据；如需清库应走显式维护脚本。
5. 新 UI 不直接展示旧表数据。

## 实施阶段

### Phase 0：设计冻结

- 完成本文评审。
- 更新 `docs/chat-sse-event-contract.md` 的正式 `MEMORY_*` 契约。
- 明确 embedding 配置和降级策略：无 embedding 时使用 Zvec 标量过滤 + BM25 / 稀疏检索，有 embedding 时启用稠密向量混合召回。

### Phase 1：后端基础设施

- 新增 SQLite schema 和 storage 方法。
- 新增 Pydantic schema。
- 新增 `/api/memory/*` 路由。
- 明确 hooks 和 memory tools 的内部接口，不新增 Runtime 分支。
- 补 storage 和 API 单测。

### Phase 2：hooks + 只读检索与注入

- 实现 `memory_retriever.py`。
- 实现 `MemoryRetrievalHook` 和 `MemoryUsageAuditHook`。
- 在 `runtime_builder.py` 挂载单 Agent / Leader 记忆 hook。
- 写入 Trace / Runtime Event。
- 补 RunState / Trace 回归测试。

### Phase 3：反思提炼与历史记忆持久化

- 实现 `memory_node_pipeline.py`。
- 实现 `MemoryReflectionHook`、`AgentIterationHook` 和 `MemoryNodePersistHook`。
- 实现 Markdown 记忆压缩摘要流程。
- 接入 `completion.py` 后处理或等价 Run 完成 hook。
- 确保提取失败不影响 Chat 成功收尾。

### Phase 4：Skill 自进化

- 已实现规则版 `SkillEvolutionRouterGate`，先判断是否值得进入 Skill 自进化链路。
- 已实现 `WorkflowEvidenceBuilder` 首版，从 RunState / Trace 中提取成功工具链、有价值失败、变量流、改动文件和验证结果摘要。
- 已扩展 `SkillEvolutionAgent` schema，加入 `granularity`、`requiresWorkflow`、`suggestedSplit` 和 `variableFlow`。
- 已实现 Skill 自进化候选持久化记录，保存 gate reason、workflow evidence、granularity、suggested split、variable flow 和 risk notes。
- 已实现标准 Skill 注册首版：只将 `granularity=atomic`、高置信度、通过安全校验且未发现相似已安装 Skill 的候选注册到现有 Skills 系统。
- 已实现相似 Skill 合并首版：命中相似能力时关联已有 Skill，不创建重复 Skill。
- 已实现标准 Skill 包落盘和导出：自动生成 / 手动注册 Skill 都写入 `backend/data/skill_packages/{skill_name}/SKILL.md`，并可从 Skills 页面按 Skill 名称导出 zip。
- 注册后的 Skill 不会自动绑定到任何 Agent，用户仍需进入 Agent 配置手动添加。
- 对 `too_broad` 结果只保存拆分建议，不注册超级 Skill；未来由 Workflow 编排器组合原子 Skill。
- 已在 Memory 页面补注册失败原因可视化和队列操作入口；后续继续增强候选检索筛选和模型重跑策略。

### Phase 5：前端 Memory 页面

- 已新增 Memory 页面和导航入口。
- 已支持历史记忆三段式查看、按日期查询和 Markdown 记忆管理。
- 已支持设置区、审计详情、历史记忆禁用 / 删除 / 恢复，以及 Skill 自进化队列操作能力。
- Chat 中只展示 “Skills 生成中” 轻量提示；记忆过程和本轮注入摘要进入 Trace / Memory 页面。

### Phase 6：检索增强

- 引入更多 embedding provider、重建任务和混合检索重排策略。
- 支持相似记忆合并建议。
- 支持 Workspace 多租户隔离后的跨项目策略。

## 测试重点

- 记忆检索不会跨 Project / Workspace 泄漏。
- 用户本轮明确指令优先于旧记忆。
- 低置信、敏感、一次性内容不会写入长期节点或 Markdown 记忆。
- 自动写入失败不影响 Chat Run 完成。
- Team 临时 Member 按 single agent 同策略注入本轮记忆，且不能跨 Project / Workspace 泄漏。
- 删除或禁用记忆后不会再被注入。
- RunState 刷新恢复不会重复展示“已更新记忆”提示。
- MVP 不从旧表迁移 active memory。
- 记忆 hook 失败不会影响 ReAct 主链路成功收尾。
- 历史记忆不保存工具调用过程，只保存用户输入、Agent 最终输出和压缩摘要。
- 默认上下文只注入最近 30 天节点树，节点详情必须通过工具按日期或日期范围查询。
- Skill 自进化不会把隐私、密钥或一次性项目细节写入可复用 Skill。
- 自动生成的 Skill 必须能被现有 Skills 页面和 selector 按标准 Skill 处理。
