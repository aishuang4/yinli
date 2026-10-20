# Proposal

## Why

Chat 的前后端契约（Event Stream V3 / DisplaySnapshot V4）目前有三份彼此独立的副本：`docs/chat-sse-event-contract.md` 的散文、后端 `projection/frame_schema.py` 的 Pydantic 模型、前端 `runtime-stream/protocol.ts` 的 TypeScript 类型与手写解码器；Block kind 的清单又分别写在后端 `projection_registry.py` 和前端 `coreModules.ts`。三者之间没有任何自动对账，任何一侧单独改字段或加 kind 都只会在运行时被 `extra="forbid"` 的校验或前端解码器拒绝。同时，从 AgentScope 事件到 RuntimeFrame 的整条管线没有端到端回归基线，既有测试大多 monkeypatch 内部函数，重构时跟着崩而不是护着。最后，根目录 `AGENTS.md`（1626 词）把所有子系统的不变量写成连续叙述堆在一处，编码智能体每次全量加载却无法判断哪条已过期。

这三件事都不依赖 `simplify-global-session-context` 或任何文件搬迁，只新增文件和测试；它们越早到位，后续 `restructure-code-layering` 的 W4 搬迁和 `unify-chat-runtime-events` 的事件统一就越有证据说明"没改坏"。

## What Changes

- 新增仓库根目录 `contracts/`，存放由后端 Pydantic 模型导出的 JSON Schema：`runtime_frame.schema.json`（五种 Frame 的联合）、`display_snapshot.schema.json`（V4）、`block_kinds.json`（kind 清单、dataVersion、patch 语义标签、是否 detached）。`contracts/` 不含代码、不依赖任何包。
- 后端新增对账测试：`frame_schema.py` 导出的 schema 必须与 `contracts/*.json` 逐字节一致；`projection_registry.py` 注册的 kind 集合必须等于 `block_kinds.json`。提供 `UPDATE_CONTRACTS=1` 重新生成，生成后的 diff 由人审阅。
- 前端新增对账测试：用 `contracts/*.json` 校验 `protocol.ts` 的 `decodeRuntimeFrame` / `decodeDisplaySnapshot` 对一组正例与反例的接受/拒绝结果与 JSON Schema 判定一致；`coreModules.ts` 注册的 kind 集合必须等于 `block_kinds.json`。
- 新增端到端 fixture 金标：`backend/test/fixtures/agentscope_events/*.jsonl` 记录 AgentScope 事件序列（普通回复、工具调用、多轮工具、HITL 中断再恢复、AskUser、取消、超最大迭代、错误、Skill 生命周期、Team 运行），`backend/test/golden/runtime_frames/*.json` 记录对应的 RuntimeFrame 序列与最终 DisplaySnapshot。测试用与 `test_chat_runtime_regressions.py` 同样的假 Agent 驱动 `stream_leader_agent_reply`，不 monkeypatch 管线内部函数。
- `docs/chat-sse-event-contract.md` 改为从 `contracts/` 引用结构定义，只保留语义规则（seq、恢复、HITL、安全）；删除与 JSON 重复的字段表。
- 根 `AGENTS.md` 按现有目录拆分：根文件压到 400 词以内（定位、目录地图、常用命令、硬规则、"进入子目录先读该目录的 AGENTS.md"）；其余内容按归属下沉到 `backend/src/server/auth/`、`backend/src/server/routes/chat/`、`backend/src/server/routes/chat/team_runtime/`、`backend/src/memory/`、`backend/src/scheduler/`、`backend/src/skills/`、`frontend/src/features/runtime-stream/`、`electron/`，每份改写为"不变量 + 钉住它的测试"体裁，不超过 800 词；每个目录配一行 `CLAUDE.md`（`@AGENTS.md`）。找不到对应测试的不变量汇总成 `docs/invariants-without-tests.md`，作为后续补测试的清单。
- 新增结构测试 `backend/test/test_agents_md.py`：指定目录必须有 `AGENTS.md` 与 `CLAUDE.md`；`AGENTS.md` 中引用的测试路径必须存在；单文件不超过 800 词。`restructure-code-layering` 后续只往 `REQUIRED_DIRS` 加目录。
- 本变更不改变任何 HTTP 接口、事件字段、Block kind、数据库结构或用户可见行为；不搬迁任何源文件。

## Capabilities

### New Capabilities

- `runtime-contracts`: Chat 实时协议（RuntimeFrame、DisplaySnapshot、Block kind 清单）以 `contracts/` 下的 JSON Schema 为唯一结构定义，后端模型、前端解码器和 kind 注册表在 CI 中与它对账。
- `runtime-pipeline-golden`: 从 AgentScope 事件序列到 RuntimeFrame 序列的端到端行为由提交到仓库的 fixture 与金标文件固定，覆盖全部运行时场景。
- `scoped-agent-guidance`: 编码智能体的指引按目录就近放置，每条不变量指向钉住它的测试，结构由测试保证。

### Modified Capabilities

（无。现有 `group-studio`、`skills` 两个主 spec 的要求不变。）

## Impact

- 前置条件：无。与 `harden-desktop-and-hosted-access`、`fix-runtime-correctness-and-quality-gates` 阶段 A 可并行；不依赖 `simplify-global-session-context`。后继：`restructure-code-layering`（W4 用本变更的金标做额外验证；其 `test_agents_md.py` 与根 AGENTS.md 拆分由本变更提供）、`unify-chat-runtime-events`（以本变更的金标为回归基线）。
- 新增：`contracts/{runtime_frame.schema.json,display_snapshot.schema.json,block_kinds.json,README.md}`；`backend/src/server/routes/chat/projection/contract_export.py`（只做导出，不被运行时导入）；`backend/test/test_runtime_contracts.py`、`backend/test/test_runtime_pipeline_golden.py`、`backend/test/test_agents_md.py`；`backend/test/fixtures/agentscope_events/*.jsonl`、`backend/test/golden/runtime_frames/*.json`；`frontend/src/features/runtime-stream/contracts.test.ts`；8 份子目录 `AGENTS.md` 与 `CLAUDE.md`；`docs/invariants-without-tests.md`。
- 修改：根 `AGENTS.md`（压缩）、根 `CLAUDE.md`（改为一行转发）、`docs/chat-sse-event-contract.md`（删重复字段表）、`backend/pyproject.toml`（新增 `jsonschema` 开发依赖）、`frontend/package.json`（新增 `ajv` 开发依赖）、`scripts/quality-gates.sh`（若已存在则加入两个对账测试；否则由 `fix-runtime-correctness-and-quality-gates` 落地时纳入）。
- 不影响：运行时代码路径、HTTP、SSE、SQLite、Electron、打包。对账测试只在测试与 CI 中运行。
