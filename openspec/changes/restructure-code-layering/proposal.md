# Proposal

## Why

后端目录名看起来是分层的（`server` → 业务域 → `storage` → `tenancy` → `config`），但依赖方向没有任何约束：快照里有 66 条"低层直接导入高层"的边。其中最重的三处是：业务域把 `server/routes/agents.py` 当作 Agent 注册表使用（11 条边）；对话引擎的 93 个文件、1.9 万行代码放在 `server/routes/chat/` 下，让 `runtime`/`subagents`/`memory`/`tools` 反向依赖路由层（20 条边）；`tenancy/lifecycle.py` 在最底层编排整个应用启动（13 条边）。此外，存储层私有的 `_lock`/`_connect` 被 17 个源文件直接使用，且 `with _lock, _connect() as conn:` 及存储层内部约 194 处同类写法都不会关闭连接；前端 40 个文件各写各的 `fetch` 与错误解析，并靠改写 `window.fetch` 适配桌面端。

这些问题让"改一处动全身"成为常态，也让新代码无从判断该放哪里。本变更只解决"文件在哪一层、依赖朝哪个方向"；另外两个同样重要的病因——同一运行时事实在四层事件表示里各有一份手写副本，以及 `database.py` 同时拥有十几张表——分别由后继变更 `unify-chat-runtime-events` 与 `split-storage-by-aggregate` 处理；前后端契约的机器对账与按目录拆分的 AGENTS.md 由可并行的 `define-runtime-contracts` 提供。`simplify-global-session-context` 归档后模块边界已经稳定，`fix-runtime-correctness-and-quality-gates` 也提供了合并门禁，是一次性把分层固化下来的时机。

## What Changes

- 引入 import-linter 分层契约，把现有向上导入写成基线（`ignore_imports`）；基线只减不增，并接入 `scripts/quality-gates.sh`。W5 完成后追加零例外的 `server is a leaf` 契约：任何非 `src.server` 模块导入 `src.server.*` 直接失败。
- 每个搬迁目标包（`src/agents`、`src/tools`、`src/common`、`src/models`、`src/runtime/chat`、`src/server`）在搬入的同一提交里写明显式公开面（`__all__`）并新建一份只写不变量与测试锚点的 `AGENTS.md`（附一行 `CLAUDE.md` 转发）；`test/test_agents_md.py` 检查目录齐全与锚点存在。
- W1：把 `server/routes/agents.py` 中 22 个非 HTTP 定义（`_agents` 缓存及其锁、`get_registered_agent` 等）搬到 `src/agents/registry.py`；把 `server/routes/tools.py` 中 HTTP/知识库工具目录搬到 `src/tools/http_tool_catalog.py`；对话引擎改为直接从 `src.skills.*`、`src.mcp.registry` 导入；`mcp/registry.py` 不再绕道路由模块调用自己的函数。
- W2：`server/auth/secrets.py` 搬到 `src/common/secrets.py`。
- W3：`server/auth/key2api.py`、`server/auth/cloud_client.py` 搬到 `src/models/platform/`；`config/model_catalog.py` 中调用上游的部分拆到 `src/models/platform/upstream_catalog.py`。
- W4：对话引擎从 `src/server/routes/chat/` 整体搬到 `src/runtime/chat/`（除 `__init__.py`、`router.py`、`handlers/` 外的 93 个文件），用脚本改写全部导入与 monkeypatch 路径。
- W5：`tenancy/lifecycle.py` 搬到 `src/server/tenant_lifecycle.py`，`iter_process_tenants` 搬到 `storage/control_records.py` 并删除其两个别名；之后 `config` 与 `tenancy` 合并为同一底层。
- W6：连接类改为在 `with` 结束时关闭（`ClosingConnection`）；存储层提供公开的 `database.transaction()`、`control_db.control_transaction()`，替换全部外部 `_lock`/`_connect`/`_control_lock` 用法；新增 `src/common/time.py` 的 `utc_now_iso()` 替换 15 个重复实现；`trace_recorder._record_run_event` 改为公开名 `record_run_event`。
- W7：为 `observe_source_event`、`_team_event` 提交金标测试但不重写它们（这两段翻译层将由 `unify-chat-runtime-events` 整体替换，豁免注释改为指向该变更）；按步骤拆分另 4 个热点（`stream_leader_agent_reply`、`_run_worker_turn`、`evaluate_job_feasibility`、`get_or_create_agent`）并去掉它们的 `# noqa: C901`。
- W8：双键兼容读取（`.get("snake") or .get("camel")`，134 处）与 C901 豁免加入只减不增的检查；删除空包 `src/model/` 与经复核的未引用顶层函数，并用测试防止再出现；`src/catalog/skills.py` 改名为 `src/catalog/skill_projection.py`，避免与 `src/skills/catalog.py` 混淆。
- W9：前端新增 `frontend/src/api/client.ts`（`apiFetch`、`apiJson`、`apiVoid`、`ApiError`），所有请求改走该客户端；删除 `installDesktopFetchBridge` 对 `window.fetch` 的改写；前端检查禁止客户端以外的裸 `fetch(`；`ChatPage.tsx` 拆出会话请求、历史栏、模型选择三块。
- 本变更不改变任何 HTTP 接口、事件格式或数据库结构。**BREAKING**（仅对仓库内部）：大量模块路径变化，未合并的分支需要用同一脚本变基。

## Capabilities

### New Capabilities

- `backend-module-layering`: 后端按分层契约组织，路由层只含 HTTP 适配，对话引擎与注册表位于业务域，向上导入只减不增。
- `storage-access-api`: 存储层只通过公开事务入口被使用，连接在事务结束时关闭。
- `frontend-api-client`: 前端所有后端请求经由统一客户端，错误以统一类型抛出，不改写全局 `fetch`。
- `code-structure-ratchets`: 复杂度豁免、双键读取、未引用代码等结构指标只减不增。

### Modified Capabilities

（无。本变更不改变任何已有能力的对外行为。）

## Impact

- 前置条件：`simplify-global-session-context` 已归档；`fix-runtime-correctness-and-quality-gates` 阶段 B 已完成（提供 `scripts/quality-gates.sh`、`_agents_lock`、`# noqa: C901 - 待 restructure-code-layering 拆分` 标记，以及"隐式再导出为 0"）。与 `harden-desktop-and-hosted-access` 无依赖。建议（不阻塞）：`define-runtime-contracts` 已合并，W4 搬迁时可用其对账测试与 fixture 金标做额外验证。后继：`unify-chat-runtime-events`（W4 之后）、`split-storage-by-aggregate`（本变更归档后）。
- 后端新增：`src/agents/registry.py`、`src/tools/http_tool_catalog.py`、`src/common/__init__.py`、`src/common/secrets.py`、`src/common/time.py`、`src/models/platform/{__init__,key2api,cloud_client,upstream_catalog}.py`、`src/runtime/chat/**`（93 个搬入文件）、`src/server/tenant_lifecycle.py`；`src/agents`、`src/tools`、`src/common`、`src/models`、`src/runtime/chat`、`src/server` 各一份 `AGENTS.md` 与 `CLAUDE.md`。
- 后端删除：`src/server/auth/secrets.py`、`src/server/auth/key2api.py`、`src/server/auth/cloud_client.py`、`src/tenancy/lifecycle.py`、`src/model/`、经复核的未引用顶层函数；`src/catalog/skills.py` 改名为 `src/catalog/skill_projection.py`；`src/server/routes/chat/` 只保留 `__init__.py`、`router.py`、`handlers/`。
- 后端修改：`pyproject.toml`（`import-linter==2.15` 开发依赖与 `[tool.importlinter]`）、`uv.lock`、`.basedpyright/baseline.json`（搬迁后重写，条数不增）、`scripts/quality-gates.sh`、`src/storage/database.py`、`src/storage/control_db.py` 及其全部外部使用方、约 125 个引用对话引擎的源文件与测试、`test/test_import_boundaries.py`。
- 后端新增测试：`test/test_layer_baseline_ratchet.py`、`test/test_storage_transaction.py`、`test/test_storage_private_access.py`、`test/test_observation_rules_golden.py` 与 `test/golden/observation_rules.json`、`test/test_team_event_rules_golden.py` 与 `test/golden/team_event_rules.json`、`test/test_c901_exemption_ratchet.py`、`test/test_dual_key_lookup_ratchet.py`、`test/test_unreferenced_code.py`、`test/test_agents_md.py`。
- 前端：新增 `frontend/src/api/client.ts`、`frontend/src/api/client.test.ts`、`frontend/src/api/noRawFetch.test.ts`、`frontend/src/features/chat/{chatSessionApi,useHistoryPanel,useSessionModelChoice}.ts`；修改 40 个含裸 `fetch(` 的文件、`frontend/src/utils/apiUrl.ts`、`frontend/src/main.tsx`；`ChatPage.tsx` 从 995 行降到不超过 750 行。唯一可见变化：原来显示 `HTTP <状态码>` 的错误改为显示后端 detail 或"请求失败"。
- 变更内脚本：`openspec/changes/restructure-code-layering/tools/` 下 `layer_baseline.py`、`move_package.py`、`extract_symbols.py`、`move_symbols.py`、`rewrite_storage_access.py`、`replace_utc_helpers.py`、`dual_key_audit.py`、`dead_code_audit.py`。
- 不影响：HTTP 路径与响应、SSE 事件、SQLite 结构、Electron 主进程、打包配置（若 `backend-server.spec` 引用了被搬模块，由 `move_package.py` 一并改写）。
