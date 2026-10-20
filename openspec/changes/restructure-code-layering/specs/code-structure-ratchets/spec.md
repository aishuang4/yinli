# Spec Delta

## Purpose

把几项容易反弹的结构指标变成只减不增的自动检查：复杂度豁免数量、双键兼容读取数量、未引用的顶层函数。同时要求两个事件分发热点在任何重构前先有金标测试，作为后继事件统一变更的回归基线。

## ADDED Requirements

### Requirement: Complexity exemptions only shrink

`src/` 中 `# noqa: C901` 的数量 MUST NOT 超过 `test/test_c901_exemption_ratchet.py` 记录的上限。以下 4 个函数 MUST 把圈复杂度降到 15 及以下并去掉豁免：`stream_leader_agent_reply`、`_run_worker_turn`、`evaluate_job_feasibility`、`get_or_create_agent`。`observe_source_event` 与 `TeamEventNormalizerMixin._team_event` MUST 保留豁免，注释改为 `# noqa: C901 - 待 unify-chat-runtime-events 删除`，函数体不得在本变更中改写。

#### Scenario: Named hotspots carry no exemption

- **WHEN** 执行 `uv run ruff check src --select C901`
- **THEN** 输出不包含上述 4 个函数名，且它们的定义行不带 `noqa: C901`

#### Scenario: New exemption fails

- **GIVEN** 有人给新函数加上 `# noqa: C901`
- **WHEN** 执行 `uv run pytest test/test_c901_exemption_ratchet.py`
- **THEN** 测试因数量超过上限而失败

### Requirement: Event dispatch hotspots have golden baselines before any rewrite

`observe_source_event` 与 `TeamEventNormalizerMixin._team_event` MUST 在本变更中获得提交到仓库的金标测试（`test/test_observation_rules_golden.py`、`test/test_team_event_rules_golden.py`）与金标文件（`test/golden/observation_rules.json`、`test/golden/team_event_rules.json`）。金标 MUST 在不改动这两个函数的前提下生成，并对时间字段与随机 id 归一。任何后续变更（含 `unify-chat-runtime-events`）改写这两段翻译逻辑时，金标输出 MUST 逐字节一致，除非该变更的 spec 明文改变了对应行为。

#### Scenario: Golden files are stable across runs

- **GIVEN** 金标文件已用 `UPDATE_GOLDEN=1` 生成并提交
- **WHEN** 不带 `UPDATE_GOLDEN` 连续运行两个金标测试 3 次
- **THEN** 3 次均通过，金标文件无差异

#### Scenario: Golden covers every branch

- **WHEN** 检查 `test/golden/observation_rules.json` 与 `test/golden/team_event_rules.json`
- **THEN** 前者覆盖 `observe_source_event` 中每一个 `event_type` 分支至少一次（含 `HITL_REQUIRED` 先关闭生成观察再打开审批观察的双分支顺序）；后者覆盖 `_team_event` 中每一个 Team outbox 事件类型至少一次

#### Scenario: Hotspots are left untouched in this change

- **WHEN** 本变更归档时比较 `observe_source_event` 与 `_team_event` 的函数体
- **THEN** 除文件路径迁移与豁免注释文案外与开工前逐行一致

### Requirement: Dual-key lookups only shrink

形如 `x.get("snake_case") or x.get("camelCase")` 的双键兼容读取，每个文件的数量 MUST NOT 超过 `test/test_dual_key_lookup_ratchet.py` 中该文件的基线；基线中没有的文件上限为 0。

#### Scenario: New dual-key read fails

- **GIVEN** 新文件中写了 `data.get("workspace_id") or data.get("workspaceId")`
- **WHEN** 执行 `uv run pytest test/test_dual_key_lookup_ratchet.py`
- **THEN** 测试失败，并提示在边界处用 Pydantic 别名一次性归一

#### Scenario: Reducing a count requires lowering the baseline

- **GIVEN** 某文件的双键读取从 26 处减少到 20 处
- **WHEN** 执行同一测试
- **THEN** 测试失败并要求把基线更新为 20，防止之后又涨回去

### Requirement: Unreferenced top-level code is removed

`test/test_unreferenced_code.py` 列出的顶层函数与类，经 `grep -rnw "<名字>" backend frontend/src electron` 复核确实无引用的，MUST 删除；确需保留的（例如通过字符串反射调用）MUST 写进该测试的 `ALLOWED`，并注明调用方。空包 `src/model/` MUST 删除。

#### Scenario: Cleanup leaves only allowed entries

- **WHEN** 清理完成后在 `backend/` 执行 `uv run pytest test/test_unreferenced_code.py`
- **THEN** 测试通过；仍无人引用的定义都在 `ALLOWED` 中且各有原因，`ALLOWED` 中没有已被引用或已删除的条目

#### Scenario: New unreferenced function fails

- **GIVEN** 有人在 `src/` 新增一个没有任何调用方的顶层函数
- **WHEN** 执行同一测试
- **THEN** 测试失败并列出该函数的路径与名字
