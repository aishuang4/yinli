# Tasks

约定：除特别说明，后端命令在 `backend/` 执行，前端命令在 `frontend/` 执行。每组结束前 `uv run --no-sync ruff check src test`、`uv run --no-sync basedpyright`、`uv run --no-sync pytest -q` 必须与开工前同样通过（既有失败集合不增不减）。本变更不搬文件，不改任何 HTTP/SSE/SQLite 行为。

## 1. 开工准备

- [ ] 1.1 从最新 master 新建分支 `feat/runtime-contracts`。验证：`git status --short` 无输出，`git branch --show-current` 输出 `feat/runtime-contracts`
- [ ] 1.2 添加开发依赖：`uv add --dev jsonschema`；`npm i -D ajv ajv-formats`。验证：`uv run --no-sync python -c "import jsonschema"` 无输出；`node -e "require('ajv')"` 无输出；`uv.lock` 与 `package-lock.json` 已更新
- [ ] 1.3 记录基线：`uv run --no-sync pytest -q -p no:cacheprovider 2>&1 | grep -E '^(FAILED|ERROR) ' | sort > /tmp/contracts_before.txt`。验证：文件存在，行数记在本任务下方

## 2. 契约文件与后端对账（Decision 1、2）

- [ ] 2.1 新建 `src/server/routes/chat/projection/block_kinds.py`：`BlockKindSpec` dataclass（`kind`、`data_version`、`patch_semantics`、`detached`、`owner`）与冻结的 `BLOCK_KINDS`，覆盖 `projection/` 下 12 个核心 kind 与 `team_runtime/` 下的 Team kind（按 design Open Question 决定 Team kind 是否常量化，并把决定写在文件顶部注释）。验证：`uv run --no-sync python -c "from src.server.routes.chat.projection.block_kinds import BLOCK_KINDS; print(len(BLOCK_KINDS))"` 输出与 `grep -rhoE 'kind=\"[a-z_]+\"' src/server/routes/chat/projection src/server/routes/chat/team_runtime | sort -u | wc -l` 一致
- [ ] 2.2 把 `projection/` 下所有 `kind="..."` 字面量改为引用 `BLOCK_KINDS`（纯换名，单独提交）。验证：`grep -rn 'kind="' src/server/routes/chat/projection/*.py` 无输出；`uv run --no-sync pytest test/test_event_stream_projection.py -q` 通过；提交前对比失败集合与 1.3 一致
- [ ] 2.3 新建 `src/server/routes/chat/projection/contract_export.py`：`export_runtime_frame_schema()`、`export_display_snapshot_schema()`、`export_block_kinds()` 三个纯函数，内含 design Decision 1 的后处理（`oneOf`+`discriminator`、`if/then` 互斥、`uniqueItems`、只保留白名单键）和 `dump(obj) -> str`（`sort_keys=True, indent=2, ensure_ascii=False` + 末尾换行）。验证：`uv run --no-sync python -c "from src.server.routes.chat.projection import contract_export as c; print(c.dump(c.export_runtime_frame_schema())[:200])"` 输出以 `{` 开头且含 `"oneOf"`；`grep -rn contract_export src --include=*.py | grep -v projection/contract_export.py` 无输出（运行时不导入）
- [ ] 2.4 新建 `test/test_runtime_contracts.py`：三个对账测试（两份 schema 逐字节、kind 集合相等），失败时打印 `difflib.unified_diff`；`UPDATE_CONTRACTS=1` 时写文件；另加 AST 测试断言 `projection/` 下无 `kind=` 字符串字面量。首次用 `UPDATE_CONTRACTS=1 uv run --no-sync pytest test/test_runtime_contracts.py -q` 生成 `../contracts/{runtime_frame.schema.json,display_snapshot.schema.json,block_kinds.json}`。验证：不带变量再跑通过；连续两次 `UPDATE_CONTRACTS=1` 后 `git status --short ../contracts` 无变化；在 `BlockOpenFrame` 临时加字段 `trace: str | None = None` 后测试失败且 diff 含 `trace`，还原
- [ ] 2.5 用 `jsonschema` 对 spec `runtime-contracts` 的反例写测试（多余字段、open 带 outcome、closed 缺 outcome、version≠4、order 重复）。验证：`uv run --no-sync pytest test/test_runtime_contracts.py -q` 通过，每个反例均被 schema 拒绝
- [ ] 2.6 新建 `../contracts/README.md`（文件说明、重新生成命令、兼容/破坏性改动分类、破坏性改动必须同步 `docs/chat-sse-event-contract.md`）。验证：`ls ../contracts` 列出 4 个文件；`grep -c '' ../contracts/README.md` 不超过 80
- [ ] 2.7 提交"契约文件与后端对账"。验证：提交前三个质量命令通过；失败集合与 1.3 一致

## 3. 前端对账

- [ ] 3.1 新建 `src/features/runtime-stream/contracts.test.ts`：用 `fs` 读 `../contracts/*.json`（路径相对 `import.meta.url`），用 ajv 编译；内置样例表（五种 Frame 正例、每种一个缺必填反例、一个多余字段反例、合法 Snapshot、order/blocksById 不一致 Snapshot）；对每个样例断言 `decodeRuntimeFrame`/`decodeDisplaySnapshot` 是否抛出 ↔ ajv 是否通过。验证：`npx vitest run src/features/runtime-stream/contracts.test.ts` 通过；临时让解码器容忍 delta 缺 `patch` 后测试失败并指出该样例，还原
- [ ] 3.2 在同一测试文件增加 kind 对账：`createCoreBlockRegistry()` 与 Team 注册表的 kind 集合和 `detached` 标记 == `block_kinds.json`。验证：测试通过；临时在 `coreModules.ts` 注册 kind `notification` 后失败并列出 `notification`，还原
- [ ] 3.3 确认 `npm test` 默认包含该文件（vitest 默认 glob）。验证：`npm test 2>&1 | grep contracts.test.ts` 有输出且通过；`npm run lint` 通过
- [ ] 3.4 提交"前端对账"

## 4. 端到端 fixture 与金标（Decision 3）

- [ ] 4.1 新建 `test/support/replay_agent.py`：`ReplayAgent(fixture_path, clock)`，`reply_stream()` 按 JSONL yield `_FakeAgentScopeEvent` 等价对象，遇 `__resume__` 行暂停并暴露 `resume_payload`；工具结果、假时钟、可预测 id 生成器一并提供。验证：`uv run --no-sync pytest test/support -q` 通过（自带一个读三行 fixture 的自测）
- [ ] 4.2 在 `stream_leader_agent_reply` 的 AgentScope 事件入口加可选录制回调：环境变量 `AGENTSCOPE_EVENT_RECORD_DIR` 存在时把每个事件追加到 `<dir>/<run_id>.jsonl`，否则回调为 `None`（运行时第二处触碰，单独提交）。验证：不设变量时 `uv run --no-sync pytest test/test_chat_runtime_regressions.py -q` 通过且失败集合不变；设变量指向 `tmp_path` 跑一个现有用例后目录下出现 `.jsonl` 且每行可 `json.loads`
- [ ] 4.3 用真模型在本地录制 8 个场景（`plain_reply`、`single_tool_call`、`multi_round_tools`、`hitl_interrupt_then_resume`、`hitl_interrupt_then_reject`、`ask_user`、`skill_lifecycle`、`team_run`），脱敏后存入 `test/fixtures/agentscope_events/<name>.jsonl`；用已录文件手工改出 `model_error`、`max_iterations_exceeded`、`cancel_mid_stream`。验证：`ls test/fixtures/agentscope_events | wc -l` 输出 11；`grep -l 'sk-\|Bearer \|@' test/fixtures/agentscope_events/*` 无输出；每个文件 `python -c "import sys,json;[json.loads(l) for l in open(sys.argv[1])]"` 无异常
- [ ] 4.4 新建 `test/test_runtime_pipeline_golden.py`：模块 docstring 写归一规则与 monkeypatch 白名单；对 11 个 fixture 参数化，用 `ReplayAgent` 驱动 `stream_leader_agent_reply`（`tmp_path` SQLite），收集 Frame、读最终 Snapshot，归一后与 `test/golden/runtime_frames/<name>.json` 比较；`UPDATE_GOLDEN=1` 写文件；比较前对每个 Frame 和 Snapshot 跑 schema 校验；另加语义不变量断言（seq 递增、open/close 配对、done 状态）。首次 `UPDATE_GOLDEN=1` 生成。验证：不带变量连续跑 3 次全通过；临时让 tool Block 关闭时不写 `outcome.status` 后 `single_tool_call` 与 `multi_round_tools` 失败且 diff 指出首个不同 `seq`，还原
- [ ] 4.5 结构测试：在 `test_runtime_pipeline_golden.py` 内用 AST 检查自身所有 `monkeypatch.setattr`/`mock.patch` 目标都在白名单（模型调用、工具执行、时钟、id）。验证：测试通过；临时加一个对 `observe_source_event` 的 patch 后失败，还原
- [ ] 4.6 录制开关不进打包：确认 `backend-server.spec` 与 `electron/` 未引用 `AGENTSCOPE_EVENT_RECORD_DIR`。验证：`grep -rn AGENTSCOPE_EVENT_RECORD_DIR .. --include=*.spec --include=*.ts --include=*.yml --include=*.json` 无输出
- [ ] 4.7 提交"端到端金标"（fixture 与 golden 一起提交）。验证：失败集合与 1.3 一致

## 5. AGENTS.md 拆分（Decision 4）

- [ ] 5.1 把根 `AGENTS.md` 每条规则编号写成清单 `/tmp/agents_rules.md`（规则、目标目录、候选测试）。验证：清单条数记在本任务下方；每条都有目标目录
- [ ] 5.2 为 design Decision 4 表中 9 个目录各写 `AGENTS.md`（五段体裁，不变量附测试路径）与 `CLAUDE.md`（内容 `@AGENTS.md`）；找不到测试的规则写入 `../docs/invariants-without-tests.md`。验证：每个文件 `wc -w` 不超过 800；5.1 清单每条都能在某份子文件或 `invariants-without-tests.md` 找到（勾销记录贴在本任务下方）
- [ ] 5.3 重写根 `AGENTS.md`（定位、目录地图链到 9 份子文件、常用命令、五条硬规则、"先读子目录 AGENTS.md"）；根 `CLAUDE.md` 改为一行 `@AGENTS.md`。验证：`wc -w ../AGENTS.md` 不超过 400；`cat ../CLAUDE.md` 输出 `@AGENTS.md`
- [ ] 5.4 新建 `test/test_agents_md.py`（`REQUIRED_DIRS` 含 9 个目录；文件存在、CLAUDE.md 内容、锚点存在、词数上限、根文件链接齐全）。验证：`uv run --no-sync pytest test/test_agents_md.py -q` 通过；在 `src/memory/AGENTS.md` 临时写入 `backend/test/test_memory_nope.py` 后失败并列出路径与行号，还原；临时把某子文件填到 801 词后失败，还原
- [ ] 5.5 `docs/chat-sse-event-contract.md`：删除 Frame/Snapshot/kind 字段表，换为指向 `contracts/` 的链接；保留语义规则。验证：`grep -n 'dataVersion' ../docs/chat-sse-event-contract.md` 只在语义说明中出现，不再有表格行；文档含 `contracts/runtime_frame.schema.json` 与 `contracts/display_snapshot.schema.json` 链接
- [ ] 5.6 提交"AGENTS.md 拆分"

## 6. 收尾

- [ ] 6.1 若 `../scripts/quality-gates.sh` 已存在，把三个后端测试文件与 `contracts.test.ts` 加入；否则在 `fix-runtime-correctness-and-quality-gates` 的 tasks.md 对应任务下补一行备注。验证：脚本存在时 `scripts/quality-gates.sh all` 退出码 0；不存在时备注已写入
- [ ] 6.2 整体验证。验证：`uv run --no-sync pytest -q` 失败集合与 1.3 一致；`npm test` 通过；`git status --short` 只有本变更文件；`git log --oneline master..HEAD | wc -l` 输出 4–5（契约、kind 常量化、前端对账、金标、AGENTS.md）
- [ ] 6.3 归档本变更：`openspec archive define-runtime-contracts`。验证：`openspec list --specs` 出现 `runtime-contracts`、`runtime-pipeline-golden`、`scoped-agent-guidance`
