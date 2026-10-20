# Proposal

## Why

2026-10-20 的代码质量审查发现了几处运行时缺陷。第一，196 个 `async` 路由里有 170 个没有任何 `await`，另有 5 个含 `await` 的路由在事件循环里直接调用同步存储或同步网络，托管会话中间件也在事件循环里查控制面库，首次登录还会在循环里完成整个租户初始化。一个慢查询会卡住所有并发请求，包括流式回复和 SSE。第二，Windows 本地命令执行把 `env=` 传给不接受这个参数的接口，触发 `TypeError` 后退回不带智能体环境的执行。第三，另有 3 处未定义名称、一个可能被回收的后台任务、128 处宽泛 `except Exception`、只有 29 个文件写日志且没有日志配置。这些问题能长期存在，是因为仓库没有任何后端 lint 和类型检查门禁，CI 只跑 3 个测试文件，前端 vitest 从未在 CI 执行。

## What Changes

- 分两个阶段实施。阶段 A 只改动少量文件，可以与进行中的 `simplify-global-session-context` 并行。阶段 B 是大范围机械修改（跨 20+ 个路由文件和 100+ 个源文件），MUST 在 `simplify-global-session-context` 归档后再开始，避免大量合并冲突。
- 阶段 A：
  - Windows 本地 shell 自己创建子进程，只执行一次，并带上 `agent_shell_env()`；超时时结束整个进程树。Windows 上的非本地后端改走后端自己的默认执行。
  - 托管会话中间件把登录会话查询和租户初始化移到线程池，同一用户的首次初始化按用户加锁串行，租户上下文仍在异步上下文中绑定。
  - 新增事件循环卡顿监测：卡顿超过阈值（默认 250 ms）写一条 warning，10 秒内最多一条。
  - 后台任务保留引用直到结束（修复 `chat/completion.py` 的悬空任务）。
  - 后端引入 Ruff（第一批规则：语法错误、pyflakes 除未使用导入外的全部规则、bugbear、悬空任务），并修完现有违规（含未定义的 `RunAuthorization`、重复定义、未使用变量、循环变量闭包、星号导入）。
  - 后端引入 basedpyright（basic 模式），把现有 561 个错误写入提交到仓库的基线，只拦新增错误。
  - **BREAKING**（只影响依赖声明）：`pytest` 从运行时依赖移到 `[dependency-groups] dev`；托管镜像的 `uv sync --no-dev` 不再安装 pytest。本地 `uv sync` 默认仍会安装 dev 组。
  - 新增统一门禁脚本 `scripts/quality-gates.sh`：后端跑 Ruff、basedpyright、类型基线不增长检查、全量 pytest；前端跑 `eslint --max-warnings=0`、vitest、构建和 Electron 编译。新增 CI 工作流 `quality-gates.yml` 调用该脚本。仓库远端是 Gitee，脚本也可以被 Gitee 流水线或本地钩子调用。
  - 前端 `tsconfig.app.json` 开启 `strict`（当前已是 0 错误），修掉 `useGroupStudioRoom.ts` 的 2 个 hooks 依赖告警。
  - 新增统一日志配置 `configure_logging()`，启动流程里的 `print` 改为 logger。
  - 把 `memory/markdown_reflection.py` 与 `tools/memory_reflection.py` 中 6 个逐字相同的函数提取到 `src/memory/markdown_sections.py`。
  - `sandbox/hosted_workspace.py` 的 sha1 标明 `usedforsecurity=False`；删除占位文件 `backend/main.py`。
- 阶段 B（`simplify-global-session-context` 归档后）：
  - 没有 `await` 且不依赖事件循环的 `async` 路由改为 `def`（当前 151 个），保留 19 个必须留在事件循环里的路由（含 3 个 SSE 流式路由）；仍是 `async` 并直接调用同步存储的 5 个路由改用 `run_in_threadpool`。给 5 个没有锁、但会被线程池路由修改的模块级缓存加锁。新增防回退测试，并用 asyncio 调试模式跑全量测试以发现跨线程调用事件循环的错误。
  - 开启 Ruff 未使用导入规则（清理 278 处，其中 42 处是被其他模块或测试使用的隐式再导出，改为从源模块导入或显式再导出）。
  - 开启宽泛异常规则（`BLE001`、`S110`、`S112`，当前 150 处），逐处改为精确异常、记录日志或注明理由；开启 `T201`，剩余 16 处 `print` 改为 logger。
  - 开启圈复杂度上限 15（`C901`），现有 29 个超限函数打上带理由的豁免注释，交给 `restructure-code-layering` 拆分。
  - 把源码、测试和前端里的繁体中文统一为简体（当前 120 个文件 432 行），并加防回退测试。
  - 移除 `src/server/routes/chat/runtime.py` 的 `F821` 临时豁免（对应死代码由 `simplify-global-session-context` 20.2 删除）。

## Capabilities

### New Capabilities

- `backend-request-concurrency`: 后端请求处理不得阻塞事件循环，包括路由的同步/异步声明规则、托管鉴权、后台任务引用和卡顿监测。
- `agent-shell-execution`: 智能体在 Windows 本地执行命令时的环境变量、执行次数、超时与后端选择规则。
- `engineering-quality-gates`: 后端 lint、类型检查基线、全量测试、前端严格模式与零告警、依赖分组，以及简体中文约定。
- `error-observability`: 宽泛异常处理必须可见、后端日志格式与输出统一。

### Modified Capabilities

（无。本变更不改任何业务行为。）

## Impact

- 后端新增文件：`backend/src/server/loop_monitor.py`、`backend/src/server/logging_setup.py`、`backend/src/memory/markdown_sections.py`、`backend/.basedpyright/baseline.json`、`backend/test/test_route_handler_concurrency_contract.py`、`backend/test/test_guarded_bash_windows.py`、`backend/test/test_loop_monitor.py`、`backend/test/test_simplified_chinese_contract.py`、`backend/test/test_markdown_sections.py`、`backend/test/test_hosted_session_middleware_threadpool.py`、`backend/test/test_background_task_retention.py`、`backend/test/test_logging_setup.py`、`scripts/quality-gates.sh`、`.git-blame-ignore-revs`。
- 后端修改：`backend/pyproject.toml`、`backend/uv.lock`、`backend/src/agents/guarded_bash.py`、`backend/src/server/auth/middleware.py`、`backend/src/server/main.py`、`backend/src/config/settings.py`、`backend/src/server/routes/chat/completion.py`，以及 Ruff 违规所在的约 20 个文件（阶段 A）、需要加锁的 5 个模块（`server/routes/agents.py`、`agents/factory.py`、`mcp/registry.py`、`server/auth/cloud_client.py`、`tenancy/quota.py`）和路由、异常、导入、繁体字所在的约 150 个文件（阶段 B）。删除 `backend/main.py`。
- 前端：`frontend/tsconfig.app.json`、`frontend/src/features/group-studio/useGroupStudioRoom.ts`；阶段 B 的繁体字修正涉及 8 个文件。
- CI：新增 `scripts/quality-gates.sh` 与调用它的 `.github/workflows/quality-gates.yml`；`model-config.yml` 的 Windows 测试列表增加 `test/test_guarded_bash_windows.py`。
- 审计脚本：本变更目录下的 `tools/loop_affinity_audit.py`、`tools/reexport_audit.py`、`tools/mutable_state_audit.py`，用于阶段 B 开始时重算清单，不进入产品代码。
- 构建：`scripts/build-backend-desktop.sh` 改为 `uv sync --no-dev` 与 `uv run --no-dev --with pyinstaller ...`。
- 外部行为：HTTP 接口、数据、界面都没有变化。可见的变化只有：并发下的响应更平稳；Windows 命令能读到智能体的 PATH 和虚拟环境；日志变得可读。
- 与其他变更的关系：3 个已知失败测试由 `simplify-global-session-context` 13.1 修复，本变更的 CI 在那之前按节点 ID 临时取消选择它们。`harden-desktop-and-hosted-access` 会修改 `server/auth/routes.py` 和 `server/main.py`，与本变更改的是不同位置，先后顺序任意。模块分层契约（import-linter）归 `restructure-code-layering` 负责。
