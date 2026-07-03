# 记忆系统重设计方案

> 状态：草案，2026-07-03。本文用于重新规划记忆模块，不复用旧三层记忆模块、旧 `memory_*` 事件或旧运行时补丁。

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
- 不把记忆能力塞进 `intent_router.py`，也不让记忆系统决定 Runtime architecture 或 Team Worker 选择。
- 不新增 `memory` runtime.kind，也不恢复旧 Plan / Subagent 式分支；记忆只能作为 ReAct 周边增强能力存在。

## 产品语义

记忆系统按“长期节点记忆 + Markdown 长期记忆”双轨设计。向量存储只保存长期节点记忆；用户记忆、Agent 迭代记忆和全局偏好记忆以 Markdown 文件存储，并通过压缩摘要控制上下文体积。

### 四层记忆

1. **长期节点记忆（memory nodes）**：按 Agent + 日期聚合当天协作过程，只记录用户输入、Agent 最终输出和压缩摘要，不记录工具调用过程。节点进入向量库，用于按日期、范围和语义检索。
2. **用户单独记忆（user memory）**：记录当前用户的长期信息、偏好、协作习惯、常用项目和表达方式。以 Markdown 文件存储，并定期压缩摘要。
3. **Agent 迭代记忆（agent memory）**：记录某个 Agent 自己在协作中沉淀出的经验、被纠正过的规则、能力边界、工作准则和 persona 演进。以 Markdown 文件存储，并只影响对应 Agent。
4. **全局偏好记忆（global preference memory）**：记录整个工作台或 Workspace 的默认偏好和协作规范，例如输出语言、文档风格、通用开发约定、废弃能力禁用规则。以 Markdown 文件存储。
5. **技能自进化记忆（skill evolution memory）**：Agent 与人完成一整个工作后，由系统判断该工作是否有复用价值；如果可复用，就自动生成一个标准 Skill，让它进入现有 Skills 系统并像普通 Skill 一样被编辑、禁用、删除和统计。系统只负责注册 Skill，不自动绑定到任何 Agent；用户需要进入 Agent 配置手动添加该 Skill。

### 生命周期状态

- **工作上下文**：本轮 Run 和当前 Session 内的上下文，由 Chat 历史和 RunState 负责，不属于长期记忆。
- **节点写入**：每天或每个聚合窗口把用户输入与 Agent 最终输出压缩成长期节点记忆，写入 SQLite 主表并同步到向量库。
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

- **向量存储只保存长期节点记忆**：节点是“某个 Agent 某一天发生了什么”的摘要索引，不保存工具调用过程。
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
- **Storage**：保存长期节点记忆、Markdown 记忆文件、Skill 自进化生成记录、使用审计和设置。
- **Skills System Integration**：只接收通过 `skill_register` 注册的标准 Skill，不负责长期记忆检索。
- **Frontend**：Memory 页面管理长期记忆，Skills 页面管理已注册 Skill，Agent Editor 决定某个 Agent 是否添加某个 Skill。
- **Trace / RunState**：Trace 记录完整审计；RunState 只展示轻量提示和本轮使用摘要，不承载完整记忆内容。

### Hook 生命周期

记忆模块固定三个 hook 节点：

1. **模型运行前：查询与注入**  
   查询 user memory、agent memory、global preference memory 的 Markdown 摘要，生成最近 30 天节点树，并注入 Agent system prompt。

2. **Agent 完成运行后：反思与提炼**  
   基于用户输入、Agent 最终输出和用户反馈，判断是否需要提炼 user memory、agent memory，并判断是否可以提炼 Skill。该阶段不写节点向量库。

3. **Agent 运行完后：节点持久化**  
   把本轮用户输入和 Agent 最终输出聚合到当天 Agent 节点，持久化到 SQLite `memory_nodes` 并同步到向量库。该阶段不记录工具调用过程。

### 主链路一：运行前查询与注入

```text
用户发送消息
-> readiness / intent_router 正常完成
-> runtime_builder 组装 Agent / Team / ToolScope / Skill context
-> MemoryRetrievalHook 调用 MemoryRetriever
-> 生成最近 30 天记忆节点树
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

### 主链路三：运行后节点持久化

```text
Agent 运行完后
-> MemoryNodePersistHook 读取用户输入、Agent 最终输出、必要 Run 元数据
-> MemoryNodePipeline 不读取完整工具调用过程，不读取 tool observation
-> 聚合到当天 Agent 记忆节点
-> 写入 SQLite memory_nodes
-> 同步 upsert 到 VectorStoreBackend 的 memory_nodes collection
-> 发送 MEMORY_NODE_UPDATED / MEMORY_NODE_IGNORED
```

节点持久化只关心“用户问了什么、Agent 最终答了什么、当天摘要是什么”。它不负责提炼用户偏好、Agent 迭代经验或 Skill。

### 主链路四：Skill 自进化

```text
一整个工作完成
-> SkillEvolutionHook 读取完整工作过程摘要
-> SkillEvolutionAgent 判断是否可复用
-> SkillEvolutionPipeline 生成 Skill 生成记录
-> skill_draft_create 生成标准 Skill 草稿
-> MemorySanitizer / Skill 安全校验
-> skill_register 写入现有 Skills 系统
-> Skills 页面出现新 Skill
-> 用户进入 Agent Editor 手动添加到某个 Agent
-> 该 Agent 后续运行时才可能注入和选择这个 Skill
```

Skill 自进化的输出是标准 Skill，不是普通 memory item。系统不得自动把新 Skill 绑定到任何 Agent。

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

首版以四层记忆为主，同时在 schema 和 UI 筛选上预留扩展：

- **Project / Workspace 记忆**：记录某个项目或工作区的架构事实、命令、流程和禁用规则。
- **Team 记忆**：记录 Team 协作模式、Worker 组合和 Leader 分派经验，后续可辅助 Worker Selector。
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

忽略条件：

- 临时上下文、闲聊、一次性路径、临时 token、密钥、隐私数据。
- 与已有 Markdown 记忆重复且没有新增信息。
- 置信度低或模型无法说明来源。

### Markdown 文件模板

user / agent / global Markdown 文件使用统一结构：

```md
# Summary

<!-- 压缩摘要，运行时优先注入。 -->

# Stable Preferences

<!-- 长期偏好和稳定事实。 -->

# Corrections

<!-- 用户纠正、反复犯错点和修正规则。 -->

# Negative Rules

<!-- 明确禁止或应避免的做法，优先级高于普通偏好。 -->

# Source References

<!-- 来源 session/run/message 引用，只保留短引用，不复制长对话。 -->
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
  ]
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

节点记忆只保存用户输入和 Agent 最终输出的压缩摘要。即便最终输出包含敏感信息，也必须先脱敏再写入。

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
- Markdown 写入失败：不影响节点持久化，Trace 记录错误。
- Skill 生成失败：不影响 Markdown 和节点持久化。

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

Hooks 负责被动生命周期事件，只做“运行前查询注入、运行后反思提炼、运行后节点持久化、审计”，不改变 Chat architecture。

- `MemoryRetrievalHook`：模型运行前，查询 user / agent / global Markdown 摘要和最近 30 天节点树，并生成注入片段。
- `MemoryUsageAuditHook`：在 Run 开始、注入、结束时记录本轮使用了哪些记忆。
- `MemoryReflectionHook`：Agent 完成运行后，判断是否需要提炼 user memory、agent memory 或 Skill。
- `AgentIterationHook`：由 `MemoryReflectionHook` 调用，识别针对某个 Agent 的纠正、偏好和能力边界，写入 agent memory Markdown。
- `SkillEvolutionHook`：由 `MemoryReflectionHook` 调用，在一整个工作完成后判断该工作是否可复用；若可复用，生成 Skill 草稿或直接注册标准 Skill。
- `MemoryNodePersistHook`：Agent 运行完后，把用户输入和 Agent 最终输出聚合为当天节点并持久化到 `memory_nodes` 与向量库。

### 提炼 Agent 与记忆操作

`MemoryReflectionHook` 可以调用专用基础设施 Agent 完成提炼，但该 Agent 不直接持有真实写文件工具。运行时给它配置专用内部工具：

- `memory_context_read`：读取摘要级现有 user / agent / global Markdown 记忆，用于判断是否重复、是否冲突、应该写入哪个 scope。
- `memory_operation_propose`：提交受控记忆操作候选，例如 append / skip；工具本身不写 Markdown。
- `skill_draft_propose`：提交标准 Skill 草稿候选；工具本身不注册 Skill，也不绑定 Agent。

这些工具只给系统提炼 Agent 使用，不默认暴露给普通业务 Agent。后端 `MemoryPolicyEngine` / Markdown service 负责校验、去重、脱敏和真正写入。

提炼 Agent 通过工具或 JSON 输出受控记忆操作协议，MVP 先支持：

```json
{
  "operation": "append | skip",
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
- `memory_node_query`：按某一天或日期范围查询长期节点记忆详情。
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

- `memory_nodes`：长期节点记忆检索文档。向量存储只保存这类记忆节点。
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
2. 生成最近 30 天的节点树，默认注入树结构而不是节点详情。
3. 如 Agent 调用查询工具，则在 `memory_nodes` collection 内过滤：agent、日期范围、scope、status 等标量字段。
4. Zvec 召回：全文 / BM25 / 稀疏召回；如已配置 embedding，再追加稠密向量语义召回。
5. MemoryPolicyEngine 重排：日期范围、Agent、scope 距离、置信度、最近使用。
6. token budget 裁剪。

### Memory nodes collection 文档建议

SQLite 的 `memory_nodes` 是长期节点记忆主表。向量数据库的 `memory_nodes` collection 存储可重建的节点索引副本：

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

长期节点记忆主表。一个节点通常表示某个 Agent 某一天的协作摘要；只保存用户输入、Agent 最终输出和压缩摘要，不保存工具调用过程。

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

建议路径：

- `data/memory/user/{user_id}.md`
- `data/memory/agents/{agent_id}.md`
- `data/memory/global/preferences.md`

Markdown 文件采用懒创建策略：

- Agent 创建时不创建 `data/memory/agents/{agent_id}.md`。
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
- `team_id`
- `user_id`
- `source_session_id`
- `source_run_id`
- `title`
- `reusable_reason`
- `trigger_description`
- `inputs_schema`
- `draft_skill_name`
- `draft_skill_description`
- `draft_skill_content`
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
- 是否允许 Team Worker 使用记忆。
- 自动写入最低置信度。

## 运行时接入点

### 检索与注入

建议通过 `MemoryRetrievalHook` 在 system prompt 组装阶段执行检索。工程上可以由 `runtime_builder.py` 完成 Skill / ToolScope / Team context 后挂载 hook，hook 内部调用独立的 `memory_retriever.py`。

输出 `MemoryContext`：

- `items`：本轮选中的记忆。
- `injectionText`：可直接拼入 Agent system prompt 的精简文本。
- `tracePayload`：用于 Trace 和 Runtime Event。

注入位置：

- 单 Agent：注入主 Agent system prompt 的独立 `Memory` 段落。
- Team Leader：只注入 Leader，Worker 默认不注入，除非 Team 设置允许。
- Team Worker：MVP 不注入长期记忆，避免 Worker 隔离语义复杂化；后续按 worker scope 独立开启。

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

Agent memory 默认只注入给对应 Agent；Team 场景中 Leader 和 Worker 分别只读取自己的 agent memory。

### Skill 自进化

Skill 自进化不是把经验写成一条普通记忆，而是把一次完成的协作沉淀成标准 Skill。

触发链路：

```text
Run / 工作完成
-> SkillEvolutionHook 观察完整过程
-> SkillEvolutionAgent 判断是否可复用
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

MVP 不做记忆审批流，也不做候选队列。记忆写入策略是：

- 明确、稳定、低风险的节点摘要直接写入 `memory_nodes`。
- user / agent / global 长期偏好写入 Markdown 文件，并维护压缩摘要。
- 不确定、低置信、重复、敏感或一次性的内容直接忽略，不进入队列。
- Chat 可展示轻量“已更新记忆”提示，但不弹审批。
- Memory 页面只负责查看节点树、查询节点详情、编辑 Markdown 记忆、删除、禁用和审计，不负责审批。
- 删除长期节点记忆使用软删除，保留审计事件。

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

Chat 主界面 MVP 不需要依赖完整记忆事件，只展示“已更新记忆”和本轮“使用了哪些记忆”的轻量摘要。完整审计进入 Trace / Memory 页面。

## API 草案

- `GET /api/memory/nodes/tree`
- `GET /api/memory/nodes`
- `GET /api/memory/nodes/{id}`
- `PATCH /api/memory/nodes/{id}`
- `DELETE /api/memory/nodes/{id}`
- `GET /api/memory/markdown/{scope}`
- `PUT /api/memory/markdown/{scope}`
- `GET /api/memory/settings`
- `PUT /api/memory/settings`

所有接口必须按作用域过滤，后续接 Workspace 时不能泄漏其他 Workspace 记忆。

## 前端页面草案

新增 Memory 页面，首版包含：

- 最近 30 天节点树：按 Agent / 日期展示每日记忆节点摘要。
- 节点查询：按某一天或日期范围查询节点详情。
- Markdown 记忆编辑：用户记忆、Agent 迭代记忆、全局偏好记忆。
- 记忆详情抽屉：来源 Run / Session、使用次数、最近使用、审计记录。
- Skill 自进化队列：展示自动生成的 Skill 草稿、复用理由、风险提示和注册状态。
- 设置区：启用开关、自动写入最低置信度、注入预算、Team Worker 是否使用记忆。

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

### Phase 3：反思提炼与节点持久化

- 实现 `memory_node_pipeline.py`。
- 实现 `MemoryReflectionHook`、`AgentIterationHook` 和 `MemoryNodePersistHook`。
- 实现 Markdown 记忆压缩摘要流程。
- 接入 `completion.py` 后处理或等价 Run 完成 hook。
- 确保提取失败不影响 Chat 成功收尾。

### Phase 4：Skill 自进化

- 实现 `SkillEvolutionHook`。
- 实现 `skill_evolution_evaluate`、`skill_draft_create`、`skill_register` 内部 tools。
- 将可复用工作自动生成标准 Skill，并接入现有 Skills 系统。
- 补 Skill 草稿安全校验、启用策略和回归测试。

### Phase 5：前端 Memory 页面

- 新增 Memory 页面和导航入口。
- 支持最近 30 天节点树、按日期查询、Markdown 记忆管理、Skill 自进化队列和设置。
- Chat 中展示轻量“已更新记忆”提示和本轮注入摘要。

### Phase 6：检索增强

- 引入更多 embedding provider、重建任务和混合检索重排策略。
- 支持相似记忆合并建议。
- 支持 Workspace 多租户隔离后的跨项目策略。

## 测试重点

- 记忆检索不会跨 Project / Workspace 泄漏。
- 用户本轮明确指令优先于旧记忆。
- 低置信、敏感、一次性内容不会写入长期节点或 Markdown 记忆。
- 自动写入失败不影响 Chat Run 完成。
- Team Worker 默认不注入记忆。
- 删除或禁用记忆后不会再被注入。
- RunState 刷新恢复不会重复展示“已更新记忆”提示。
- MVP 不从旧表迁移 active memory。
- 记忆 hook 失败不会影响 ReAct 主链路成功收尾。
- 节点记忆不保存工具调用过程，只保存用户输入、Agent 最终输出和压缩摘要。
- 默认上下文只注入最近 30 天节点树，节点详情必须通过工具按日期或日期范围查询。
- Skill 自进化不会把隐私、密钥或一次性项目细节写入可复用 Skill。
- 自动生成的 Skill 必须能被现有 Skills 页面和 selector 按标准 Skill 处理。
