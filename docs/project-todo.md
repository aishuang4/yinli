# Agent Workstation TODO

> 最后更新：2026-07-06
>
> 本文件只保留当前仍有效的任务。旧记忆模块、旧 Plan Runtime、旧 Subagent Runtime 已下线，相关历史 TODO 已清理。

## P0. Chat Runtime 收尾

- [ ] 让 `resume.py` 继续复用 `leader_stream.py`、`completion.py`、`orchestration.py`，减少 resume 和 send 行为分叉。
- [ ] 在 Trace 前端展示 `INTENT_ROUTE` 的 readiness、runtime、executor、Team worker 选择和 toolScope 摘要。
- [ ] 补齐 RunState / DisplaySnapshot 的回归矩阵，覆盖 Team、Plan Mode finalized、HITL、clarification、工具失败和取消。
- [ ] 检查历史消息回放路径，确保新消息优先读取 `displaySnapshot`，旧消息只按 `content` 和兼容字段降级展示。

## P1. Plan Mode 后续

- [ ] 明确 Plan Mode 执行计划后的下一轮语义：用户说“按计划执行”时是否注入 finalized plan，上下文格式和状态恢复方式需要单独设计。
- [ ] 为 `todo_write`、`task_output`、`task_list` 补更细的后端单测和前端展示验证。
- [ ] 保持 Plan Mode 只支持 `single_agent`，Team 场景继续禁止注入 Plan 控制工具。
- [ ] 如需审批流，必须重新设计生命周期、持久化、RunState 和前端入口，不复用旧 approve / deny 字段。

## P2. Team Runtime 与 HITL

- [ ] 继续验证 Worker HITL resume 与 Leader HITL resume 的状态一致性。
- [ ] 在 Team 展示中进一步区分 worker `output` 与正式 `report`，避免 Leader 上下文和前端过程日志混淆。
- [ ] 为 Worker selector 增加更多模型输出解析失败回归用例。
- [ ] 评估 Team 动态编排是否纳入 Workflow / Dynamic Workflows，而不是恢复旧 Team Plan 路线。

## P3. Trace、Artifact 与工具结果

- [ ] 大体积工具输出进入 Trace / Artifact 按需查看，Chat 主界面继续只保留轻量摘要。
- [ ] Trace 页面增加权限审计筛选、工具结果大小和截断标记展示。
- [ ] 对 Bash / Write / Edit 的文件变更记录继续补可靠性测试。
- [ ] 设计生成文件、下载产物、报告类结果的 `ARTIFACT_*` 生命周期。

## P4. 工具、MCP 与 Skills

- [ ] 完善 HTTP 工具调试体验：请求预览、响应提取规则、错误摘要和脱敏展示。
- [ ] Agent 工具配置继续统一到 Tools 入口，避免知识库工具旧概念继续扩散。
- [ ] Skills 使用过程补更多状态：等待用户、失败恢复、产物路径、引用资源。
- [ ] MCP 注册和调用继续加强 token 脱敏、超时和错误提示。

## P5. 桌面端与发布

- [ ] 验证 macOS / Windows 真机 PyInstaller 后端产物。
- [ ] 完成 Electron 正式打包：签名、公证、安装包资源过滤、日志路径和异常展示。
- [ ] 将运行时数据目录稳定迁移到 Electron `userData`。
- [ ] 评估桌面自动更新流程。

## P6. 前端组件化与 UI 规范收敛

- [x] 建立 `frontend/src/components/form/` 统一表单封装，优先组合 `@agentscope-ai/design` 的 `Input`、`Select`、`DatePicker`、`Switch`、`Slider`、`InputNumber`。
- [x] 将 Agent、Team、MCP、Skills、Tools、Settings、Sandbox、Memory 等非 Chat 业务表单逐步迁移到共享表单字段。
- [x] 新增 `FileImportButton`，用 Spark Design `Upload` 替代页面内隐藏原生 file input。
- [x] 新增 `CatalogFilterBar`，收敛 MCP / Skills 目录页搜索和筛选控件。
- [x] 拆分 `AgentEditor`：基础信息、模型高级设置、能力工具、Guardrails、MCP / HTTP / Skill 选择弹窗拆到 `features/agents/sections/`。
- [x] 拆分 `MemoryPage`：页面只保留数据装配和 tab 编排，Markdown、历史节点、Skill 自进化队列、Wiki Vault 面板拆到 `features/memory/`。
- [x] 拆分 `TracePage`：页面只保留运行轨迹数据加载和分页，列表、详情、事件时间线、消息卡、工具列表拆到 `features/traces/`。
- [ ] 继续拆 `AgentTeamPage`：侧栏导入导出、Team 模板区、Team 列表项和空状态可继续下沉到 `features/teams/` 或 `features/agents/`。
- [ ] 继续拆 `RunsPage`：运行列表、状态筛选、详情操作和空状态应组件化。
- [ ] 抽取通用非表单交互组件：卡片式 tab、可选择列表项、时间线节点、工具条按钮组，减少页面内重复 `<button>` className。
- [ ] 给非 Chat 前端整改增加轻量自动检查脚本，固定扫描裸表单控件和超大 `.tsx` 文件。
- [ ] Chat 页面和 `features/chat/` 单独排期整改；当前非 Chat 整改不顺手修改 Chat 行为。

## P7. 记忆系统重设计

- [x] 将 `docs/memory-system-design.md` 更新到当前 MVP 进度，记录已实现能力、未完成闭环和 Skill 自进化约束。
- [ ] 评审并冻结 `docs/memory-system-design.md`，重点确认历史记忆、Markdown 记忆、hooks/tools 接入、运行前注入、运行后自动写入、Skill 自进化和冲突优先级。
- [ ] 正式定义 `MEMORY_*` Runtime Event，并更新 `docs/chat-sse-event-contract.md`、后端 schema、前端类型和 projector。
- [x] 新增历史记忆底层 `memory_nodes` 数据模型、SQLite schema、Pydantic schema 和 `/api/memory/nodes*` 路由。
- [x] 新增 Agent Markdown 记忆懒创建存储与 `/api/memory/markdown/agent/{agent_id}` 读写接口。
- [ ] 新增 User / Global Markdown 记忆文件存储、使用审计、Skill 自进化记录和 Memory 设置数据模型。
  - [x] User / Global Markdown 记忆文件存储与懒创建 API。
  - [x] Skill 自进化候选记录首版。
  - [ ] 使用审计视图和 Memory 设置数据模型。
- [x] 新增全局 `VectorStoreBackend` 抽象、provider registry、`VectorStorePolicy` 持久化和 `disabled` 降级 backend。
- [x] `VECTOR_STORE_PROVIDER` 只在首次初始化时生效；初始化后持久化 `VectorStorePolicy`，后续 provider、collection schema、embedding 维度只能通过显式迁移 / rebuild 改变。
- [x] 向量检索分级策略：Level 0 禁用、Level 1 Zvec + BM25 / 稀疏检索、Level 2 embedding + 稠密向量混合召回。
- [x] 完成 Zvec provider 适配层：collection 创建/打开、`Doc` 写入、删除、向量/FTS 查询和结果映射。
- [x] 安装 `zvec` 真实依赖并补充端到端集成测试，验证本机 collection 落盘、FTS/BM25 和 dense vector 查询。
- [ ] 记忆模块必须继续通过 hooks / tools / 后处理服务接入，不新增 `memory` Runtime 分支。
- [x] 新增 `memory_node_tree` / `memory_node_query` Agent 工具，默认注入普通 Agent，基础设施 Agent 可排除。
- [x] 记忆采用双轨存储：向量库只保存历史记忆底层 `memory_nodes`；用户记忆、Agent 迭代记忆和全局偏好记忆以 Markdown 文件保存并压缩摘要。
- [x] 实现 `MemoryRetrievalHook` 首版：模型运行前注入 Agent Markdown 摘要、最近 30 天历史记忆索引和 memory 查询工具提示。
- [ ] 扩展 `MemoryRetrievalHook`：加入 user / global Markdown 摘要、Team Worker 策略和 Trace payload。
  - [x] 加入 user / global Markdown 摘要。
  - [ ] Team Worker 策略和 Trace payload。
- [ ] 实现 `MemoryReflectionHook`：Agent 完成运行后判断是否提炼 user memory、agent memory，以及是否生成标准 Skill。
  - [x] 规则型首版：成功运行后提炼明确长期意图，写入 user / agent / global Markdown。
  - [x] 新增并行 `UserMemoryReflectionAgent` / `AgentMemoryReflectionAgent` / `GlobalMemoryReflectionAgent` 与 `SkillEvolutionAgent`，并配置文件绑定的 `memory_read`、`memory_add`、`memory_update`、`memory_delete` 与 `skill_draft_propose` 内部工具。
  - [x] 实现规则版 `SkillEvolutionRouterGate`，先判断本轮是否值得进入 Skill 自进化链路。
  - [x] 实现 `WorkflowEvidenceBuilder` 首版，汇总成功工具链、有价值失败、变量流、改动文件和验证结果摘要。
  - [x] 强化 Skill 颗粒度控制：只保留原子 Skill 候选，过大流程只允许拆分建议并交给 Workflow 编排器。
  - [x] 强化上下文依赖抽象：工具返回值必须作为变量传递，禁止硬编码本轮具体 ID、路径、URL 或返回值。
  - [x] 持久化 Skill 自进化候选记录，保存 gate reason、workflow evidence、granularity、suggested split、variable flow 和 risk notes。
  - [x] 实现标准 Skill 注册首版：仅将 `draft + atomic + 高置信度 + 安全校验通过 + 未发现相似已安装 Skill` 的候选注册到现有 Skills 系统，且不自动绑定 Agent。
  - [x] 自动生成 / 手动注册 Skill 时写入标准包目录 `backend/data/skill_packages/{skill_name}/SKILL.md`，并支持从 Skills 页面按 Skill 名称导出 zip。
  - [x] 实现 Trace 审计首版：历史记忆持久化和后台反思只写入 `run_events` 的独立 `MEMORY_AUDIT`，不驱动 Chat 页面过程展示。
  - [x] 实现 Chat 轻量 Skill 生成中提醒首版：只通过 `RUNTIME_EVENT.type=MEMORY_SKILL_GENERATING` 触发 `RUN_STATE_PATCH.kind=notice`，文案为 “Skills 生成中”，不展示 Skill 内容、结果或记忆过程。
  - [x] 增加用户显式请求生成 Skill 的手动触发流：用户说“把这个生成 Skill”时读取上一轮完成的 user / assistant 对话，复用 SkillEvolutionAgent 和标准注册链路。
  - [x] 实现相似 Skill 合并首版：注册前综合名称、描述、触发条件、标签、正文步骤和关键概念 token 判断相似能力，命中时关联已有 Skill，不创建重复 Skill。
  - [ ] 敏感信息过滤增强、候选队列管理和注册失败原因可视化。
- [x] 实现 `MemoryNodePersistHook` 首版：Agent 成功运行完后只用用户输入和 Agent 最终输出写入当天节点并同步向量库。
- [ ] 扩展 `MemoryNodePersistHook`：接入反思模型摘要、去重、敏感信息过滤、Trace 事件和异步失败重试。
- [ ] 新增 Memory 页面，支持历史记忆、三类 Markdown 记忆、Skill 自进化队列、设置和审计查看。
  - [x] Memory 页面首版：导航入口、唯一用户记忆、唯一全局记忆、每个 Agent 一份 Agent Markdown 记忆、历史记忆按“Agent -> 时间节点 -> 具体记忆”查看。
  - [x] Skill 自进化队列查看：展示候选状态、草稿 Skill、复用理由、router gate、workflow evidence、风险提示和注册状态。
  - [ ] 设置区、审计详情、历史记忆删除 / 禁用、Skill 队列操作和注册失败原因可视化。
- [ ] 融合 Obsidian-compatible Memory Vault 方向：先做可读 Wiki 表现层，再逐步补实体索引、记忆准入门和差异追踪。
  - [x] 在 `docs/memory-system-design.md` 记录 Vault 边界：不替代 SQLite / Markdown / VectorStoreBackend 权威存储。
  - [x] 新增 `backend/data/memory/wiki/` 目录生成器和基础同步器，支持手动生成只读 wiki 视图。
  - [x] Memory 页面增加 Wiki 同步入口和同步结果查看。
  - [x] Memory 页面增加打开 wiki 文件夹 / 打开 Obsidian vault 入口。
  - [ ] 设计 Memory Wiki 自动同步策略和用户在 wiki 中手动编辑后的反向合并边界。
  - [x] Wiki 同步器维护轻量实体索引，生成 `entities/{kind}/{name}.md`、Obsidian 双链和 `entities/graph.md`。
  - [x] 记忆提炼 Agent 输出时主动使用 `[[类型:名称]]` 链接。
  - [x] 新增轻量规则版 `MemoryAdmissionGate`，控制历史记忆、长期 Markdown 和 Skill 自进化的准入。
  - [ ] 增强 `MemoryAdmissionGate`：接入模型评分、配置化阈值、Trace payload 和更完整的脱敏策略。
  - [ ] 新增轻量 `memory_change_log`，后续再评估 Git-backed vault diff。
- [x] 明确旧 `memory_facts` / `memory_events` / `memory_candidates` 表的迁移或废弃策略：当前代码不再创建/读写旧表，已有本地库不自动 drop。
- [ ] 新方案必须先更新架构文档和 Chat 事件契约，再接入 Runtime。

## P8. 长期方向

- [ ] Dynamic Workflows 概念评估，参考 `docs/dynamic-workflows-plan.md`。
- [ ] Milvus 或其他向量数据库集成评估。
- [ ] Workspace 多租户隔离：Agent、Team、Run、Tool、Skill、Memory 归属同一 Workspace。
- [ ] Agent / Team 模板市场。
- [ ] 多语言 UI。

## 文档维护规则

- 新增 Chat SSE、RunState、DisplaySnapshot 字段前先更新 `docs/chat-sse-event-contract.md`。
- 新增大功能前写短设计；实现完成后合并结论到 `docs/project-todo.md` 或 `docs/project-progress-handoff.md`。
- 临时执行计划完成后删除，不长期留在 `docs/`。
- 已下线能力只能以“历史兼容字段”描述，不写成当前能力。
