# Spec Delta

## Purpose

约束后端模块的依赖方向：`src.server` 位于最上层，只做 HTTP/进程适配；业务域位于中间层；`src.storage`、`src.tenancy`、`src.config` 依次在下。低层不得新增对高层的导入，历史遗留的向上导入以基线形式记录且只减不增。

## ADDED Requirements

### Requirement: Backend imports follow the layer contract

后端 MUST 在 `backend/pyproject.toml` 中声明名为 `backend layers` 的 import-linter 分层契约，并由 `scripts/quality-gates.sh` 执行 `lint-imports`。契约中未列入 `ignore_imports` 的任何"低层导入高层"都 SHALL 使门禁失败。`src` 下每个顶层包和顶层模块 MUST 属于某一层。

#### Scenario: New upward import is rejected

- **GIVEN** `src/storage/` 下某模块新增 `from src.server.routes.chat import router`
- **WHEN** 执行 `scripts/quality-gates.sh`
- **THEN** `lint-imports` 报告 `backend layers BROKEN` 且脚本以非零状态退出

#### Scenario: Every top-level package belongs to a layer

- **WHEN** 在 `backend/` 执行 `tools/layer_baseline.py`
- **THEN** 标准错误不输出"未归层的顶层模块"一行

### Requirement: The layer baseline only shrinks

`ignore_imports` 中的每一条 MUST 对应代码中仍然存在的直接导入；条目总数 MUST NOT 超过 `test/test_layer_baseline_ratchet.py` 记录的上限。本变更完成时上限 SHALL 不高于 15。

#### Scenario: Stale baseline entry fails

- **GIVEN** 某条向上导入已在代码中被删除，但仍留在 `ignore_imports`
- **WHEN** 执行 `uv run pytest test/test_layer_baseline_ratchet.py`
- **THEN** 测试失败并列出该条目，提示从基线删除

#### Scenario: Baseline growth fails

- **GIVEN** 有人把新的向上导入加进 `ignore_imports` 以绕过契约
- **WHEN** 执行同一测试
- **THEN** 测试因条目数超过上限而失败

### Requirement: Route modules contain only HTTP adapters

`src/server/routes/` 下的模块 SHALL 只包含路由函数、请求/响应模型的使用以及 HTTP 错误转换。Agent 注册表 MUST 位于 `src/agents/registry.py`，HTTP/知识库工具目录 MUST 位于 `src/tools/http_tool_catalog.py`；`src.server` 以外的模块 MUST NOT 导入 `src.server.routes.*`。

#### Scenario: Domain code looks up an agent

- **WHEN** 调度器、群聊工作室或对话引擎需要按 ID 取 Agent
- **THEN** 它们从 `src.agents.registry` 导入 `get_registered_agent`，`grep -rn "server.routes.agents" src --include=*.py | grep -v "^src/server/"` 无输出

#### Scenario: Tests patch the registry cache

- **GIVEN** 测试执行 `monkeypatch.setattr(agent_registry, "_agents", {...})`
- **WHEN** 测试调用 `src.server.routes.agents` 中的路由函数
- **THEN** 路由函数读到的是被替换后的缓存，因为路由模块通过 `agent_registry._agents` 访问而不是持有自己的绑定

### Requirement: The chat engine lives in the runtime layer

对话引擎 MUST 位于 `src/runtime/chat/`。`src/server/routes/chat/` SHALL 只保留 `__init__.py`、`router.py` 与 `handlers/`，其中不定义任何路由以外的业务逻辑。搬迁 MUST NOT 改变 HTTP 路径、SSE 事件或测试结果。

#### Scenario: Route package keeps only adapters

- **WHEN** 执行 `find backend/src/server/routes/chat -name '*.py'`
- **THEN** 输出只有 `__init__.py`、`router.py`、`handlers/__init__.py`、`handlers/resume.py`、`handlers/runs.py`、`handlers/send.py`、`handlers/sessions.py`

#### Scenario: Test outcomes are unchanged by the move

- **GIVEN** 搬迁前在同一提交上记录了全量测试的失败集合
- **WHEN** 执行 `tools/move_package.py` 并更新 `test/test_import_boundaries.py` 后重跑全量测试
- **THEN** 失败集合与搬迁前完全相同

### Requirement: Tenant lifecycle orchestration lives in the server layer

创建、停用、启用、删除租户的编排 MUST 位于 `src/server/tenant_lifecycle.py`。列出本进程服务租户的 `iter_process_tenants` MUST 位于 `src/storage/control_records.py`。`src/tenancy/` SHALL 不导入 `src.storage`、`src.server` 或任何业务域包（基线中记录的 `src.tenancy.capabilities -> src.agents.runtime_types` 除外）。

#### Scenario: Scheduler iterates tenants without importing the server

- **WHEN** `src/scheduler/ticker.py` 需要遍历租户
- **THEN** 它从 `src.storage.control_records` 导入 `iter_process_tenants`，不导入 `src.server.tenant_lifecycle`

#### Scenario: Config and tenancy share the bottom layer

- **WHEN** W5 完成后查看 `backend layers` 契约
- **THEN** 最底层为 `src.config : src.tenancy : src.common : src.exception_detail`，原来 4 条 `src.config.* -> src.tenancy.context` 的基线条目已删除

### Requirement: Server package is a leaf after W5

W5 完成后，`pyproject.toml` MUST 含名为 `server is a leaf` 的 `forbidden` 契约，`forbidden_modules = ["src.server"]`，`source_modules` 覆盖 `src/` 下除 `src.server` 外的全部顶层包，且 MUST NOT 有 `ignore_imports`。任何非 `src.server` 模块导入 `src.server.*`（含函数内延迟导入）MUST 使 `lint-imports` 失败。

#### Scenario: Contract passes after W5

- **WHEN** W5 提交后在 `backend/` 执行 `uv run lint-imports`
- **THEN** 输出中 `backend layers` 与 `server is a leaf` 均为 `KEPT`

#### Scenario: Any upward import into server fails immediately

- **GIVEN** `src/scheduler/ticker.py` 中新增一行 `from src.server.main import app`
- **WHEN** 执行 `uv run lint-imports`
- **THEN** `server is a leaf` 报告 `BROKEN` 并列出该导入；该边不可通过加入基线规避

### Requirement: Relocated packages declare a public surface and ship AGENTS.md

每个由本变更新建或搬入代码的包（`src/agents`、`src/tools`、`src/common`、`src/models`、`src/runtime/chat`、`src/server`）MUST 在搬入的同一提交中：（1）在被搬入的模块或包 `__init__.py` 中声明 `__all__`，内容等于该包外部实际导入的名字集合（`src/runtime/chat` 豁免此项，改为在 AGENTS.md 列出被外部导入的模块）；（2）包内有 `AGENTS.md`，固定五段：职责与边界、入口、不变量（每条附一个以 `test/` 开头且存在的测试路径）、禁止、改动政策，不超过 800 词；（3）同目录有内容为 `@AGENTS.md` 的 `CLAUDE.md`。`test/test_agents_md.py` MUST 检查以上目录齐全且所有测试锚点存在。

#### Scenario: Structure test passes for all relocated packages

- **WHEN** W8 完成后执行 `uv run pytest test/test_agents_md.py`
- **THEN** 测试通过，`REQUIRED_DIRS` 包含上述六个目录

#### Scenario: Dangling test anchor fails

- **GIVEN** `src/agents/AGENTS.md` 中某条不变量引用 `test/test_not_exist.py`
- **WHEN** 执行 `uv run pytest test/test_agents_md.py`
- **THEN** 测试失败并列出该路径与所在文件

#### Scenario: Importing a non-public name is caught

- **GIVEN** `src/scheduler/service.py` 导入 `src.agents.registry._load_agents`（不在 `__all__` 中）
- **WHEN** 执行 `uv run ruff check src`（启用 `PLC2701` 私有名导入检查）
- **THEN** 报告该导入
