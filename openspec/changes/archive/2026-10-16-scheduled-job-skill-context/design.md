# Design

## Context

当前 scheduled firing 通过 `build_scheduled_run_context()` 组装 `used_skills` 和 `skill_paths`，然后由 `react_runtime` 创建 ReAct Agent。`react_runtime` 已经支持 `activate_forced_skills()`，Skill Context middleware 也能在压缩、截断和实例重建后恢复 `<activated-skill>`，但 scheduled context 没有设置 `forced_skills`，因此首轮模型只看到 Skill 目录，仍可能临时调用 `Skill` 工具或完全跳过说明书。

Job 的 `skill_ids`、能力合同的 `skill_ids`、Agent profile 的 Skills 和 Project resource binding 目前由不同路径解析。数据库的 Skill catalog `content` 也不是包内说明书的唯一权威来源；权威正文必须来自 Skill 包中的 `SKILL.md`。现有 Job 状态机、ticker、firing 记录、通知、Job Agent 校验和 Context Governance 都应继续复用。

## Goals / Non-Goals

**Goals:**

- 让 Job 在确认时冻结 required Skill 的可验证版本，在每次 firing 前 fail-closed 校验。
- 让第一次模型调用前就能看到冻结的 `SKILL.md`，并复用已有会话级 Skill Context 生命周期。
- 让 Job、能力合同、Project binding、Agent 权限和工具 denylist 使用一致的资源集合。
- 让每次 firing 的 Context policy、预算和 Skill hash 可审计、可测试、可恢复。
- 为旧 Job 提供明确的兼容默认值，不把 Agent 当前全量 Skills 静默升级为 Job 能力。

**Non-Goals:**

- 不恢复 `runtime.kind=scheduled`，不新增独立 Job Runtime；Job 继续走 `react` + `single_agent`。
- 不把所有 Skill 正文写入 system prompt，不改 Chat 的按需加载语义。
- 不把 `references/`、`scripts/`、MCP 或网络权限随正文预加载，不改变现有权限/审批模型。
- 不把每次 firing 的完整对话跨周期继承；跨 firing 的结果/摘要策略只提供有界扩展点。
- 不改变 script Job、ticker 调度算法、通知通道或 Artifact ownership。

## Decisions

### 1. 使用 Job 级冻结 Skill manifest，保留旧字段作为兼容投影

在 `ScheduledJob` 增加 `required_skills`（冻结 manifest 列表）和 `context_policy`。每一项包含：`skill_id`、`source_scope`、`source_path`/package locator、完整 `content_sha256`、正文字符数/估算 token、`frozen_at`。现有 `skill_ids` 保留作为 API/文件定义的向后兼容输入和快速索引；装配前按“Job `skill_ids` 顺序，再追加 capability contract `skill_ids`，去重保序”生成 canonical 集合，并要求 manifest 覆盖 canonical 集合。

选择显式 manifest 而不是只存 hash 的原因是 firing 需要知道从哪个包读取正文、如何审计来源和如何处理旧数据。hash 使用 `SKILL.md` 原始 UTF-8 文件字节计算（不 `strip()`、不只取 SQLite `content`），使用完整 SHA-256；不改变现有 external Skill ID 的生成规则。只在审计中记录路径和 hash，不把正文落入 Job 行或 firing 记录。

### 2. 确认时冻结，运行时重新校验

`create_job` 只接受用户显式给出的 Skill ID 和 Context policy，写入 pending confirmation。`confirm`、编辑后重新确认和 resume 的资源检查统一调用冻结流程：

1. 检查 Project active、执行 Agent 存在且满足 Job Agent 角色要求。
2. 合并 Job 与 capability contract 的 Skill ID，按 Project binding、安装/启用状态、来源可读性解析。
3. 从包内 `SKILL.md` 计算 manifest 和 hash；发现缺失、空正文、不可读或 Project 不允许时返回 422/暂停，不生成半份 manifest。
4. 根据 Skill 元数据检查 `scripts/` 或 `shell.execute` 标签是否要求 Bash；沿用 `frozen_skills_require_bash` 和 unattended permission 检查。
5. 原子保存 `required_skills`、`context_policy`、必要的 tool scope 和 confirmed timestamp。

运行时不能只信 confirm 的结果：`build_scheduled_run_context` 在创建 Agent 前按 manifest 重新读取并比较 hash、enabled、binding、source path 和 Job Agent 资格。版本漂移使用专用错误码（建议 `skill_version_changed`），资源消失使用 `skill_unbound` / `skill_missing`，预算超限使用 `skill_context_budget_exceeded`。这些错误写入 firing 并经现有 failure policy 处理。

保留 Agent profile 权限边界：Job 明确冻结 Skill 只代表该 Job 的 required capability，不因 `availableToAllAgents` 自动添加；仍必须通过 Project binding、Job Agent 资格和现有资源/工具权限。若实现选择继续要求 Agent profile 的 Skill 绑定，必须在 confirm 时冻结该授权并在变更时走重新确认，不能到期静默丢 Skill。

### 3. 复用现有 forced Skill 和 middleware，不改 system prompt

scheduled context 解析完成后设置 `ChatRunContext.forced_skills = required_skill_objects`，并把这些对象放入 `used_skills`/`skill_paths`。`react_runtime` 已在 Agent 创建和会话状态恢复之后调用 `activate_forced_skills()`；实现应保证它在首个 `UserMsg`/模型调用之前执行，并让 `build_context_governance_middlewares(skill_context=True, hard_stop=True)` 继续负责压缩、重建和 `session_context_state` 恢复。

新增一个稳定的 scheduled Skill Context prompt addendum（或复用 `_build_skill_context_prompt()` 的规则部分），只说明 `<activated-skill>` 的语义、hash 和按需读取引用文件的规则；正文作为 HintBlock/context message 注入，不拼进 `system_prompt`。这样保留 Agent runtime cache 的 system prompt key 和供应商 prompt cache，也避免每轮重复把正文当系统指令发送。

### 4. Context policy 默认 fresh，跨 firing 只传有界结果

`fresh` 是唯一默认且向后兼容的策略：每次 firing 使用新 session，保留当前 Job prompt、当前文件输入、冻结 Skill Context、Project/Workspace 说明和允许的 memory recall，不读取上一 firing 的 chat/tool history。`previous_result` 和 `rolling_summary` 可以作为同一 manifest 的受限变体，但本次实现只定义数据接口和长度上限，不能隐式开启；它们只读取上一终态的最终 assistant 文本或受限摘要，不复制 `state.context`、工具输出、Skill 正文或审批事件。

`context_policy` 变更加入 `_RECONFIRM_FIELDS`，修改后 Job 回到 pending confirmation。每次 firing 的 policy 和实际注入来源写入审计 manifest，避免用户以为 fresh 却继承旧会话。

### 5. 预算使用同一 token 计数源，超限 fail closed

在 Skill manifest 中保存字符数用于 UI/诊断，但实际预算用现有 AgentScope `count_tokens` 同源算法（包含工具 schema 与保留区）。定义单 Skill 上限、required Skill 总上限和 firing 可用 Context 上限；预算检查发生在 Agent 创建/模型调用前。

不能复用 Chat middleware 当前“超预算淘汰最早已启用 Skill”的行为来处理 required Skill：普通 Chat 可以重新调用 Skill，Job required Skill 被淘汰会改变任务语义，因此必须报错要求拆分/缩短。`references/` 与 `scripts/` 不计入预加载正文，只有实际 Read/Bash 时才进入工具结果预算。

### 6. 审计放入既有 firing manifest 和 trace，不新建领域事实表

继续使用 `scheduled_job_runs.error_code/error_message/output_manifest` 作为 firing 事实源，在 `output_manifest.skillContext` 保存：canonical Skill IDs、hash、校验结果、policy、每项/总量预算、preloaded 时间和失败阶段；不保存 Skill 正文。运行 trace 的 payload 记录同样的 hash/预算摘要，正文只存在 AgentScope session context。这样不新增表，也能让 Jobs 页和通知读取稳定字段；若启动失败，先写失败 manifest 再结束 firing。

### 7. 统一 Job 与 capability contract 的资源检查

把 canonical Skill 合并和 manifest 校验放入 scheduler 可调用的纯模块，避免 `scheduler` import `src.server.routes.*`。`feasibility`、`assert_contract_satisfiable`、`firing_toolkit`、Project binding 变更暂停逻辑和 scheduled context 都调用该模块或其无副作用函数。`firing_toolkit` 仍只负责 MCP/HTTP/knowledge/tool scope；Skill 校验失败不得被 `filter_claimed` 或 `denylist` 路径吞掉。

### 8. 数据迁移和 API 兼容

SQLite 用现有 `_ensure_column()` 增加 JSON 文本列（建议 `required_skills`、`context_policy`）；旧行默认 `[]` 和 `fresh`。Pydantic/API/Job Markdown serializer 允许旧定义没有 manifest，读取时保留 `skill_ids`；首次 confirm/reconfirm 生成 manifest。已有 active Job 不在启动迁移时自动暂停或自动扩容，只在下一次 firing 进行校验；如果无法建立 manifest，按 `skill_metadata_missing` 失败并通过 failure policy 提醒用户。

Job definition 文件与 SQLite 使用同一序列化字段，写文件失败仍遵循现有日志策略，但不能在数据库成功确认后返回一个看似已冻结却没有 manifest 的内存对象。更新/删除 Skill、Project binding 或 Job Agent 时，暂停匹配 Job 的索引必须同时检查 `required_skills` 和旧 `skill_ids`。

## Risks / Trade-offs

- **[Skill 正文很大，Context 成本上升]** → 单 Skill/总量/保留区预算在首轮前硬失败；UI 和 firing manifest 给出具体 Skill 大小，要求拆分职责。
- **[SKILL.md 在确认后被改写]** → 运行时完整 SHA-256 校验；版本漂移专用错误，不自动刷新 hash，用户必须重新确认。
- **[SQLite content 与包内文件不一致]** → 以包内原始 `SKILL.md` 为唯一正文和 hash 来源；catalog 元数据只用于 ID、名称和 enabled/binding 检查。
- **[Agent profile 或 Project binding 变更导致 Job 突然失败]** → confirm/resume 预检查、资源变更主动暂停、firing 再校验，并在错误中指向重新确认动作。
- **[把正文放进 system prompt 破坏缓存]** → 代码审查和测试明确断言 system prompt 不含 Skill body，只含稳定规则；正文只能出现在 `<activated-skill>` Context。
- **[恢复时 middleware 淘汰 required Skill]** → Job forced activation 使用独立的 required 标记；预算不足直接失败，不沿用普通 Chat 的 oldest-skill eviction。
- **[Capability contract 漏合并]** → canonical 合并函数在 create/confirm/firing 三个入口共享，增加“contract 有 Skill、job.skill_ids 为空”的回归测试。
- **[Skill 指令诱导越权]** → 预加载只影响模型上下文，不改变 toolkit、filesystem、network、MCP、approval 和 denylist；增加脚本/外呼拒绝测试。
- **[旧 Job 在迁移后行为突然改变]** → 默认 fresh、空 manifest，不把 Agent Skills 自动复制进 Job；旧行首次 firing 失败时给出 metadata-missing，而不是隐式升级。
- **[重试造成重复副作用]** → Skill Context 校验必须发生在模型调用前；失败不会创建有效 chat run，现有 firing 幂等键和 failure policy 继续负责重复触发控制。

## Migration Plan

1. 先发布只读兼容模型、SQLite 列和序列化字段；对旧 Job 只展示 `fresh` 与待生成 manifest 状态，不改变 ticker。
2. 发布 confirm/reconfirm 冻结逻辑和 Job/contract canonical merge；新确认的 Job 必须写 manifest。
3. 发布 firing 前校验、forced activation、预算和审计；旧 active Job 首次执行按兼容策略校验，失败通知并沿既有 pause/keep-schedule 策略处理。
4. 发布资源变更暂停索引、API/UI 展示和完整测试；观察一段时间后再考虑是否把 metadata-missing 的旧 Job 批量标为需要确认。

回滚时保留新列和 manifest 数据，关闭预加载校验开关只能作为临时运维措施；不得通过回滚让 Job 静默忽略已经确认的 required Skill。代码回滚后旧读取逻辑仍可使用 `skill_ids`，因此不会破坏旧 Job 行。

## Open Questions

- `previous_result` 与 `rolling_summary` 的具体产品入口和默认长度可以在本变更实现后单独设计；本变更只落地 `fresh` 及其数据契约，不依赖该扩展。
