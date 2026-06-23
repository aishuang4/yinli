# Agent Workstation TODO

> Last updated: 2026-06-23
>
> This file is the working TODO for the project. Use it as the source of truth
> for upcoming implementation order. When a feature is completed, update the
> checkbox and add a short note if behavior changed.

## Current Focus

- [x] Stabilize runtime observability and testability.
- [x] Keep Agent, Team, Tool, Skill, Memory, and Settings data portable.
- [x] Improve high-frequency Chat and Team workflows before adding marketplace-style features.
- [x] 拆分前置意图路由，降低主 Agent 的规划、Skill、Subagent、Team 选择压力。
  - 实现于 2026-06-23：新增 `docs/pre-agent-intent-router-design.md`、`intent_router.py`、`runtime_builder.py`、`orchestration.py`、`leader_stream.py`、`completion.py`，将 Plan/Skill/Subagent/Team/ToolScope/Clarification 前置路由和 runtime 组装从 `send.py` 拆出。
  - 当前 `IntentRoute` 会写入 `INTENT_ROUTE` trace；Team worker 选择为规则优先、小模型兜底；主 Agent 工具注入受 `RuntimeToolScope` 裁剪。
- [ ] 收尾 Chat runtime 拆分的剩余边界。
  - [x] 拆出 Plan approval 流程，减少 `send.py` 中审批等待、拒绝计划和错误写入逻辑。实现于 2026-06-23：已下沉到 `completion.handle_plan_approval_if_required()`。
  - [x] 抽统一早期错误处理 helper，覆盖图片模型不支持、API Key 缺失、Agent 创建失败等重复路径。实现于 2026-06-23：已下沉到 `completion.emit_failed_startup_run()`。
  - [ ] 低优先级：让 `resume.py` 复用 `leader_stream.py`、`completion.py`、`orchestration.py`，避免 resume 和 send 再次分叉。
    - 当前不影响主流程；除非后续 HITL resume 与正常 send 出现明显行为分叉，否则先不作为近期主线。
  - [ ] 在 Trace 前端展示 `INTENT_ROUTE` 的 mode、skills、subagents、team workers 和 toolScope。

## P0. Finish Current Trace And Sandbox Work

- [x] Runtime trace event table
  - Add a normalized `run_events` SQLite table.
  - Persist model events, tool events, Team worker state, HITL events, errors, and token/cache usage.
  - Keep the current trace-from-history API as fallback during development.
  - Add trace detail filtering by Agent, Team, Worker, tool, and status.
  - Implemented 2026-06-14: added `run_events`, trace event persistence, trace API `events`, and frontend timeline. Filtering remains a follow-up.

- [x] Test sandbox saved cases
  - Save sandbox inputs as reusable test cases.
  - Support single Agent and Team test cases.
  - Allow re-running a saved case after Agent/Team config changes.
  - Show pass/fail notes manually first; automate assertions later.
  - Implemented 2026-06-14: added `/api/sandbox/cases` CRUD and Sandbox page save/load/delete controls. Automated assertions remain a follow-up.

- [x] Tool-call display polish
  - Keep repeated tool calls visible as separate calls.
  - Normalize duplicated display names such as `A_A`.
  - Keep tool-call panels height-limited with internal scroll.
  - Add copy buttons for tool input and output.
  - Highlight failed tool calls.
  - Implemented 2026-06-14: repeated calls remain visible, duplicated names are normalized, panels are height-limited, copy actions were added, and failed status is highlighted.

## P1. Portability And Configuration

- [x] Electron 开发期桌面窗口支持
  - 增加专用开发命令，在后端和 Vite 前端启动后拉起 Electron 桌面壳。
  - 将 `electron/main.ts` 和 `electron/preload.ts` 编译到 `electron/dist`，并纳入开发启动链路。
  - 确保 Electron 窗口加载 Vite 开发服务，使用自定义 `TitleBar`，并保留 DevTools 方便桌面端调试。
  - 按需接入 `window.electronAPI`，让桌面模式可以读取后端地址、应用版本和平台信息，减少硬编码假设。
  - 当前范围只做开发期桌面窗口支持。生产打包、内置 Python 运行时、安装器、签名和自动更新等能力，等核心开发稳定后再处理。
  - 实现于 2026-06-17：新增 `npm run electron:dev` 和 `npm run electron:compile`，开发脚本会复用或启动后端/Vite 服务，编译 Electron 主进程后拉起桌面窗口；前端启动时会读取 Electron preload 暴露的后端地址和版本号。

- [x] Agent import/export
  - Export Agent config as JSON.
  - Include model config, plan config, guardrails, MCP tools, knowledge tools, Skills, and memory injection settings.
  - Import with conflict handling for duplicate IDs and missing tools/skills.
  - Implemented 2026-06-14: added `/api/agents/{id}/export`, `/api/agents/import`, and Agent page import/export buttons. Imports create a new ID by default and suffix duplicate names.

- [x] Team import/export
  - Export Team config with leader, members, dependencies, execution modes, handoff prompts, and worker selection policy.
  - Import with validation against existing Agents.
  - Offer automatic remapping when Agent IDs differ.
  - Implemented 2026-06-14: added `/api/teams/{id}/export`, `/api/teams/import`, and Team page import/export buttons. Imports create a new Team ID, rebuild member IDs, and warn when referenced Agents are missing.

- [x] Agent Team preset templates
  - Built-in templates:
    - Research Team
    - Development Review Team
    - Knowledge Base QA Team
    - Business Data Analysis Team
  - Allow creating a Team from a template.
  - Allow saving a user-created Team as a template.
  - Implemented 2026-06-15: added built-in Team templates, custom templates saved from existing Teams, `/api/teams/templates` APIs, and Teams page template creation controls.

- [x] Model parameter tuning
  - Add per-Agent settings for temperature, top_p, max_tokens, thinking mode, cache policy, and tool choice.
  - Keep provider selection in system settings; Agent should only select configured models.
  - Show effective runtime model config in Agent detail and runtime trace.
  - Implemented 2026-06-15: Agent model config now supports temperature, top_p, max_tokens, primary model, and fallback chain in runtime. Thinking mode is controlled from Chat today; cache policy and tool choice remain follow-up items.

## P2. Memory System Improvements

- [x] Memory panel editing
  - Edit accepted memories.
  - Delete accepted memories.
  - Pin important memories.
  - Mark outdated memories.
  - Implemented 2026-06-15: accepted facts now support edit, delete, pin/unpin, and active/outdated status. Outdated facts stay visible for review but are excluded from runtime memory injection.

- [x] Memory candidate review
  - Merge duplicate candidates.
  - Reject with reason.
  - Show source conversation/run for each candidate.
  - Add confidence and category display.
  - Implemented 2026-06-15: pending candidates now persist category, confidence, source session, and reject reason. The memory panel can merge exact duplicates and reject candidates with a reason.

- [x] Memory cleanup
  - Detect stale or contradictory memories.
  - Suggest memory consolidation after repeated similar facts.
  - Keep automatic writes behind user approval unless explicitly configured.
  - Implemented 2026-06-15: memory panel now shows cleanup suggestions for exact duplicates, same-title conflicts, and long-unupdated facts. Applying a suggestion only marks facts outdated after user confirmation.

## P3. Team Runtime And HITL

- [x] Chat 后台 Run 生命周期第一版
  - 参考 `docs/background-run-lifecycle-plan.md`，把 Agent/Team 执行从前端 SSE 订阅生命周期中解耦。
  - `/api/chat/send` 创建进程内后台 run，SSE 连接只订阅事件；前端断开订阅不会直接取消后台执行。
  - 新增 `/api/chat/runs/{run_id}/stream`，支持在同一后端进程内按 run 重新订阅事件。
  - 实现于 2026-06-18：新增进程内 `background_runs` 管理器。后续还需补 run 状态持久化、前端自动重连、增量草稿消息和真正取消 run 的接口。
  - 更新于 2026-06-20：Run 记录与原始 SSE 事件已落 SQLite（`chat_runs` / `chat_run_events`），HITL resume 会写回原 Run；运行任务中心改为看板式展示进度、当前阶段、参与者和完整结果。

- [x] Worker execution graph
  - Make mixed parallel/serial execution easier to inspect.
  - Show dependency wait reasons.
  - Confirm leader synthesis uses completed worker outputs.
  - Implemented 2026-06-15: Team activity events now include execution mode, wait reason, upstream output count, and leader worker-output context count. Chat renders a compact execution graph above Team cards and keeps details in the side panel.

- [x] Worker selection policy
  - Keep AI auto-selection silent by default.
  - Put human confirmation only in the chat input dock.
  - Do not keep selected worker confirmation cards after approval.
  - Implemented 2026-06-15: Team worker preselection stays in the input dock, defaults to a compact confirmation bar, hides selected Worker names unless expanded, and clears immediately after confirmation/cancel.

- [x] HITL consistency
  - Support leader and worker HITL with the same UX pattern.
  - Keep pending approvals visible near the input box.
  - Persist HITL events into `run_events`.
  - Implemented 2026-06-15: Leader and Worker approvals share the input dock UX. HITL_REQUIRED is now recorded from the centralized pending-interaction creator, and HITL_RESOLVED is recorded when the user approves or rejects.

## P4. Safety And Execution Isolation

- [x] Docker sandbox
  - Run file/script tools in isolated workspace containers.
  - Provide per-Agent or per-Team sandbox policy.
  - Capture stdout, stderr, file changes, and exit codes into traces.
  - Partial 2026-06-15: Agent guardrails can now select `local` or AgentScope `DockerWorkspace`, configure base image, and choose temporary or persistent Docker workdir. Trace capture for stdout/stderr/file changes remains pending.
  - Partial 2026-06-15: runtime traces now include `WORKSPACE_CONFIG` events for leader and worker agents, including backend, filesystem scope, Docker image, persistence mode, network policy, and permission profile.
  - Completed 2026-06-16: tool result processing now records `COMMAND_RESULT` events for Bash output with stdout, stderr, status, and exit-code metadata, and records `FILE_CHANGE` events for Write/Edit operations plus best-effort shell file-change hints. Trace detail now expands these payloads in the frontend.

- [x] Tool permission profiles
  - Read-only
  - Trusted tools without confirmation
  - Confirm destructive actions
  - Always ask
  - Per-Agent overrides at creation/edit time.
  - Implemented 2026-06-15: Agent guardrails now expose four permission profiles. Runtime maps them to AgentScope permission modes and read-only removes write/edit/bash/http/MCP execution paths.

## P5. Frontend Performance And UX

- [x] Route-level code splitting
  - Lazy-load heavy pages such as Agents, MCP, Skills, Trace, and Sandbox.
  - Add lightweight page skeletons.
  - Implemented 2026-06-15: App pages are now loaded with `React.lazy` and `Suspense`, with a lightweight skeleton fallback. Build output is split into page chunks; shared icon/design chunks remain a follow-up optimization.

- [x] Chat message rendering performance
  - Add `content-visibility: auto` to long message lists.
  - Avoid unnecessary derived-state effects.
  - Keep long tool outputs collapsed or height-limited.
  - Partial 2026-06-15: historical Chat message bubbles now use `content-visibility: auto` with intrinsic size hints. Tool outputs were already height-limited. Derived-state effect cleanup remains pending.
  - Completed 2026-06-15: removed the derived `renderedTeamActivity` state/effect and render the selected Team activity directly.

- [x] UI consistency pass
  - Container panels stay borderless/floating.
  - Repeated item cards keep borders.
  - Use consistent transitions for drawers, sidebars, and panels.
  - Implemented 2026-06-15: added a shared `.ui-card-transition` utility and applied it to high-frequency repeated cards in Chat, Trace, and Team member lists.

## P6. Model Reliability

- [x] Multi-step fallback model chain
  - Current AgentScope fallback support is effectively single fallback.
  - Add an application-level fallback chain that tries configured models in order.
  - Record fallback attempts and failures in runtime trace.
  - Implemented 2026-06-15: added an application-level `FallbackChainModel` that tries primary and fallback models in order, records attempt/success/failure events into `run_events`, and includes fallback chain in Agent cache keys.

- [x] Provider-side prompt cache reporting
  - Continue showing cache creation and cache hit usage.
  - Persist cache usage into runtime trace.
  - Show cost-saving estimate when provider pricing is configured.
  - Implemented 2026-06-15: MODEL_CALL_END cache metadata is persisted as `MODEL_CACHE_USAGE`; Chat now shows hit rate and estimated saved input tokens. Pricing-based currency estimate remains future work until provider pricing is configured.

## P7. Longer-Term Features

- [x] Electron macOS/Windows 内测打包
  - 参考 `docs/electron-packaging-plan.md` 完成第一阶段内测打包。
  - 使用 `electron-builder` 输出 macOS `.dmg`/`.zip` 和 Windows `.exe` 安装包。
  - 先允许依赖本机 Python/uv 或项目内 `.venv`，重点验证桌面壳、前端、后端健康检查和核心业务流程能跑通。
  - 验证 SQLite、系统设置、Agent、Team、MCP、Skills、历史对话在桌面端重启后不丢失。
  - 实现于 2026-06-17：新增 `electron-builder` 配置和 `npm run electron:pack` / `electron:build:mac` / `electron:build:win` 命令，内测包会携带前端产物、Electron 主进程和后端源码资源；生产模式后端数据写入 Electron `userData` 目录。
  - 验证记录：`npm run build`、`npm run electron:compile`、脚本语法检查已通过；`electron-builder --dir` 在当前沙箱环境卡在 packaging 阶段，需要在本机终端或 CI 中继续验证真实产物。

- [ ] Electron 正式打包与后端运行时内置
  - 将 Python 后端打成 macOS/Windows 平台相关可执行文件，供 Electron 生产模式启动。
  - 将运行时数据目录迁移到 Electron `userData` 路径，避免写入应用安装目录。
  - 补齐后端异常展示、日志路径、子进程退出清理、安装包资源过滤和平台差异处理。
  - 正式发布前再处理 macOS notarization、Windows 代码签名和安装包体积优化。
  - 部分实现于 2026-06-17：新增 `backend/src/server/desktop_entry.py` 和 `scripts/build-backend-desktop.sh`，Electron 生产模式会优先启动内置 `backend-server` / `backend-server.exe`，找不到时回退到内测源码启动。仍需在 macOS 与 Windows 真机分别验证 PyInstaller 产物、签名、公证和安装包行为。

- [ ] Milvus or vector database integration.
- [ ] Memory decay and automatic memory reorganization.
  - Partial 2026-06-15: accepted memory facts now track `access_count` and `last_accessed_at` whenever they are injected into an Agent run. Cleanup suggestions use this signal to identify stale low-use facts; automatic reorganization remains behind user approval.
- [ ] Dynamic Workflows 概念评估与技术预研。
  - 参考 `docs/dynamic-workflows-plan.md`，后续评估是否引入动态编排模式。
  - 暂不进入实现；先观察 Agent、Team、Trace、Sandbox、Electron 打包稳定性。
  - 决策时重点比较三种路线：只增强 Team Runtime、先做固定 Workflow、直接做 Dynamic Workflow。
  - 如果决定实现，第一版优先放在 Chat 的“动态编排模式”，复用现有 `run_events`、HITL、Team Activity 和 Sandbox。
- [ ] Community Agent and Team template marketplace.
- [ ] Multi-language UI support.
- [ ] Desktop auto-update flow.

## P8. 大而全 Agent 平台补齐 TODO

> 本节用于把当前工作台从“可用的多 Agent 协作工具”继续推进到“大而全的 Agent 平台”。优先级按平台地基能力排序，后续实现时优先从 P0 开始。

### P0. 平台地基能力

- [x] 运行任务中心
  - 新增统一 Run 列表页或 Chat 侧边运行中心，展示运行中、等待 HITL、已完成、失败、已取消任务。
  - 将后台 Run 状态持久化到 SQLite，后端重启后至少能恢复任务记录和最终状态。
  - 支持查看任务进度、当前阶段、参与 Agent / Team、Worker 完成数和最终结果。
  - 支持取消运行中任务。
  - 实现于 2026-06-19：新增运行任务页面和 `/api/chat/runs` 相关接口，可查看当前进程内后台 Run，并支持取消运行中任务。
  - 更新于 2026-06-20：新增 `chat_runs` / `chat_run_events` SQLite 持久化；后端重启后可恢复任务记录、最终状态、原始事件和完整结果；Run Center 改为任务看板，底层事件细节交给运行轨迹页。
  - 后续补充项：
    - [ ] 失败重试。
    - [ ] 从历史 Run 重新运行。
    - [ ] 将 Sandbox / Workflow 运行统一接入同一套 Run 生命周期。
    - [ ] 运行中任务跨后端重启恢复执行；当前只能恢复记录和最终状态，不能恢复已中断的 Python task。

- [ ] 统一资源权限系统
  - 定义 Agent / Team / Tool / Skill / MCP / Knowledge Tool 的统一授权模型。
  - 支持 Agent 级文件目录访问范围、网络访问范围、工具调用权限范围。
  - 支持不同工具的审批策略：自动放行、破坏性确认、每次确认、禁用。
  - 保存每次权限决策和人工审批记录，进入 `run_events` 审计。
  - 为后续多用户和组织空间预留 owner、workspace、role 字段。
  - 第一阶段实现于 2026-06-20：新增 `docs/unified-permission-system-plan.md` 和 `backend/src/security/permissions.py`，将旧 guardrails 归一化为 `UnifiedPermissionProfile`，运行时 AgentScope permission mode、工具注入、缓存 key 和 workspace trace 已统一读取该模型。
  - 第二阶段实现于 2026-06-22：新增 `PERMISSION_POLICY` 和 `PERMISSION_DECISION` 运行事件；Agent/Worker 启动会记录权限策略快照，每次工具调用开始会记录资源类型、动作、策略、决策和原因，进入 `run_events` 审计。
  - 第三阶段实现于 2026-06-22：Trace 页面新增权限审计筛选和权限事件摘要，可直接查看策略快照、工具资源类型、动作、allow/ask/deny 决策和原因。
  - 后续补充项：
    - [ ] 前端 Agent 高级设置改成资源权限视角，而不是散落的 guardrails 字段。
    - [ ] 增加 Tool / Skill / MCP 级别的单项覆盖策略，例如某个 MCP 自动放行、某个 Bash 总是审批。
    - [ ] 预留 Workspace / 用户 / 角色表结构，为多用户和组织空间做准备。

- [ ] Agent / Prompt / Team 版本管理
  - Agent 每次保存生成新版本，保留 prompt、模型、工具、权限、记忆注入等配置快照。
  - Team 运行时绑定成员 Agent 的具体版本，历史运行可还原当时配置。
  - 支持版本 diff、回滚、复制为新 Agent、标记稳定版本。
  - Chat / Trace / Sandbox 记录运行使用的 Agent 版本号。

### P1. 编排、评测和知识能力

- [ ] Workflow 固定编排第一版
  - 提供可视化或表单化 Workflow 配置：步骤、依赖、输入输出、条件分支、人工节点。
  - 步骤可选择 Agent、Team、Tool、Skill 或人工输入。
  - 支持失败策略：终止、跳过、重试、转人工。
  - 运行时复用现有 `run_events`、HITL、Trace、Sandbox。
  - 第一版先做固定 Workflow，再评估是否升级到 Dynamic Workflows。

- [ ] Dynamic Workflows 技术预研落地入口
  - 基于 `docs/dynamic-workflows-plan.md` 设计动态编排模式入口。
  - 让 Leader 可以根据任务动态选择 Worker、工具、步骤和依赖。
  - 前端展示动态生成的执行图，并允许用户在关键节点确认。
  - Sandbox 中支持保存一次动态编排结果作为固定 Workflow 模板。

- [ ] 低优先级：Team Plan 动态化增强
  - 当前 Team Plan 已支持模型优先生成结构化 Plan，失败时使用规则模板兜底。
  - 现阶段看起来像固定步骤，主要是因为 Team 场景会按成员职责收敛为“Worker 步骤 + Leader 汇总”。
  - 后续如要增强，再做 Team 专用动态 Plan：允许模型决定是否跳过某些 Worker、是否增加跨 Worker 依赖、是否插入验证步骤。
  - 该项暂不进入近期主线，优先保证 Subagent、权限、工具、运行任务中心等地基能力稳定。

- [ ] 评测体系增强
  - Sandbox 支持测试集批量运行。
  - 支持断言类型：文本包含、JSON 字段、工具调用次数、是否命中特定知识、是否触发 HITL。
  - 增加 LLM Judge 评分，记录正确性、完整性、格式、工具使用合理性。
  - Agent / Team 修改后可批量回归测试，并生成对比报告。
  - Trace 中关联测试结果，方便定位失败原因。

- [ ] 平台内置知识库/RAG 模块
  - 支持文件上传、文档解析、chunk、embedding、检索、rerank。
  - 支持知识库集合、权限、标签、更新时间和引用来源。
  - Chat 工具调用结果展示命中文档片段和引用。
  - 提供检索调试页面：query、召回结果、rerank 分数、最终注入上下文。
  - 第一版可先支持 SQLite + 本地向量索引，后续再接 Milvus / pgvector。

### P2. 运营、市场和发布能力

- [ ] 成本与用量中心
  - 按 Agent、Team、Run、模型供应商统计 token、调用次数、Prompt Cache 命中、估算成本。
  - 支持日/周/月趋势图和单次运行明细。
  - 支持预算阈值提醒和单 Agent/Team 最大成本限制。
  - 将工具调用耗时、模型耗时、Worker 等待时间纳入统计。

- [ ] 模板与插件市场
  - 建立 Agent 模板、Team 模板、Skill 包、MCP Server、Workflow 模板的统一市场页。
  - 支持安装、卸载、启用、禁用、版本升级和依赖检查。
  - 支持从本地 zip / git 仓库 / 远程 registry 导入。
  - 模板安装后生成可编辑副本，避免直接污染内置模板。

- [ ] 工具管理中心
  - 参考 `docs/tools-management-plan.md`，建立统一 Tools 入口。
  - 子界面包含 MCP、HTTP 工具、系统内部工具。
  - HTTP 工具使用独立 `http_tools` 表和 `/api/tools/http` 业务接口，不再继续绑定知识库概念。
  - 系统内部工具第一版只读展示工具说明、动作类型、风险等级和默认注入状态。
  - 第一阶段实现于 2026-06-22：新增 Tools 页面、HTTP 工具 CRUD API、`http_tools` SQLite 表、系统工具只读 API。MCP Tab 复用现有 MCP 页面；旧 MCP 导航和知识库工具接口暂时保留兼容。
  - 第二阶段实现于 2026-06-22：旧知识库工具会按原 ID 迁移为 HTTP 工具模板；Agent 高级设置已改为选择 HTTP 工具；运行时通过 `ConfiguredHttpTool` 按独立工具名注入；新增 HTTP 工具调试接口；Trace 可展示 HTTP URL、方法、状态、耗时摘要。
  - 后续补充项：
    - [x] 将现有知识库工具迁移为 HTTP 工具模板。
    - [x] Agent 高级设置中把“知识库工具”升级为“HTTP 工具”选择。
    - [x] 实现 `ConfiguredHttpTool`，让每个 HTTP 工具以独立工具名注入 Agent。
    - [x] 增加 HTTP 工具调试请求和响应提取配置。
    - [x] Trace 展示 HTTP 工具 URL、方法、耗时和响应摘要。
    - [x] HTTP 工具编辑器补齐高级字段 UI：method、query/body 模板、parameter schema、responsePath、auth 类型。
    - [x] 接入统一资源权限系统，支持 HTTP 工具单项覆盖策略。
      - 实现于 2026-06-22：HTTP 工具支持 `approvalPolicy` 单项覆盖，可配置继承 Agent 权限、自动放行、每次确认或禁用；运行时 `ConfiguredHttpTool.check_permissions` 会执行该策略。

- [ ] 多用户与 Workspace 预留
  - 增加 Workspace 概念，Agent、Team、Memory、Run、Tool 均归属某个 Workspace。
  - 预留用户、角色、权限表结构。
  - 支持本地单用户模式和未来服务端多用户模式共存。
  - 操作审计记录用户、时间、资源、动作和结果。

- [ ] 正式桌面发布完善
  - 完成 macOS / Windows 后端可执行文件内置验证。
  - 补齐 macOS 签名和公证、Windows 代码签名。
  - 增加自动更新、崩溃日志、用户数据备份和恢复。
  - 桌面端增加本地模型服务检测和后端异常恢复提示。

## Operating Rules

- Complete one major feature before committing.
- Update this TODO when implementation order changes.
- Prefer SQLite normalized tables for durable application data.
- Keep sandbox runs non-persistent unless explicitly saved as test cases.
- Do not hide repeated tool calls; only fix bad display names and overflowing layout.
