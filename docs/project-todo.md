# Agent Workstation TODO

> 最后更新：2026-07-03
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

## P6. 记忆系统重设计

- [ ] 评审并冻结 `docs/memory-system-design.md`，重点确认四层记忆、hooks/tools 接入、运行前注入、运行后自动写入、Skill 自进化和冲突优先级。
- [ ] 正式定义 `MEMORY_*` Runtime Event，并更新 `docs/chat-sse-event-contract.md`、后端 schema、前端类型和 projector。
- [x] 新增长期节点记忆 `memory_nodes` 数据模型、SQLite schema、Pydantic schema 和 `/api/memory/nodes*` 路由。
- [x] 新增 Agent Markdown 记忆懒创建存储与 `/api/memory/markdown/agent/{agent_id}` 读写接口。
- [ ] 新增 User / Global Markdown 记忆文件存储、使用审计、Skill 自进化记录和 Memory 设置数据模型。
  - [x] User / Global Markdown 记忆文件存储与懒创建 API。
  - [ ] 使用审计、Skill 自进化记录和 Memory 设置数据模型。
- [x] 新增全局 `VectorStoreBackend` 抽象、provider registry、`VectorStorePolicy` 持久化和 `disabled` 降级 backend。
- [x] `VECTOR_STORE_PROVIDER` 只在首次初始化时生效；初始化后持久化 `VectorStorePolicy`，后续 provider、collection schema、embedding 维度只能通过显式迁移 / rebuild 改变。
- [x] 向量检索分级策略：Level 0 禁用、Level 1 Zvec + BM25 / 稀疏检索、Level 2 embedding + 稠密向量混合召回。
- [x] 完成 Zvec provider 适配层：collection 创建/打开、`Doc` 写入、删除、向量/FTS 查询和结果映射。
- [x] 安装 `zvec` 真实依赖并补充端到端集成测试，验证本机 collection 落盘、FTS/BM25 和 dense vector 查询。
- [ ] 记忆模块必须通过 hooks 和 tools 接入，不新增 `memory` Runtime 分支。
- [x] 新增 `memory_node_tree` / `memory_node_query` Agent 工具，默认注入普通 Agent，基础设施 Agent 可排除。
- [x] 记忆采用双轨存储：向量库只保存长期节点记忆 `memory_nodes`；用户记忆、Agent 迭代记忆和全局偏好记忆以 Markdown 文件保存并压缩摘要。
- [x] 实现 `MemoryRetrievalHook` 首版：模型运行前注入 Agent Markdown 摘要、最近 30 天节点树和 memory 查询工具提示。
- [ ] 扩展 `MemoryRetrievalHook`：加入 user / global Markdown 摘要、Team Worker 策略和 Trace payload。
  - [x] 加入 user / global Markdown 摘要。
  - [ ] Team Worker 策略和 Trace payload。
- [ ] 实现 `MemoryReflectionHook`：Agent 完成运行后判断是否提炼 user memory、agent memory，以及是否生成标准 Skill。
  - [x] 规则型首版：成功运行后提炼明确长期意图，写入 user / agent / global Markdown。
  - [ ] 模型提炼、去重增强、敏感信息过滤增强、Trace 审计和标准 Skill 生成。
- [x] 实现 `MemoryNodePersistHook` 首版：Agent 成功运行完后只用用户输入和 Agent 最终输出写入当天节点并同步向量库。
- [ ] 扩展 `MemoryNodePersistHook`：接入反思模型摘要、去重、敏感信息过滤、Trace 事件和异步失败重试。
- [ ] 新增 Memory 页面，支持四层记忆管理、Skill 自进化队列、设置和审计查看。
- [x] 明确旧 `memory_facts` / `memory_events` / `memory_candidates` 表的迁移或废弃策略：当前代码不再创建/读写旧表，已有本地库不自动 drop。
- [ ] 新方案必须先更新架构文档和 Chat 事件契约，再接入 Runtime。

## P7. 长期方向

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
