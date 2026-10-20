# Tasks

约定：除特别说明，命令在 `backend/` 执行，`T=../openspec/changes/restructure-code-layering/tools`。"提交前检查"指 design.md Decision 2 的五条命令（ruff、basedpyright、lint-imports、pytest 失败集合对比），要求 `diff /tmp/before.txt /tmp/after.txt` 无输出。行号与数字是快照值，以重算结果为准。

## 1. 开工准备

- [ ] 1.1 确认前置条件。验证：在仓库根目录 `ls openspec/changes/archive | grep simplify-global-session-context` 有输出；`fix-runtime-correctness-and-quality-gates` 的 tasks.md 第 10–16 组全部勾选；在 `backend/` 执行 `uv run --no-sync python "$(find ../openspec -path '*fix-runtime-correctness-and-quality-gates/tools/reexport_audit.py' | head -1)"` 输出 `隐式再导出共 0 个`；`test -x ../scripts/quality-gates.sh` 成功。任一不满足就停止，不开工
- [ ] 1.2 从最新主分支新建分支 `refactor/code-layering`。验证：`git status --short` 无输出，`git branch --show-current` 输出 `refactor/code-layering`
- [ ] 1.3 在未改动的代码上用 Decision 2 的 pytest 命令生成 `/tmp/before.txt`。验证：文件存在；把 `wc -l < /tmp/before.txt` 的结果记在本任务下方（全绿时为 0）
- [ ] 1.4 重算快照数字：`uv run python $T/layer_baseline.py >/dev/null`（标准错误第一行是向上导入条数）、`uv run python $T/dual_key_audit.py | tail -1`、`uv run python $T/dead_code_audit.py | tail -1`、`grep -rn "noqa: C901" src | wc -l`、`grep -rlE '(^|[^.[:alnum:]_$])fetch[[:space:]]*\(' ../frontend/src --include=*.ts --include=*.tsx | grep -v '\.test\.' | wc -l`。验证：把 5 个数字与 design.md Context 的期望值（66、134、69–71、29、40）对照记在本任务下方；差异不改变 Decision 中的规则时继续，改变时先更新 design.md 再继续

## 2. 分层契约与基线（Decision 1）

- [ ] 2.1 执行 `uv add --dev import-linter==2.15`。验证：`uv run --no-sync lint-imports --help` 退出码为 0；`git diff --stat` 只包含 `pyproject.toml` 与 `uv.lock`
- [ ] 2.2 按 Decision 1 在 `pyproject.toml` 末尾追加 `[tool.importlinter]` 与 `backend layers` 契约，用 `uv run python $T/layer_baseline.py` 的输出整段替换 `ignore_imports`。验证：`uv run --no-sync lint-imports` 输出 `backend layers KEPT` 且退出码为 0；`layer_baseline.py` 的标准错误中未归层模块只有 `src.model`
- [ ] 2.3 新建 `test/test_layer_baseline_ratchet.py`（代码见 Decision 1），`MAX_ENTRIES` 填 2.2 的条数。验证：`uv run --no-sync pytest test/test_layer_baseline_ratchet.py` 通过；把 `MAX_ENTRIES` 临时加 1 后该测试失败并提示"请把 MAX_ENTRIES 下调"，还原
- [ ] 2.4 在 `scripts/quality-gates.sh` 的 `backend()` 中，类型基线检查的 `PY` 结束行之后、`uv run --no-sync pytest` 之前插入 `  uv run --no-sync lint-imports`。验证：在仓库根目录 `scripts/quality-gates.sh backend` 退出码为 0；在 `src/storage/database.py` 末尾临时加 `import src.server.main  # noqa` 后该脚本在 lint-imports 步骤失败并输出 `backend layers BROKEN`，还原
- [ ] 2.5 提交"分层契约与基线"。验证：提交前检查通过

## 3. W1：业务域不再依赖路由模块（Decision 3–5）

- [ ] 3.1 按 Decision 3 运行 `extract_symbols.py`，把 22 个名字搬到 `src/agents/registry.py`。验证：脚本无报错；`src/agents/registry.py` 存在且包含 `def get_registered_agent`、`_install_agent_registry()` 顶层调用；`src/server/routes/agents.py` 导入 `from src.agents import registry as agent_registry`
- [ ] 3.2 在 `src/agents/registry.py` 加入 Decision 3 的延迟 `_job_service()`，并把 `_job_service.pause` 改为 `_job_service().pause`。验证：`uv run --no-sync python -c "import src.agents.registry, src.scheduler.chat_bridge, src.server.main"` 成功
- [ ] 3.3 按 Decision 3 运行 `move_symbols.py` 与两条 ruff 命令，并按脚本"需人工处理"清单修改测试中的 `invalidate_agent` patch。验证：`grep -rn "server\.routes\.agents\|server\.routes import agents" src --include=*.py | grep -v "^src/server/"` 无输出；`uv run --no-sync pytest test/test_default_agent.py test/test_scheduled_job_resource_pause.py test/test_system_job_agent.py` 通过
- [ ] 3.4 验证路由与注册表共用同一份缓存（spec "Route modules contain only HTTP adapters"）。验证：`grep -n "^_agents\b\|^_agents:" src/server/routes/agents.py` 无输出；`grep -c "agent_registry\._agents" src/server/routes/agents.py` 大于 0；`uv run --no-sync pytest -q -k agent` 失败集合不变
- [ ] 3.5 按 Decision 4 用闭包脚本重算名单，再运行 `extract_symbols.py`/`move_symbols.py` 把 HTTP/知识库工具目录搬到 `src/tools/http_tool_catalog.py`。验证：`grep -rn "server\.routes\.tools\|server\.routes import tools" src --include=*.py | grep -v "^src/server/"` 无输出；`uv run --no-sync pytest -q -k "tool"` 失败集合不变
- [ ] 3.6 按 Decision 5 把引擎对 `routes/skills.py`、`routes/mcp.py` 的引用改为原始模块，并去掉 `src/mcp/registry.py` 绕道路由模块的调用，同步测试里的 `_fetch_*` patch。验证：Decision 5 末尾的 grep 无输出；`grep -rn "routes.mcp._fetch\|mcp_routes" src test` 无输出
- [ ] 3.7 重新生成分层基线并下调 `MAX_ENTRIES`，按 Decision 19 重写类型基线。验证：基线条数为 56（或 1.4 重算值减 10）；`uv run --no-sync pytest test/test_layer_baseline_ratchet.py` 通过；类型基线条数不增加
- [ ] 3.8 提交 W1。验证：提交前检查通过

## 4. W2、W3：密钥与平台客户端（Decision 6–7）

- [ ] 4.1 按 Decision 6 创建 `src/common/` 并搬迁 `secrets.py`，把契约中的 `(src.common)` 改为 `src.common`。验证：`grep -rn "server\.auth\.secrets\|server.auth import secrets" src test` 无输出；`grep -n "^from src\|^import src" src/common/secrets.py` 无输出；基线 55 条，`MAX_ENTRIES` 已下调
- [ ] 4.2 按 Decision 7 搬迁 `key2api.py`、`cloud_client.py` 到 `src/models/platform/`，并把 `account_path` 搬到 `cloud_client.py`。验证：`grep -rn "server\.auth\.\(key2api\|cloud_client\)" src test` 无输出；`grep -n "server" src/models/platform/*.py` 无输出；`uv run --no-sync pytest test/test_cloud_client.py` 通过
- [ ] 4.3 按 Decision 7 拆分 `ensure_hosted_upstream_catalog`：`config/model_catalog.py` 保留 `apply_upstream_models(names, upstream_base_url)`，新建 `src/models/platform/upstream_catalog.py`，更新调用方与测试。验证：`grep -n "key2api" src/config/*.py` 无输出；`uv run --no-sync pytest test/test_upstream_models.py` 通过
- [ ] 4.4 重新生成分层基线并下调 `MAX_ENTRIES`，按 Decision 19 重写类型基线。验证：基线 52 条；`ls src/server/auth/` 不再包含 `secrets.py`、`key2api.py`、`cloud_client.py`
- [ ] 4.5 提交 W2+W3。验证：提交前检查通过

## 5. W4：对话引擎搬到 `src/runtime/chat/`（Decision 8）

- [ ] 5.1 先执行 `uv run python $T/move_package.py --old src.server.routes.chat --new src.runtime.chat --keep __init__.py --keep router.py --keep handlers --dry-run`。验证：输出的搬迁文件数与 1.4 一致（快照 93），"需人工处理"为 0 条；否则先逐条记录在本任务下方
- [ ] 5.2 去掉 `--dry-run` 正式运行。验证：`find src/server/routes/chat -name "*.py" | sort` 只有 `__init__.py`、`router.py`、`handlers/` 下 5 个文件；`uv run --no-sync python -c "import src.server.main"` 成功
- [ ] 5.3 完成 Decision 8 的手工收尾 1–5（`CHAT_PREFIX`、两个 `__init__.py` 的 docstring 与再导出、`backend-server.spec`、文档路径）。验证：`uv run --no-sync pytest test/test_import_boundaries.py` 通过；`grep -n "routes.chat" *.spec` 只剩 `router`/`handlers`；`grep -rn "from src.server.routes.chat import" src test | grep -v "router\|handlers"` 无输出
- [ ] 5.4 按 Decision 19 同步 `per-file-ignores` 路径、重写类型基线，重新生成分层基线并下调 `MAX_ENTRIES`。验证：分层基线 32 条；`grep -n "server/routes/chat" pyproject.toml` 只剩路由文件或无输出；类型基线条数不增加
- [ ] 5.5 提交 W4（可拆为"脚本搬迁"与"手工收尾"两个提交）。验证：提交前检查通过；在提交说明中写明在途分支要先对自己的分支运行 5.2 的命令再变基

## 6. W5：租户生命周期上移（Decision 9）

- [ ] 6.1 按 Decision 9 `git mv` 到 `src/server/tenant_lifecycle.py` 并批量改写引用。验证：`grep -rn "tenancy\.lifecycle" src test` 无输出
- [ ] 6.2 把 `iter_process_tenants` 搬到 `src/storage/control_records.py` 末尾，删除 `startup_tenants`、`iter_tick_tenants`，更新 `main.py` 与 `scheduler/ticker.py`。验证：`grep -rn "startup_tenants\|iter_tick_tenants" src test` 无输出；`grep -n "src.server" src/scheduler/ticker.py` 无输出；`uv run --no-sync pytest test/test_tenant_background.py test/test_tenant_lifecycle.py` 通过
- [ ] 6.3 契约删除单独的 `"src.tenancy"` 层，最底层改为 `"src.config : src.tenancy : src.common : src.exception_detail"`；重新生成基线、下调 `MAX_ENTRIES`、重写类型基线。验证：分层基线 15 条；`uv run --no-sync lint-imports` 输出 `KEPT`
- [ ] 6.4 提交 W5。验证：提交前检查通过

## 7. W6：存储访问、时间工具与公开名（Decision 10–12）

- [ ] 7.1 在 `src/storage/database.py` 与 `src/storage/control_db.py` 加入 `ClosingConnection`、`factory=ClosingConnection`、`transaction()` / `control_transaction()`，并新建 `test/test_storage_transaction.py`（代码见 Decision 10）。验证：`uv run --no-sync pytest test/test_storage_transaction.py` 4 个用例通过；全量失败集合不变
- [ ] 7.2 复核没有在 `with` 之外继续使用连接的代码。验证：`grep -rnE "= (self\.connect|_connect|database\._connect|control_connect)\(\)" src` 与 `grep -rn "with conn:" src` 均无输出；若有输出，逐处改为在 `with` 块内完成读写后再继续
- [ ] 7.3 运行 `uv run python $T/rewrite_storage_access.py --dry-run`，再正式运行。验证：改写数与快照接近（102 处）；剩余"需人工处理"只含 Decision 10 表中的位置
- [ ] 7.4 按 Decision 10 表格手工修改 `DomainEventRepository`、`ProjectionWriter`、`skills/migration.py`，并只删除改写后未使用的 `closing` 导入。验证：`grep -n "self.lock\|self.connect" src/runtime/chat/projection/domain_events.py src/runtime/chat/projection/projection_writer.py` 无输出；`uv run --no-sync ruff check src test` 通过
- [ ] 7.5 新建 `test/test_storage_private_access.py`（代码见 Decision 10）。验证：`test_src_uses_public_transactions` 与 `test_tests_use_public_transactions` 通过；`test_no_local_utc_now_iso_helpers` 此时预期失败，在 7.6 完成后通过
- [ ] 7.6 按 Decision 11 运行 `replace_utc_helpers.py`（先 `--dry-run`），手工删除不再使用的 `datetime`/`timezone`/`UTC` 导入。验证：`src/common/time.py` 存在；`uv run --no-sync pytest test/test_storage_private_access.py` 全部通过；`uv run --no-sync ruff check src` 无新增 E402/F401
- [ ] 7.7 按 Decision 12 把 `_record_run_event` 整体改名为 `record_run_event`。验证：`grep -rn "_record_run_event" src test` 无输出；`uv run --no-sync pytest test/test_import_boundaries.py test/test_runtime_governance.py` 通过
- [ ] 7.8 提交 W6。验证：提交前检查通过

## 8. W7：拆分复杂度热点（Decision 13–15）

- [ ] 8.1 新建 `test/test_c901_exemption_ratchet.py`（Decision 15），暂不包含 `test_hotspots_have_no_exemption`，`MAX_EXEMPTIONS` 填 1.4 的重算值。验证：`uv run --no-sync pytest test/test_c901_exemption_ratchet.py` 通过
- [ ] 8.2 在重构前新建两个金标测试（代码见 Decision 13），先用 `UPDATE_GOLDEN=1` 生成 `test/golden/observation_rules.json`、`test/golden/team_event_rules.json`，单独提交。验证：不带 `UPDATE_GOLDEN` 连续运行 3 次都通过；`team_event_rules.json` 中除 `unknown.type` 与首条 `member.spawned` 外，每帧 `events` 都非空
- [ ] 8.3 新建 `src/runtime/chat/event_rules.py`，按 Decision 13 把 `observe_source_event` 改为 17 条规则的规则表，并删除其 `# noqa: C901`。验证：`uv run --no-sync pytest test/test_observation_rules_golden.py` 通过；`uv run --no-sync ruff check src --select C901` 输出不含 `observe_source_event`；`MAX_EXEMPTIONS` 减 1
- [ ] 8.4 按 Decision 13 把 `TeamEventNormalizerMixin._team_event` 的 16 个分支改为规则表，删除 `# noqa: C901`。验证：`uv run --no-sync pytest test/test_team_event_rules_golden.py test/test_event_stream_projection.py` 通过；C901 输出不含 `_team_event`；`MAX_EXEMPTIONS` 减 1
- [ ] 8.5 按 Decision 14 拆分 `stream_leader_agent_reply`（事件阶段与收尾阶段），删除 `# noqa: C901`。验证：C901 输出不含 `stream_leader_agent_reply` 及任何新拆出的函数；`uv run --no-sync pytest -q -k "leader or hitl or clarification or plan"` 失败集合不变；全量失败集合不变；`MAX_EXEMPTIONS` 减 1
- [ ] 8.6 按 Decision 14 拆分 `_run_worker_turn`。验证：C901 输出不含该函数；`uv run --no-sync pytest -q -k group_studio` 失败集合不变；`MAX_EXEMPTIONS` 减 1
- [ ] 8.7 按 Decision 14 拆分 `evaluate_job_feasibility`。验证：C901 输出不含该函数；`uv run --no-sync pytest -q -k feasibility` 失败集合不变；`MAX_EXEMPTIONS` 减 1
- [ ] 8.8 按 Decision 14 拆分 `get_or_create_agent`，并把 `test_hotspots_have_no_exemption` 加入 `test/test_c901_exemption_ratchet.py`。验证：C901 输出不含 6 个热点；`uv run --no-sync pytest test/test_c901_exemption_ratchet.py` 通过且 `MAX_EXEMPTIONS` 比 8.1 少 6
- [ ] 8.9 提交 W7（可按热点分多个提交）。验证：每个提交前检查都通过

## 9. W8：结构检查与清理（Decision 15–16）

- [ ] 9.1 用 `uv run python $T/dual_key_audit.py --json` 生成基线，新建 `test/test_dual_key_lookup_ratchet.py`（Decision 15）。验证：两个用例通过；在任一 `src` 文件临时加入 `x = {}.get("workspace_id") or {}.get("workspaceId")` 后 `test_dual_key_lookups_do_not_grow` 失败，还原
- [ ] 9.2 新建 `test/test_unreferenced_code.py`（Decision 16），按 Decision 16 的步骤 1–4 逐项复核并删除未引用定义，确需保留的写入 `ALLOWED` 并注明调用方。验证：`uv run --no-sync pytest test/test_unreferenced_code.py` 两个用例通过；全量失败集合不变
- [ ] 9.3 `git rm -r src/model`。验证：`grep -rn "src\.model\b" src test` 无输出；`uv run python $T/layer_baseline.py >/dev/null` 的标准错误不再出现"未归层的顶层模块"
- [ ] 9.4 按 Decision 16 把 `src/catalog/skills.py` 改名为 `src/catalog/skill_projection.py`，重新生成分层基线与类型基线。验证：`grep -rn "src\.catalog\.skills\b\|from src.catalog import skills" src test` 无输出；分层基线仍为 15 条
- [ ] 9.5 提交 W8。验证：提交前检查通过

## 10. W9：前端统一 API 客户端（Decision 17–18，在 `frontend/` 执行）

- [ ] 10.1 新建 `src/api/client.ts`、`src/api/client.test.ts`（代码见 Decision 17），并把 `src/utils/apiUrl.ts` 的 `resolveApiUrl` 改为先判断 `typeof window === 'undefined'`。验证：`npm run test -- src/api/client.test.ts` 8 个用例通过；`npm run build` 通过
- [ ] 10.2 迁移 `features/group-studio/api.ts`：加入 Decision 17 的 `groupStudioJson`/`groupStudioVoid`，替换 22 处 `fetch`，删除 `readDetail`。验证：`grep -n "fetch(\|readDetail" src/features/group-studio/api.ts` 无输出；`npm run build` 与 `npm run test` 通过
- [ ] 10.3 迁移 `features/auth`、`features/sessions`（删除 `SessionScopePanel.tsx` 的 `readErrorCode`，改用 `error instanceof ApiError ? error.code : ''`）。验证：这两个目录 `grep -rn "fetch(\|readErrorCode"` 无输出；`npm run build` 与 `npm run test` 通过
- [ ] 10.4 迁移 `hooks/`（含 `useEventStream.ts` 的 SSE，按 Decision 17 表中 SSE 写法）。验证：`grep -rn "fetch(" src/hooks` 无输出；`npm run build` 与 `npm run test` 通过；开发模式下发送一条消息能流式显示，点击停止后不出现错误提示
- [ ] 10.5 按 Decision 18 拆分 `pages/ChatPage.tsx`：新建 `features/chat/chatSessionApi.ts`、`useHistoryPanel.ts`、`useSessionModelChoice.ts`。验证：`grep -n "fetch(" src/pages/ChatPage.tsx` 无输出；`wc -l < src/pages/ChatPage.tsx` 不超过 750；`npm run build`、`npm run lint`、`npm run test` 通过；手工验证切换会话、删除会话、收起/展开历史栏、切换模型
- [ ] 10.6 迁移其余含裸 `fetch(` 的文件（以 1.4 的命令列出为准）。验证：在 `backend/` 执行 1.4 中的前端 `grep` 命令（去掉末尾的 `| wc -l`）只列出 `../frontend/src/api/client.ts`；`npm run build` 与 `npm run test` 通过
- [ ] 10.7 新建 `src/api/noRawFetch.test.ts`（代码见 Decision 17），删除 `installDesktopFetchBridge` 及其在 `main.tsx` 中的导入与调用。验证：`npm run test` 通过；`grep -rn "installDesktopFetchBridge\|window.fetch" src` 无输出；在任一组件临时加入 `await fetch('/api/agents')` 后 `noRawFetch.test.ts` 失败并列出文件与行号，还原
- [ ] 10.8 用 Electron 打包版手测：登录、发送消息与流式显示、审批、群聊工作室创建与发言、文件预览与下载、设置页保存。验证：以上操作均正常，开发者工具 Network 中所有 `/api` 请求都发往 `http://127.0.0.1:<桌面端口>`；把结果记在本任务下方
- [ ] 10.9 提交 W9。验证：在仓库根目录 `scripts/quality-gates.sh frontend` 退出码为 0

## 11. 收尾

- [ ] 11.1 整体验证。验证：在仓库根目录 `scripts/quality-gates.sh all` 退出码为 0；分层基线 15 条且 `MAX_ENTRIES = 15`；`diff /tmp/before.txt /tmp/after.txt` 无输出；桌面开发模式与打包版都能完成一轮对话
- [ ] 11.2 更新仓库文档中的目录说明（`AGENTS.md` 及 `docs/` 中提到 `server/routes/chat`、`tenancy/lifecycle`、`server/auth/{secrets,key2api,cloud_client}` 的位置），写明分层规则与"新增向上导入要改代码、不要加基线"。验证：Decision 19 第 4 步的 grep 对这些旧路径无输出
- [ ] 11.3 执行 `openspec validate restructure-code-layering --strict`。验证：输出通过，并把实际基线数字（分层、C901、双键、ALLOWED 条目数）记在本任务下方
