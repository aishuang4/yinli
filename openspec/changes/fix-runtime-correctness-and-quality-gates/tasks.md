# Tasks

阶段 A（第 1–9 组）可以立即实施。阶段 B（第 10–16 组）必须等 `simplify-global-session-context` 归档后开始。所有后端命令在 `backend/` 目录执行，前端命令在 `frontend/` 目录执行。各项具体写法以 design.md 对应的 Decision 为准。

## 1. 依赖分组与工具配置

- [ ] 1.1 按 Decision 8 修改 `backend/pyproject.toml`：从 `[project].dependencies` 删除 `pytest>=9.0.2`，新增 `[dependency-groups] dev`（pytest、`ruff==0.17.1`、`basedpyright==1.40.2`），执行 `uv lock`。验证：`uv sync && uv run ruff --version` 输出 `ruff 0.17.1`；`uv run basedpyright --version` 含 `1.40.2`；`uv export --frozen --no-dev | grep -ci '^pytest=='` 输出 0
- [ ] 1.2 修改 `scripts/build-backend-desktop.sh`：`uv sync` 改为 `uv sync --no-dev`，`uv run --with pyinstaller` 改为 `uv run --no-dev --with pyinstaller`。验证：`grep -n 'no-dev' scripts/build-backend-desktop.sh` 有 2 行；若本机可打包，执行该脚本后 `backend/dist/backend-server/` 下不存在 `_internal/pytest`
- [ ] 1.3 删除占位文件 `backend/main.py`。验证：`test ! -e backend/main.py`；`grep -rn 'backend/main.py' deploy scripts backend/*.spec .github` 无输出
- [ ] 1.4 按 Decision 6 在 `backend/pyproject.toml` 追加 `[tool.ruff]`、`[tool.ruff.lint]`、`[tool.ruff.lint.flake8-bugbear]`、`[tool.ruff.lint.per-file-ignores]`。验证：`uv run ruff check src test --statistics` 只出现 Decision 6 表中列出的规则，不出现 F401、B008、B009、B010
- [ ] 1.5 按 Decision 7 追加 `[tool.basedpyright]`，在改任何源码之前执行 `uv run basedpyright --writebaseline`，提交 `backend/.basedpyright/baseline.json`。验证：紧接着执行 `uv run basedpyright` 退出码为 0，输出 `0 errors`

## 2. Ruff 阶段 A 修复

- [ ] 2.1 按 Decision 6 表格修复 F811（`scheduler/service.py`、`server/routes/mcp.py`）、F821（`security/authorization/service.py` 补导入 `RunAuthorization`）、F841（`routes/agents.py`、`routes/tools.py`、`skills/catalog.py`）、B007、B904、B905、F541。验证：`uv run ruff check src --select F811,F821,F841,B007,B904,B905,F541` 输出 `All checks passed!`
- [ ] 2.2 把 `src/server/routes/memory_api/{live,markdown,nodes,settings,skills}.py` 的星号导入按 Decision 6 列出的名字改为显式导入。验证：`uv run ruff check src --select F403,F405` 通过；`uv run pytest -q test/test_memory_nodes.py test/test_memory_markdown_store.py test/test_skill_evolution.py` 通过
- [ ] 2.3 修复 `src/` 中 14 处 B023（默认参数绑定循环变量）。逐处判断闭包是否在同一轮循环内被调用；如发现延迟调用的真实缺陷，在本任务下方记录文件与行号，并在对应测试文件补回归用例。验证：`uv run ruff check src --select B023` 通过；`uv run pytest -q test/test_permission_adapter*.py test/test_scheduler*.py test/test_authorization*.py` 通过（按实际存在的文件名）
- [ ] 2.4 修复 `test/` 中的 F841（12 处）、B023（6 处）、B905（1 处）。验证：`uv run ruff check test` 通过
- [ ] 2.5 按 Decision 5 修改 `src/server/routes/chat/completion.py` 的 `_schedule_skill_evolution`，新增 `_BACKGROUND_TASKS` 与 `_finish_background_task`；新增 `backend/test/test_background_task_retention.py`：用 monkeypatch 把 `_run_skill_evolution_background` 换成等待 `asyncio.Event` 的协程，断言调度后任务在 `_BACKGROUND_TASKS` 中、完成后被移除；再换成抛异常的协程，用 caplog 断言有一条 ERROR 日志。验证：`uv run ruff check src --select RUF006` 通过；`uv run pytest -q test/test_background_task_retention.py` 通过
- [ ] 2.6 全量验证：`uv run ruff check src test` 输出 `All checks passed!`；`uv run pytest -q` 除 3 个已知失败外全部通过

## 3. Windows 本地命令执行

- [ ] 3.1 按 Decision 2 修改 `src/agents/guarded_bash.py`：`call()` 的分支条件、导入（`contextlib`、`subprocess`、`ExecResult`、`logging`）、`_exec_windows_shell` 整体替换、新增 `_kill_windows_process_tree`。验证：`grep -n 'exec_shell' src/agents/guarded_bash.py` 无输出；`grep -n 'TypeError' src/agents/guarded_bash.py` 无输出
- [ ] 3.2 新增 `backend/test/test_guarded_bash_windows.py`，用 monkeypatch 替换 `src.agents.guarded_bash.asyncio.create_subprocess_exec` 与 `agent_shell_env`，直接调用 `_exec_windows_shell`，覆盖：① 子进程只创建一次，`env` 等于 `agent_shell_env()` 的返回值，参数以 `[shell, "/d", "/c", ...]` 开头；② 创建抛 `OSError` 时返回 `exit_code == 127`；③ `communicate` 超时时调用 `taskkill /PID <pid> /T /F` 并返回 `exit_code == -1`、`stderr == b"timed out"`；④ `monkeypatch.setattr(guarded_bash.os, "name", "nt")` 且后端类名不是 `LocalBackend` 时，`call()` 走 `Bash.call`（monkeypatch 父类方法并断言被调用）。验证：`uv run pytest -q test/test_guarded_bash_windows.py` 在 macOS 上通过
- [ ] 3.3 在 `.github/workflows/model-config.yml` 的后端 pytest 命令末尾追加 `test/test_guarded_bash_windows.py`。验证：`grep -n test_guarded_bash_windows .github/workflows/model-config.yml` 有输出
- [ ] 3.4 有 Windows 机器时手工验证：桌面端让智能体执行 `echo %PATH%` 与 `echo %VIRTUAL_ENV%`，PATH 以智能体虚拟环境 `Scripts` 目录开头；执行 `ping -n 30 127.0.0.1` 并设置短超时，超时后任务管理器中没有残留的 `ping.exe`。没有 Windows 机器时以 `model-config.yml` 的 Windows 任务通过为准，并在本任务下注明

## 4. 托管会话中间件

- [ ] 4.1 按 Decision 3 修改 `src/server/auth/middleware.py`：新增 `_TENANT_INIT_GUARD`、`_TENANT_INIT_LOCKS`、`_tenant_init_lock`、`_HostedIdentity`、`_resolve_hosted_identity`，`__call__` 改为 `await run_in_threadpool(_resolve_hosted_identity, scope)`。验证：`grep -n 'ensure_tenant\|current_user' src/server/auth/middleware.py` 的命中都在 `_resolve_hosted_identity` 函数体内
- [ ] 4.2 新增 `backend/test/test_hosted_session_middleware_threadpool.py`，用 `httpx.AsyncClient(transport=httpx.ASGITransport(app=...))` 对一个只挂该中间件的最小 Starlette 应用发请求，覆盖：① monkeypatch `current_user` 记录 `threading.get_ident()`，断言与事件循环线程不同；② 未登录返回 401 `{"detail": "需要登录"}`，停用返回 403 `{"detail": "账号已停用"}`；③ `get_tenant` 在 `ensure_tenant` 被调用前返回 `None`，`ensure_tenant` 内 `time.sleep(0.1)`，同一用户 5 个并发请求后 `ensure_tenant` 只被调用 1 次；④ 下游同步路由里 `current_tenant()` 的 `tenant_id` 等于该用户。验证：`uv run pytest -q test/test_hosted_session_middleware_threadpool.py` 通过，且现有托管鉴权测试（`uv run pytest -q -k hosted`）全部通过

## 5. 事件循环卡顿监测

- [ ] 5.1 按 Decision 4 新增 `src/server/loop_monitor.py`，`Settings` 新增 `loop_lag_warn_ms: int = 250`，在 `src/server/main.py` 的 `lifespan` 中 `yield` 前启动、`yield` 后停止。验证：`grep -n 'start_loop_monitor\|stop_loop_monitor' src/server/main.py` 各 1 行
- [ ] 5.2 新增 `backend/test/test_loop_monitor.py`，用 `asyncio.run` 覆盖：① `threshold_ms=100, interval=0.05` 时，协程内 `time.sleep(0.3)` 后 caplog 恰有 1 条 WARNING 且包含毫秒数；② `cooldown=10` 时连续两次阻塞只有 1 条；③ `start_loop_monitor(0)` 返回 `None`；④ `stop_loop_monitor` 后任务 `cancelled()` 为真。验证：`uv run pytest -q test/test_loop_monitor.py` 通过

## 6. 统一日志

- [ ] 6.1 按 Decision 11 新增 `src/server/logging_setup.py`，`Settings` 新增 `log_level: str = "INFO"`，`src/server/main.py` 在创建应用前调用 `configure_logging()`。新增 `backend/test/test_logging_setup.py`：连续调用两次后 `logging.getLogger("src")` 只有 1 个带标记的 handler；`logging.getLogger("src.x").warning("x")` 的格式化结果含时间、`WARNING`、`[src.x]`；caplog 仍能捕获。验证：`uv run pytest -q test/test_logging_setup.py` 通过
- [ ] 6.2 `src/server/main.py` 的 11 处 `print` 改为 logger（异常分支用 `logger.warning(..., exc_info=True)`）。验证：`grep -c 'print(' src/server/main.py` 输出 0；本地启动后端时终端出现 `INFO [src.server.main] [Server] ... started on ...`

## 7. 前端严格模式与告警

- [ ] 7.1 `frontend/tsconfig.app.json` 增加 `"strict": true`。验证：`npx tsc -p tsconfig.app.json --noEmit` 无输出且退出码为 0
- [ ] 7.2 按 Decision 10 修改 `src/features/group-studio/useGroupStudioRoom.ts`：react 导入补 `useRef`；约 170 行依赖数组加 `localStudioSeed?.modelConfigId`；`postToStudio` 定义后增加 `postToStudioRef` 与同步 effect；约 755 行改为 `postToStudioRef.current(...)`。验证：`npm run lint -- --max-warnings=0` 退出码为 0；`npm test` 通过

## 8. 其他小项

- [ ] 8.1 按 Decision 12 新建 `src/memory/markdown_sections.py`，搬入 6 个相同函数；两个源文件删除定义并按别名导入；新增 `backend/test/test_markdown_sections.py`（每个函数至少 1 个用例）。验证：`grep -c '^def _rebuild_summary_section\|^def _parse_markdown_sections\|^def _replace_section\|^def _strip_source_reference\|^def _is_near_duplicate_note\|^def _dedupe_tokens' src/memory/markdown_reflection.py src/tools/memory_reflection.py` 两个文件都是 0；`uv run pytest -q test/test_markdown_sections.py test/test_memory_reflection.py test/test_memory_reflection_tools.py` 通过
- [ ] 8.2 `src/sandbox/hosted_workspace.py` 的 `hashlib.sha1(...)` 增加 `usedforsecurity=False`。验证：`grep -n 'sha1' src/sandbox/hosted_workspace.py` 的命中行都含 `usedforsecurity=False`
- [ ] 8.3 类型检查复核：`uv run basedpyright` 退出码为 0。出现新错误时修改本变更引入的代码，不得重新生成基线来放行；若本变更修掉了基线内的错误，执行 `uv run basedpyright --writebaseline` 并确认条目数变小

## 9. 统一门禁脚本与 CI

- [ ] 9.1 按 Decision 9 新建 `scripts/quality-gates.sh` 并 `chmod +x`。验证：`bash -n scripts/quality-gates.sh` 无输出；在仓库根目录执行 `scripts/quality-gates.sh backend` 与 `scripts/quality-gates.sh frontend` 退出码都为 0；`scripts/quality-gates.sh foo` 退出码为 2
- [ ] 9.2 反向验证门禁会拦截（每步做完即还原）：① 在任一 `src` 文件末尾加 `_x = undefined_name`，`scripts/quality-gates.sh backend` 在 Ruff 步骤失败且输出 F821；② 加 `def _y() -> int: return "a"`，在 basedpyright 步骤失败；③ 在任一 effect 里引用未列入依赖的变量，`scripts/quality-gates.sh frontend` 在 lint 步骤失败；④ 在 `.basedpyright/baseline.json` 里复制一条条目并设 `QUALITY_BASE_REF=HEAD`，基线不增长检查失败且输出两个条目数。验证：4 次均失败，还原后 `git status --short` 不含这些文件
- [ ] 9.3 按 Decision 9 新建 `.github/workflows/quality-gates.yml`。验证：`uv run --with pyyaml python -c "import yaml,sys; yaml.safe_load(open('../.github/workflows/quality-gates.yml'))"` 无报错；工作流中除环境准备外只调用 `scripts/quality-gates.sh`，不重复列出检查命令
- [ ] 9.4 阶段 A 收尾：在仓库根目录执行 `scripts/quality-gates.sh all` 退出码为 0；执行 `openspec validate fix-runtime-correctness-and-quality-gates --strict` 通过；把 Open Questions 中关于 Gitee 流水线的结论（或"待维护者答复"）写在本任务下方

## 10. 阶段 B 前置（`simplify-global-session-context` 归档后）

- [ ] 10.1 确认前置条件：`openspec list --json` 中没有 `simplify-global-session-context`，且 `ls openspec/changes/archive | grep simplify-global-session-context` 有输出；当前分支已合入它的最终代码。验证：两条命令结果符合预期
- [ ] 10.2 重算阶段 B 的清单：执行 `tools/loop_affinity_audit.py`、`tools/reexport_audit.py`、`tools/mutable_state_audit.py`（路径见 design.md），以及 `uv run ruff check src --select BLE001,S110,S112,T201,C901 --statistics`，把结果数字和清单更新到 design.md Decision 13–16 的对应位置。验证：design.md 中的清单与脚本输出一致
- [ ] 10.3 移除临时豁免（Decision 18）：确认 `grep -n scope_guardrails_to_project src/server/routes/chat/runtime.py` 无输出后，删除 `per-file-ignores` 中 `runtime.py` 一行；确认 3 个已知失败测试已通过后，删除 `scripts/quality-gates.sh` 中的 `DESELECT` 数组及其引用。验证：`scripts/quality-gates.sh backend` 退出码为 0

## 11. 路由并发（阶段 B）

- [ ] 11.1 按 Decision 13 新增 `backend/test/test_route_handler_concurrency_contract.py`，`KEEP_ASYNC` 用 10.2 中 `loop_affinity_audit.py` 的输出。验证：此时 `uv run pytest -q test/test_route_handler_concurrency_contract.py` 失败，列出的待改路由数量等于审计脚本报告的"可改为 def"数量
- [ ] 11.2 按 Decision 13 的线程安全表，给 `server/routes/agents.py`、`agents/factory.py`、`mcp/registry.py`、`server/auth/cloud_client.py`、`tenancy/quota.py`（以 10.2 重算结果为准）加 `threading.RLock()`，所有修改容器以及先读后写的代码持锁执行。每个模块补一个并发测试：用 `concurrent.futures.ThreadPoolExecutor(max_workers=16)` 并发调用该模块的写入函数 200 次，断言最终状态与串行执行一致。验证：新增测试与相关模块现有测试通过
- [ ] 11.3 用 Decision 13 的改写脚本把待改路由的 `async def` 改为 `def`，每个路由文件一个提交。验证：`uv run pytest -q test/test_route_handler_concurrency_contract.py` 通过；每个提交后 `uv run pytest -q` 通过
- [ ] 11.4 按 Decision 13 表格对 5 个含 `await` 的路由改用 `run_in_threadpool`（`auth/platform_models.py::provided`、`auth/routes.py::write_platform_models`、`platform_gateway.py::chat_completions`、`routes/agent_builder.py::plan_agents`、`routes/sessions.py::upload_session_file`）。验证：相关测试通过；`grep -n 'run_in_threadpool' <这 5 个文件>` 每个文件至少 1 处
- [ ] 11.5 asyncio 调试模式全量运行：`PYTHONASYNCIODEBUG=1 uv run pytest -q 2>&1 | tee /tmp/asyncio-debug.log`。验证：测试全部通过；`grep -c 'Non-thread-safe operation' /tmp/asyncio-debug.log` 输出 0。若不为 0，把对应路由改回 `async def` 并加入 `KEEP_ASYNC`，重跑直至为 0
- [ ] 11.6 在 `scripts/quality-gates.sh` 的 pytest 命令前加 `PYTHONASYNCIODEBUG=1`。验证：`scripts/quality-gates.sh backend` 退出码为 0
- [ ] 11.7 托管模式手工验证：本地以托管模式启动后端，一个终端保持对话 SSE 流，另一个终端并发 20 次请求列表类接口。验证：SSE 没有超过 1 秒的停顿；后端日志没有"事件循环卡顿"告警

## 12. 未使用导入（阶段 B）

- [ ] 12.1 按 Decision 14 处理 10.2 审计出的隐式再导出：生产代码使用方改为从源模块导入；测试使用方改为从源模块导入；monkeypatch 依赖的以及对话引擎对 `routes/skills.py`、`routes/mcp.py` 的依赖改为冗余别名 `import x as x` 并注释使用方。验证：重新执行 `tools/reexport_audit.py`，输出 `隐式再导出共 0 个`
- [ ] 12.2 从 Ruff `ignore` 中删除 `F401`，执行 `uv run ruff check src test --select F401 --fix`。验证：`uv run ruff check src test` 通过；`uv run pytest -q` 通过；桌面端打包脚本可以启动后端（PyInstaller 不缺模块）

## 13. 宽泛异常与 print（阶段 B）

- [ ] 13.1 把 `BLE001`、`S110`、`S112`、`T201` 加入 Ruff `select`，`per-file-ignores` 增加 `src/security/authorization/evaluate_cli.py = ["T201"]`。验证：`uv run ruff check src --statistics` 只报这 4 个规则
- [ ] 13.2 按 Decision 15 的处理表，依次修改 `trace_recorder.py`、`storage/database.py`、`memory/nodes.py`、`memory/reflection.py`、`background_runs.py`、`tools/delegate_task.py`（前 6 个文件，共 39 处）。验证：`uv run ruff check <这 6 个文件> --select BLE001,S110,S112` 通过；相关测试通过
- [ ] 13.3 同样规则处理其余文件，按目录分批提交（`src/memory`、`src/scheduler`、`src/server/routes/chat`、其余）。验证：`uv run ruff check src --select BLE001,S110,S112` 通过；`grep -rn 'noqa: BLE001$\|noqa: S110$\|noqa: S112$' src` 无输出（每个豁免都带理由）
- [ ] 13.4 按 Decision 15 把剩余 16 处 `print` 改为 logger。验证：`uv run ruff check src --select T201` 通过；`uv run pytest -q` 通过

## 14. 复杂度上限（阶段 B）

- [ ] 14.1 按 Decision 16 开启 `C901`（`max-complexity = 15`），执行 `uv run ruff check src --select C901 --add-noqa`，再把每个 `# noqa: C901` 补为 `# noqa: C901 - 待 restructure-code-layering 拆分`。验证：`uv run ruff check src` 通过；`grep -rn 'noqa: C901$' src` 无输出

## 15. 简体中文（阶段 B）

- [ ] 15.1 dev 组增加 `opencc-python-reimplemented==0.1.7`，`uv lock`。验证：`uv run python -c "from opencc import OpenCC; print(OpenCC('t2s').convert('記錄'))"` 输出简体的"记录"
- [ ] 15.2 按 Decision 17 执行转换脚本，单独提交，提交信息以 `chore(text):` 开头；把该提交哈希写入仓库根目录 `.git-blame-ignore-revs`（下一个提交）。验证：`git show --stat HEAD~1` 只含文字改动；`uv run pytest -q` 与 `npm test` 通过
- [ ] 15.3 按 Decision 17 新增 `backend/test/test_simplified_chinese_contract.py`。验证：`uv run pytest -q test/test_simplified_chinese_contract.py` 通过；临时在任一源文件注释里写一个繁体字后该测试失败并输出文件与行号，还原后通过

## 16. 阶段 B 收尾

- [ ] 16.1 在仓库根目录执行 `scripts/quality-gates.sh all`。验证：退出码为 0
- [ ] 16.2 执行 `openspec validate fix-runtime-correctness-and-quality-gates --strict`。验证：输出校验通过
