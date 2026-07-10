# Agent Workstation TODO

> 最后更新：2026-07-10
>
> 本文件只保留当前仍有效的任务。已完成事项归档到 `docs/project-progress-handoff.md` 和 `docs/architecture-overview.md`，旧记忆模块、旧 Plan Runtime、旧 Subagent Runtime 已下线，相关历史 TODO 已清理。

## P0. Chat Runtime 收尾

- [ ] 让 `resume.py` 继续复用 `leader_stream.py`、`completion.py`、`orchestration.py`，减少 resume 和 send 行为分叉。
- [ ] 在 Trace 前端展示 `INTENT_ROUTE` 的 readiness、runtime、executor 和 toolScope 摘要。
- [ ] 补齐 RunState / DisplaySnapshot 与独立 TeamSnapshot 的回归矩阵，覆盖 Plan Mode finalized、HITL、clarification、工具失败和取消。
- [ ] 检查历史消息回放路径，确保新消息优先读取 `displaySnapshot`，旧消息只按 `content` 和兼容字段降级展示。

## P1. Plan Mode 后续

- [ ] 明确 Plan Mode 执行计划后的下一轮语义：用户说“按计划执行”时是否注入 finalized plan，上下文格式和状态恢复方式需要单独设计。
- [ ] 为 `todo_write`、`task_output`、`task_list` 补更细的后端单测和前端展示验证。
- [ ] 保持 Plan Mode 只支持 `single_agent`，Team 场景继续禁止注入 Plan 控制工具。
- [ ] 如需审批流，必须重新设计生命周期、持久化、RunState 和前端入口，不复用旧 approve / deny 字段。

## P2. Team Runtime

- [ ] 增加 Team Mailbox API 与综合阶段并发投递的端到端测试。
- [ ] 增加 Team Console 的 Playwright 回归，覆盖第二轮清空、成员切换不重放和用户主动发信。
- [ ] 评估 Team 动态编排是否纳入 Workflow / Dynamic Workflows，但不得恢复旧 Team Plan 路线。

## P2.5. Subagent 委托能力

- [ ] M2 继续借鉴官方补动作型 Subagent 的 HITL resume、inline MCP 和 hooks，但最终行为落到 AW PermissionPolicy、Trace 和 RunState 契约。
- [ ] M4 补 `isolation: worktree`、嵌套深度限制和 fork；M5 补托管 / 会话 / Plugin scope、frontmatter `memory`、`effort`、`initialPrompt` 和显式 @-mention 调用。

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

- [ ] 拆分 `AgentTeamPage` 的 Agent 管理部分，并评估删除与一次性 TeamRun 产品语义冲突的持久 Team / template 管理入口。
- [ ] 继续拆 `RunsPage`：运行列表、状态筛选、详情操作和空状态应组件化。
- [ ] 抽取通用非表单交互组件：卡片式 tab、可选择列表项、时间线节点、工具条按钮组，减少页面内重复 `<button>` className。
- [ ] 给非 Chat 前端整改增加轻量自动检查脚本，固定扫描裸表单控件和超大 `.tsx` 文件。
- [ ] Chat 页面和 `features/chat/` 单独排期整改；当前非 Chat 整改不顺手修改 Chat 行为。

## P7. 记忆系统重设计

近期目标是固化主干、减少分支扩张，先形成一个可回归、可提交、可继续演进的稳定版本。

- [ ] 补运行前记忆注入 Trace payload：记录注入阶段、目标 Agent、摘要条数、历史索引条数、来源和截断信息，只进入 Trace / Memory 审计，不展示到 Chat 主界面。
- [ ] 补 Memory 设置策略迁移、重建流程和风险提示：覆盖 `VectorStorePolicy`、准入阈值、Markdown 质量策略和向量索引 rebuild。
- [ ] 跑 Memory 后端回归、前端 build 和一次手动冒烟测试，确认当前主干可用后提交稳定版本。

暂停分支，不作为近期 TODO 推进：

- 复杂召回质量视图、反馈权重配置、空结果 / 低分结果诊断和召回样本回放。
- `MemoryAdmissionScoringAgent` 效果评估、模型评分回归集和脱敏策略分级展示。
- `MemoryNodePersistHook` 的反思模型摘要、异步失败重试和可重放错误记录增强。
- 轻量 `memory_change_log` 与 Git-backed vault diff。
- Wiki 图谱成熟化、实体类型过滤、关系类型、专业图谱组件和反向合并边界。

## P8. 长期方向

- [ ] Dynamic Workflow Phase 0：按 `docs/dynamic-workflows-plan.md` 修正当前 MVP 方向，停止固定四节点动画，改为 Script Review + Runtime Cockpit 壳。
- [ ] Dynamic Workflow Phase 1：定义 `workflow.script.v1` schema，后端先用规则模板生成 script 草稿，前端展示阶段、并发、预算、风险、权限和原始 JSON。
- [ ] Dynamic Workflow Phase 2：Runtime 按 script 动态生成 stage / task / variable / artifact 事件，支持 fan-out、barrier、aggregate。
- [ ] Dynamic Workflow Phase 3：接入 AgentScope Bridge，让 `workflow_agent_tasks` 调用现有 Agent / Team / Skill，并继承权限、ToolScope、Memory 和 HITL。
- [ ] Dynamic Workflow Phase 4：评估 LangGraph / LangChain 作为执行引擎，SQLite 继续作为产品事实来源。
- [ ] Milvus 或其他向量数据库集成评估。
- [ ] Workspace 多租户隔离：Agent、Team、Run、Tool、Skill、Memory 归属同一 Workspace。
- [ ] Agent / Subagent preset 市场。
- [ ] 多语言 UI。

## 文档维护规则

- 新增 Chat SSE、RunState、DisplaySnapshot 字段前先更新 `docs/chat-sse-event-contract.md`。
- 新增大功能前写短设计；实现完成后合并结论到 `docs/project-todo.md` 或 `docs/project-progress-handoff.md`。
- 临时执行计划完成后删除，不长期留在 `docs/`。
- 已下线能力只能以“历史兼容字段”描述，不写成当前能力。
