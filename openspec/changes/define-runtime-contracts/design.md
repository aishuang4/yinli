# Design

## Context

动机见 proposal.md 的 Why。与设计相关的现状：

- 后端协议模型在 `backend/src/server/routes/chat/projection/frame_schema.py`：`ProtocolModel(extra="forbid", populate_by_name)` 下的 `DisplayBlock`、`DisplaySnapshot`、`BlockOpenFrame`、`BlockDeltaFrame`、`BlockCloseFrame`、`SnapshotFrame`、`DoneFrame`，`RuntimeFrame` 是五者的 `TypeAlias` 联合。模型已用 `alias` 输出 camelCase，`dataVersion >= 1` 等约束已在 `Field` 上。
- 后端 Block kind 没有集中清单：12 个 kind 分散在 `projection/` 下各 projector 的 `kind="..."` 字面量里（`text`、`thinking`、`reasoning`、`tool`、`approval`、`skill`、`subagent`、`plan`、`message`、`error`、`context`、`artifact`），`ProjectionRegistry` 按事件类型而非 kind 注册。Team 的 kind 在 `team_runtime/` 下。
- 前端 `runtime-stream/protocol.ts` 是手写的类型 + 解码器，`registry.ts` 的 `BlockRegistry.register()` 按 kind 注册模块，`coreModules.ts` 注册核心 kind（含 `detached` 标记），`requireVersion()` 校验 `dataVersion`。已有 `protocol.test.ts`。
- 端到端驱动已有先例：`backend/test/test_chat_runtime_regressions.py` 用 `_FakeAgentScopeEvent` 和若干 `_Fake*Agent.reply_stream()` 直接调用 `stream_leader_agent_reply(...)`，证明不走模型也能跑通整条管线。但它的断言是点对点的，没有完整序列金标。
- 根 `AGENTS.md` 1626 词，内容按主题分段（hosted/auth、chat 链路、team 限制、memory、scheduler、skills、打包等），没有测试引用；根 `CLAUDE.md` 是它的副本而非转发。
- `scripts/quality-gates.sh` 尚不存在，由 `fix-runtime-correctness-and-quality-gates` 引入。

约束：不搬文件（搬迁属于 `restructure-code-layering`）；不改任何运行时行为；全部新增物必须在 `simplify-global-session-context` 合并前后都能工作。

## Goals / Non-Goals

**Goals:**

- 结构定义只有一份源头，且是机器可读的 JSON；两端在 CI 中与它对账，不靠人记得同步。
- 端到端金标覆盖全部运行时场景，且不 patch 管线内部，让后续搬迁和事件统一有可信的"行为未变"证据。
- 智能体指引按目录就近、每条不变量有测试锚点、结构由测试保证。

**Non-Goals:**

- 不用 schema 自动生成前端类型或后端模型（理由见 Decision 1）。
- 不定义 AgentScope → SourceEvent → RuntimeEvent 这些中间层的 schema：它们是内部形态，`unify-chat-runtime-events` 会把它们合并，现在固定只会增加那个变更的成本。
- 不重写 `protocol.ts` 或 `frame_schema.py`；只加测试和导出入口。
- 不补写找不到测试的不变量的测试；只把它们列入 `docs/invariants-without-tests.md`。

## Decisions

### 1. Schema 由后端模型导出，两端都只做"对账"而非"生成"

`contracts/*.json` 由 Pydantic `model_json_schema(by_alias=True)` 导出，再经过一个后处理器：展平 `$defs` 中的别名、把 `RuntimeFrame` 联合写成 `oneOf` + `discriminator: op`、补上 Pydantic 表达不了但 spec 要求的约束（`phase/outcome` 互斥用 `if/then`，`order` 用 `uniqueItems`）。后端测试断言"导出 == 文件"，前端测试用 ajv 断言"文件对样例的判定 == 解码器对样例的判定"。

为什么不从 schema 生成两端代码：`protocol.ts` 的解码器除结构外还做语义检查（`seq` 单调、`close` 必须对应已 `open` 的 block），生成器做不到；且生成链路会引入构建步骤，与现有 Vite/uv 流程不匹配。对账测试的成本是每次改字段要改三处（模型、文件、前端类型），但三处不一致时 CI 会指出具体差异，这正是要的效果。

为什么不用 `zod` 从前端生成再对账后端：后端是协议的生产者，`extra="forbid"` 与字段约束已经在模型上；从生产者导出比从消费者反推少一层翻译。

导出入口放在 `backend/src/server/routes/chat/projection/contract_export.py`，只被测试导入；`restructure-code-layering` W4 会把它随 `projection/` 一起搬到 `src/runtime/chat/projection/`，对账测试里的导入路径由该变更的搬迁脚本改写。

### 2. Block kind 清单需要先在后端有一个可枚举的来源

后端 kind 目前是字面量。新建 `projection/block_kinds.py`，内容是一个冻结的 `BLOCK_KINDS: tuple[BlockKindSpec, ...]`（`kind`、`data_version`、`patch_semantics`、`detached`、`owner`），各 projector 的 `kind="text"` 改为引用常量 `BLOCK_KINDS.text.kind`——这是本变更唯一触碰运行时代码的地方，属于纯换名，由现有 `test_event_stream_projection.py` 覆盖。`block_kinds.json` 从这个常量导出。Team 的 kind（`team_runtime/` 下）以 `owner: "team"` 进入同一个清单。

对账方式：后端测试用 AST 扫描 `projection/` 和 `team_runtime/` 下所有 `kind=` 关键字参数与 `DisplayBlock(kind=...)` 构造，要求不存在字符串字面量（都引用常量），再断言常量集合 == JSON。前端测试构造 `createCoreBlockRegistry()` 和 Team 注册表，取全部已注册 kind 与 `detached` 标记，断言 == JSON。

备选：只用 grep 收集字面量。不采用：grep 不知道哪些是 kind，常量化后新增 kind 只有一个入口。

### 3. 端到端金标用 fixture 回放假 Agent，输出序列归一后逐字节比较

**驱动方式**：在 `test_chat_runtime_regressions.py` 的 `_FakeAgentScopeEvent` 基础上新建 `backend/test/support/replay_agent.py`：`ReplayAgent(fixture_path)` 的 `reply_stream()` 按 JSONL 逐行 yield 事件，`delay_ms` 用假时钟推进而非真 sleep；工具执行用 fixture 里预写的结果；HITL 场景的第二段（resume / reject）是同一个 fixture 里的第二组事件，用 `{"type": "__resume__", "payload": {...}}` 分隔行标记，测试在该行处调用真实的 resume 入口。

**捕获点**：在 SSE 编码之前、Frame 模型之后——即收集 `stream_leader_agent_reply()` yield 的 RuntimeFrame（或其直接上游），再对最后一个 snapshot 或持久化的 DisplaySnapshot 读一次作为 `final_snapshot`。不走 HTTP：`TestClient` 的 SSE 反压和超时会让金标不稳定，而 `router.py` 到引擎之间没有逻辑。

**归一**：递归替换键名以 `At`/`Ms`/`_at`/`_ms`/`timestamp`/`duration` 结尾的值为 `"<normalized>"`；`^(run|block|domain|obs|trace)-[0-9a-f]{6,}$` 的 id 按首次出现顺序映射为 `<prefix>-1`、`<prefix>-2`，保持引用关系；`seq` 不归一。规则写在 `test_runtime_pipeline_golden.py` 顶部的模块 docstring。

**比较**：`json.dumps(sort_keys=True, indent=2, ensure_ascii=False)` 后逐字符串比较，失败时输出 `difflib.unified_diff` 和第一个不同 Frame 的 `seq`。每个金标文件在比较前先过 `jsonschema.Draft202012Validator`（见 spec `runtime-pipeline-golden`）。

**场景 fixture 的来源**：不手写。新增一个开发期开关 `AGENTSCOPE_EVENT_RECORD_DIR`，设置后 `stream_leader_agent_reply` 的事件入口把每个 AgentScope 事件追加到该目录的 `<run_id>.jsonl`（这是运行时代码的第二处触碰，默认关闭、不进打包配置；实现为一个可选回调，没设置时为 `None`）。用真模型在本地跑一遍 11 个场景，录得的文件人工脱敏（模型文本换成短句）后提交。`model_error`、`max_iterations_exceeded`、`cancel_mid_stream` 三个场景用已录 fixture 手工改出（在指定行插入异常事件 / 截断 / 超过 `max_iters`）。

备选：用 `test_chat_runtime_regressions.py` 里手写的 `_Fake*Agent` 扩展。不采用：手写事件容易漏掉真实 AgentScope 会发的边缘事件（空 chunk、重复 `tool_result`），金标就会对真实行为不忠实。

### 4. 根 AGENTS.md 按"归属目录"拆，每条不变量附测试锚点，找不到测试的单独列表

拆分映射（按根文件现有段落）：

| 根文件段落 | 目标目录 |
| --- | --- |
| Hosted 登录、key2api、租户隔离、密钥 | `backend/src/server/auth/` |
| Chat 链路、Event Stream V3、投影、HITL、恢复 | `backend/src/server/routes/chat/` |
| Team / Group Studio 限制、outbox | `backend/src/server/routes/chat/team_runtime/` |
| 记忆、向量、摘要 | `backend/src/memory/` |
| 定时任务、feasibility | `backend/src/scheduler/` |
| 技能目录、强制技能、磁盘刷新 | `backend/src/skills/` |
| 前端 Block 渲染、reducer、动画 | `frontend/src/features/runtime-stream/` |
| Electron 打包、校验、QuickLook | `electron/` |
| 后端通用命令、uv、测试约定 | `backend/` |

每份文件五段：职责与边界、入口、不变量、禁止、改动政策（与 `restructure-code-layering` Decision 2 同一体裁，该变更后续新增的目录只是往 `REQUIRED_DIRS` 加项）。不变量的测试锚点用 `grep -rln <关键词> backend/test frontend/src --include=*.test.*` 找，找到多个取最直接的一个；找不到的进 `docs/invariants-without-tests.md`（表格：不变量、来源段落、建议测试位置）。根文件压缩到 400 词以内，全局硬规则只留五条（中文注释、不改协议先改 `contracts/`、跨目录改动先读目标目录 AGENTS.md、不加基线要改代码、提交前跑质量门）。

`test_agents_md.py`：`REQUIRED_DIRS` 常量；每目录检查两个文件存在与 `CLAUDE.md` 内容；正则 `(backend/test/[\w/.-]+\.py|frontend/src/[\w/.-]+\.test\.tsx?)` 提取锚点并 `Path.exists()`；`len(text.split()) <= 800`（根文件 400）；根文件必须链到每个 `REQUIRED_DIRS`。这个测试不读代码，跑在毫秒级。

备选：用 `.cursor/rules` 或 `.github/copilot-instructions.md` 等工具专属格式。不采用：`AGENTS.md` 是 Codex / Claude Code / Copilot 都读的交集，`CLAUDE.md` 用转发覆盖 Claude Code 的优先读取。

### 5. 文档与契约的关系：JSON 管结构，Markdown 管语义

`docs/chat-sse-event-contract.md` 删掉 Frame / Snapshot / kind 字段表，换成指向 `contracts/` 的链接；保留并稍加整理语义规则（`seq`、恢复协议、HITL 不结束订阅、安全要求、兼容/破坏性改动分类）。`contracts/README.md` 只写"这些文件是什么、怎么重新生成、改动分类"，不重复语义。

## Risks / Trade-offs

- [Pydantic 导出的 schema 在 Pydantic 小版本升级后形状变化，对账误报] → 后处理器只保留我们关心的键（`type`、`properties`、`required`、`enum`、`const`、`oneOf`、`additionalProperties`、`minimum`、`uniqueItems`），丢掉 `title`、`description`、`$defs` 命名等易变部分；`uv.lock` 锁版本。
- [金标对投影层的每一点改动都敏感，成为改动阻力] → 这是有意的：投影输出就是用户看到的东西。`UPDATE_GOLDEN=1` 重新生成很便宜，审查者看金标 diff 就能判断行为变化是否符合预期。
- [录制 fixture 需要真模型和工具，每人环境不同] → 录制只做一次，文件提交到仓库；测试本身不需要网络。录制开关默认关闭。
- [HITL / Team 场景涉及 SQLite 状态，金标可能依赖数据库默认值] → 每个场景用 `tmp_path` 独立数据库，由现有 `conftest.py` 的租户/会话 fixture 初始化；`simplify-global-session-context` 合并后如果会话模型变化，只需要改 fixture 的初始化部分，金标不变。
- [根 AGENTS.md 拆分时丢掉规则] → 拆分前先把根文件每条规则编号写成清单（tasks 4.1），拆完逐条勾销；未勾销的进 `docs/invariants-without-tests.md`。
- [`block_kinds.py` 常量化是运行时改动] → 纯换名；`test_event_stream_projection.py` 30+ 用例覆盖全部 projector；单独一个提交。

## Migration Plan

1. 代码改动全部是新增文件或测试，不需要部署步骤；合并顺序：契约对账 → kind 常量化 → 端到端金标 → AGENTS.md 拆分，每步一个提交。
2. 若 `scripts/quality-gates.sh` 已存在，把 `pytest test/test_runtime_contracts.py test/test_runtime_pipeline_golden.py test/test_agents_md.py` 和 `npm test -- contracts.test.ts` 加入；否则在 `fix-runtime-correctness-and-quality-gates` 落地该脚本时纳入（其 tasks 已有"运行全部 pytest"，自然覆盖）。
3. 回滚：删除新增文件即可；`block_kinds.py` 常量化那个提交可单独 revert。

## Open Questions

- Team kind 是否在本变更里一并常量化，还是只进 `block_kinds.json` 而代码仍用字面量（选后者时 AST 检查对 `team_runtime/` 放宽）。不影响 spec，开工时按 `team_runtime/` 中 kind 字面量数量决定。
