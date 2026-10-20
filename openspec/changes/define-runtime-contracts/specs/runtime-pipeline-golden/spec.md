# Spec Delta

## Purpose

从 AgentScope 事件序列到 RuntimeFrame 序列和最终 DisplaySnapshot 的端到端行为，由提交到仓库的 fixture 与金标文件固定。它是后续搬迁和事件统一的回归基线：管线内部怎么改都可以，输出序列不得变。

## ADDED Requirements

### Requirement: Every runtime scenario has a committed fixture and golden

仓库 MUST 在 `backend/test/fixtures/agentscope_events/` 下为以下每个场景提供一个 JSONL fixture（每行一个 AgentScope 侧事件，字段为 `type`、`payload`、可选 `delay_ms`），并在 `backend/test/golden/runtime_frames/` 下提供同名金标（含 `frames` 数组与 `final_snapshot`）：`plain_reply`、`single_tool_call`、`multi_round_tools`、`hitl_interrupt_then_resume`、`hitl_interrupt_then_reject`、`ask_user`、`cancel_mid_stream`、`max_iterations_exceeded`、`model_error`、`skill_lifecycle`、`team_run`。金标生成时 MUST 归一化所有时间戳、随机 id 与耗时字段；归一化规则 MUST 写在测试文件顶部。

#### Scenario: Golden suite passes on an unmodified tree

- **WHEN** 在未改动的代码上运行端到端金标测试
- **THEN** 11 个场景全部通过，且连续运行 3 次结果一致

#### Scenario: A behavior change surfaces as a golden diff

- **GIVEN** 投影层被改为在 `tool` Block 关闭时不再写 `outcome.status`
- **WHEN** 运行端到端金标测试
- **THEN** `single_tool_call` 与 `multi_round_tools` 失败，输出逐 Frame 的 unified diff，并指出第一个不同的 `seq`

#### Scenario: Golden regeneration is explicit

- **WHEN** 设置 `UPDATE_GOLDEN=1` 运行端到端金标测试
- **THEN** 金标文件被重写且测试通过；不设置该变量时测试永远不写文件

### Requirement: Golden tests drive the real pipeline

端到端金标测试 MUST 通过一个按 fixture 回放事件的假 Agent 驱动真实的对话流入口，经过真实的事件归一、投影和 Frame 编码，读取真实的 SQLite（临时文件）。测试 MUST NOT monkeypatch 事件归一、投影、Frame 编码或持久化路径上的任何函数；允许替换的只有模型调用、工具执行、时钟和 id 生成。所有金标中的 Frame MUST 通过 `contracts/runtime_frame.schema.json` 校验，`final_snapshot` MUST 通过 `contracts/display_snapshot.schema.json` 校验。

#### Scenario: Frames in goldens are contract-valid

- **WHEN** 对每个金标文件中的每个 Frame 和 `final_snapshot` 执行 schema 校验
- **THEN** 全部通过

#### Scenario: Semantic invariants hold in every golden

- **WHEN** 检查每个金标的 `frames`
- **THEN** `seq` 从 1 起严格递增；最后一个 Frame 是 `done`或在 `hitl_*`、`ask_user` 场景中最后一个 Frame 是带 `pending_hitl` 或 `ask_user` Block 的 `snapshot`；`done` 之前所有 `open` 都有对应 `close`；`cancel_mid_stream` 的 `done.payload.status` 为 `cancelled`；`max_iterations_exceeded` 与 `model_error` 的 `done.payload.status` 为 `error` 且含非空 `error.message`

#### Scenario: Pipeline internals are not patched

- **WHEN** 在端到端金标测试文件中搜索 `monkeypatch.setattr` 与 `mock.patch`
- **THEN** 所有被替换的目标都属于模型调用、工具执行、时钟或 id 生成；该条件本身由一个结构测试检查（白名单写在测试文件顶部）
