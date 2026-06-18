# Agent Workstation TODO

> Last updated: 2026-06-17
>
> This file is the working TODO for the project. Use it as the source of truth
> for upcoming implementation order. When a feature is completed, update the
> checkbox and add a short note if behavior changed.

## Current Focus

- [x] Stabilize runtime observability and testability.
- [x] Keep Agent, Team, Tool, Skill, Memory, and Settings data portable.
- [x] Improve high-frequency Chat and Team workflows before adding marketplace-style features.

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

## Operating Rules

- Complete one major feature before committing.
- Update this TODO when implementation order changes.
- Prefer SQLite normalized tables for durable application data.
- Keep sandbox runs non-persistent unless explicitly saved as test cases.
- Do not hide repeated tool calls; only fix bad display names and overflowing layout.
