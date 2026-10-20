# Spec Delta

## Purpose

Chat 实时协议的结构定义只有一份源头：仓库根目录 `contracts/` 下的 JSON 文件。后端模型、前端解码器和两端的 Block kind 注册表都在测试中与它对账，任何一侧单独改动结构都在提交前失败，而不是在运行时被拒。

## ADDED Requirements

### Requirement: Contracts directory is the single structural source of truth

仓库根目录 MUST 存在 `contracts/` 目录，含 `runtime_frame.schema.json`（RuntimeFrame 五种形状的联合 JSON Schema，draft 2020-12）、`display_snapshot.schema.json`（DisplaySnapshot V4）、`block_kinds.json`（每个 kind 的 `kind`、`dataVersion`、`patchSemantics`（`text-append` | `shallow-merge` | `tool-merge` | `activity-merge`）、`detached`（布尔）、`owner`（`core` | `team`））与 `README.md`。这些文件 MUST NOT 包含可执行代码，`contracts/` MUST NOT 被任何运行时模块导入或读取。Schema 对所有对象 MUST 声明 `additionalProperties: false`，与后端 `extra="forbid"` 一致。

#### Scenario: Schemas reject an unknown top-level field

- **GIVEN** 一个合法的 open Frame 额外带有字段 `"mask": {}`
- **WHEN** 用 `contracts/runtime_frame.schema.json` 校验
- **THEN** 校验失败，错误指向 `mask`

#### Scenario: Snapshot schema enforces the documented invariants that are expressible

- **GIVEN** 一个 `phase="open"` 且带 `outcome` 的 Block
- **WHEN** 用 `contracts/display_snapshot.schema.json` 校验
- **THEN** 校验失败；同样，`phase="closed"` 缺 `outcome`、`version` 不为 4、`order` 含重复项都失败

### Requirement: Backend models are reconciled against the contracts in CI

后端 MUST 提供一个从协议 Pydantic 模型导出 JSON Schema 的入口，导出结果经确定性序列化（键排序、两空格缩进、末尾换行）后 MUST 与 `contracts/runtime_frame.schema.json`、`contracts/display_snapshot.schema.json` 逐字节一致；后端投影注册表实际注册的 kind 集合 MUST 等于 `block_kinds.json` 中的 kind 集合。对账测试 MUST 是默认 `pytest` 集合的一部分；设置 `UPDATE_CONTRACTS=1` 时测试改为重新生成文件并通过，生成的差异由代码审查审阅。

#### Scenario: Adding a field on the backend without updating contracts fails

- **GIVEN** `BlockOpenFrame` 新增了一个可选字段 `trace`，而 `contracts/` 未更新
- **WHEN** 执行后端对账测试
- **THEN** 测试失败，输出两份 schema 的 unified diff，并提示用 `UPDATE_CONTRACTS=1` 重新生成

#### Scenario: Registering a kind on the backend only fails

- **GIVEN** 后端注册表新增 kind `notification`，`block_kinds.json` 未更新
- **WHEN** 执行后端对账测试
- **THEN** 测试失败并列出多出的 kind 名

#### Scenario: Regeneration is deterministic

- **WHEN** 连续两次以 `UPDATE_CONTRACTS=1` 运行对账测试
- **THEN** 第二次运行后 `git status` 显示 `contracts/` 无变化

### Requirement: Frontend decoders and kind registry are reconciled against the contracts in CI

前端 MUST 有一个测试读取 `contracts/*.json`，对一组固定的正例与反例（至少：五种 Frame 各一个正例、每种至少一个缺必填字段的反例、一个多余字段反例、一个合法 Snapshot、一个 `order` 与 `blocksById` 不一致的 Snapshot），断言前端解码器的接受/拒绝结果与 JSON Schema 校验结果逐条一致；前端 Block 注册表的 kind 集合与 `detached` 标记 MUST 等于 `block_kinds.json`。该测试 MUST 是 `npm test` 的一部分。

#### Scenario: Frontend accepts what the schema rejects

- **GIVEN** 前端解码器被改为容忍 `op="delta"` 缺少 `patch`
- **WHEN** 执行前端对账测试
- **THEN** 测试失败，指出该反例在 schema 下被拒而解码器接受

#### Scenario: Frontend registers a kind the contract does not know

- **GIVEN** `coreModules` 新增 kind `notification`，`block_kinds.json` 未更新
- **WHEN** 执行前端对账测试
- **THEN** 测试失败并列出多出的 kind 名

### Requirement: Contract changes are classified and documented

`contracts/README.md` MUST 列出哪些改动是兼容的（新增 kind、新增可选字段、新增 `dataVersion`）、哪些是破坏性的（删改 `op`、删改必填字段、改变 `patch` 语义、删除 kind），并规定破坏性改动 MUST 附带 `docs/chat-sse-event-contract.md` 的对应更新与前后端同一次合并。`docs/chat-sse-event-contract.md` MUST NOT 再重复 JSON 里已有的字段表，只保留语义规则。

#### Scenario: Semantics doc references contracts instead of duplicating

- **WHEN** 查看 `docs/chat-sse-event-contract.md`
- **THEN** Frame 和 Snapshot 的结构部分链接到 `contracts/` 下的文件，文档中不再出现 kind 字段表
