# 引力 YINLI TODO

> 最后更新：2026-09-14。本文只保留尚未完成的任务。

## P0. Runtime 稳定性

- [ ] 在 Docker daemon 可用的环境完成 `DockerWorkspace` 真实 Runtime 门禁（多供应商切换已于 2026-09-14 桌面打包版验证通过，见 handoff），结果写回 `docs/project-progress-handoff.md`。
- [ ] 补齐 RuntimeFrame / DisplaySnapshot V4 浏览器回归矩阵，覆盖 Plan finalized、HITL、AskUser、工具失败、取消和刷新恢复。
- [ ] 增加关键 Chat 工作流的 Playwright 测试，避免只依赖 reducer 和后端单测。
- [ ] 在 Trace 前端展示 `INTENT_ROUTE` 的 readiness、runtime、executor 和 ToolScope 摘要，以及 `MODEL_CALL_START.contextStats`（2026-09-14 后端已写入，前端未展示）。
- [ ] 用真实供应商验证 2026-09-14 的 ReAct 上下文改造：多轮同会话 token 曲线应不再随轮次线性增长；`FinalAnswerStart` 放宽后无工具回答的乐观流式阈值（600 字）是否合理；实例搬运后 AskUser 未回应场景无 `tool_call_id` 不匹配报错。
- [ ] Agent 缓存的 LRU 上限 / 闲置时长目前是常量（48 / 6h），后续如需按部署规模调节再进系统设置。
- [ ] `SkillContextMiddleware` 目前只挂在单 Agent；Team Leader / Member（`team_runtime/member_runtime.py`）与群聊工作室 Worker 仍是"读完即扔"，需要时按同一 middleware 接入。Skill 正文补回目前不发 SSE 事件，前端看不到"哪些 Skill 仍在生效"。

## P1. 群聊工作室后续

Intake 对谈、FIFO 点名队列、`group_work_items` 认领已在线。下面只保留未做项。

- [ ] 把 `MainAgentRuntimeSpec` / cache key 从 `routes/chat/runtime_builder.py` 下沉到 `agents/` 或 `runtime/`，Chat 与 group_studio 平级调用。
- [ ] 真并行 Worker（`maxParallelWorkers>1`）与 `parent_run_id` / child runs。
- [ ] 时间旅行快照表不要叫 `group_snapshots`，以免和 `GROUP_STUDIO_STATE` 口语撞车。
- [ ] 群事件进入 Trace 页前给 `run_events` 增加 `source` 默认 `chat`，且 `session_id` 必须是真实 Chat session。
- [ ] `max_cost_usd` 熔断；P0 只记 token，不按金额熔断。
- [ ] 归档后 MemoryAdmissionGate 只用最终 Goal、黑板决策和人类可见结论，禁止用 `group-studio-host` 写 agent memory。
- [ ] 工作室列表入口与跨群切换（P0 一 Chat session 一间未归档工作室，不提供群目录）。

## P1. Event Stream 后续加固

- [ ] 在已有 `vitest` 基础上增加前端 RuntimeFrame decoder、gap recovery 和 kind reducer 的独立单元测试，减少只靠 production build 与浏览器回归发现问题的风险。当前只有 `echartsMarkdown.test.ts` 一个前端单测。
- [ ] 为 ProjectionRunner backlog、Snapshot fallback、retention 和 invariant repair 增加可观测指标。

## P2. TeamRun 与 Subagent

- [ ] 按 `docs/team-member-lifecycle-v2.md` 分阶段实现长生命周期 Member、TaskAttempt、Mailbox 唤醒和 Reviewer 返工闭环；实施前先冻结 Chat SSE 契约。
- [ ] 增加 Team Mailbox API 与综合阶段并发投递的端到端测试。
- [ ] 增加 Team Console Playwright 回归，覆盖第二轮清空、成员切换不重放和用户主动发信。
- [ ] 在 Task API 基础上接入 Subagent 控制台刷新恢复回归；Chat 使用嵌套 `kind=subagent` Block，detached background 暂不开放。

## P3. 平台基础

### Workspace / Project

- [ ] 在现有数据、文件路径、Memory 和资源绑定隔离测试基础上，补浏览器级跨 Project 隔离测试。
- [ ] 为资源创建与 Project binding 增加后端事务接口，避免绑定失败时留下未授权资源。
- [ ] 评估并收紧资源详情、更新和删除 API 的 Workspace ownership 校验。

### Artifact

- [ ] 定义 Artifact 数据模型和 `ARTIFACT_*` 生命周期。
- [ ] 支持文件、报告、代码补丁和数据集的来源、预览、下载和版本。
- [ ] 大体积工具输出进入 Trace / Artifact 按需查看，Block 只保留轻量摘要。

## P4. Plan、Trace 与工具

- [ ] Trace 增加权限审计筛选、工具结果大小和截断标记。
- [ ] 对 Bash / Write / Edit 的文件变更记录补可靠性测试。
- [ ] 完善 HTTP 工具请求预览、响应提取、错误摘要和脱敏展示。
- [ ] 在 WebFetch 稳定后增加 WebSearch provider 抽象，首批支持 SearXNG 和一个正式云端供应商，并建立来源引用契约。
- [ ] MCP 调用加强 Secret 脱敏、超时和错误提示。
- [ ] Skills 补等待用户、失败恢复、产物路径和引用资源状态。
- [ ] 用至少两个模型供应商完成 AgentScope 原生 Skills Sandbox 行为验证，覆盖 `scripts/`、`references/`、Office 文件和 fallback 模型。
- [ ] 补 Excel / CSV（`openpyxl`）与 PDF 读取的系统 Skill 包，与 `word-documents` 同样放在 `artifacts/skills/` 并加入 `SYSTEM_SKILL_IDS`；当前 Office 能力只有 Word。
- [ ] 为非持久 DockerWorkspace 设计一次性 Skill seed 与运行结束清理，解除当前“Docker + Skills 必须启用 persistent_sandbox”的限制。

## P5. Memory 稳定化

- [ ] 补运行前记忆注入 Trace payload，记录目标、来源、条数和截断信息。
- [ ] 补 VectorStorePolicy、准入阈值、Markdown 质量策略的迁移、重建和风险提示。
- [ ] 建立 Memory 回归集和手动冒烟流程，确认主干稳定后再扩展召回实验。

暂缓：复杂召回质量视图、模型评分评估、`memory_change_log`、Git-backed vault、Wiki 反向合并和专业图谱组件。

## P1. 系统定义文件化

规格见 `docs/file-catalog-v1.md`。第一期（持久 Agent + INDEX）在本轮实施。

- [x] 第二期：Skill 去掉库内正文双写；项目 `.agents/skills` 默认可运行；seed `word-documents` / `yinli-charts`（本轮实施）。
- [x] 第三期：MCP / HTTP 定义文件化，密钥走 secret_store（本轮实施）。
- [x] 第四期：Job 定义收到同一棵树，撤回模型侧 `ScheduleJob` 平行入口（本轮实施）。

## P6. 桌面端与前端

- [ ] 验证 macOS / Windows 真机 PyInstaller 后端产物。
- [ ] 完成 Electron 签名、公证、安装包过滤、日志和异常展示。
- [ ] 将运行时数据稳定迁移到 Electron `userData`，并设计备份与恢复。
- [ ] 评估桌面自动更新。
- [ ] 拆分 RunsPage 和 ChatPage 的高复杂度区域。
- [ ] 抽取卡片式 tab、可选择列表项、时间线节点和工具条按钮组。
- [ ] 增加非 Chat 表单与超大组件的轻量自动检查。
- [ ] 在仓库打 `v0.1.0-beta.1` tag 并按 `docs/beta-release-checklist.md` 在 macOS arm64 / Windows 真机逐项验收；当前仓库没有任何 tag。

## P7. 长期路线

- [ ] Dynamic Workflow Phase 0：从固定节点演示改为 Script Review + Runtime Cockpit。
- [ ] 定义 `workflow.script.v1`，再实现 stage / task / variable / artifact 动态事件。
- [ ] 接入 AgentScope Bridge，复用 Agent、TeamRun、Skill、权限、Memory 和 HITL。
- [ ] 评估 LangGraph / LangChain 执行引擎，SQLite 继续作为产品事实源。
- [ ] 建立 Agent / Prompt / Model / TeamRun 的评估和成本体系。
- [ ] 增加 Agent / Subagent 配置版本、Diff、发布和回滚。
- [ ] 在现有 `secret_store` 上补引用面、轮换和桌面端凭据迁移。
- [ ] 定时任务预览发送、OS 唤醒与失败重试；第一批 weekly/once 到点跑并通知已在线。
- [ ] 评估自动化触发、任务队列、预算和后台通知扩展。
- [ ] 在现有 `NotificationChannel` 上接入邮箱、微信、飞书投递；密钥走 secret_store，启用前补投递失败重试与脱敏。
- [ ] Agent / Subagent preset 市场、多语言 UI 和后续多用户 RBAC。

## 维护规则

- 新增 Chat RuntimeFrame、DisplaySnapshot 或 kind 语义前先更新 `docs/chat-sse-event-contract.md`。
- 新增大功能前写短设计；实现完成后合并事实并删除阶段草案。
- 已下线能力只能以历史清理边界描述，不能重新写成当前入口。
