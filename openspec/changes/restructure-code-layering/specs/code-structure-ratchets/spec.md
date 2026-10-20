# Spec Delta

## Purpose

把几项容易反弹的结构指标变成只减不增的自动检查：复杂度豁免数量、双键兼容读取数量、未引用的顶层函数。同时规定事件分发热点改为规则表后必须保持原有的分支顺序与贯穿语义。

## ADDED Requirements

### Requirement: Complexity exemptions only shrink

`src/` 中 `# noqa: C901` 的数量 MUST NOT 超过 `test/test_c901_exemption_ratchet.py` 记录的上限。以下 6 个函数 MUST 把圈复杂度降到 15 及以下并去掉豁免：`observe_source_event`、`TeamEventNormalizerMixin._team_event`、`stream_leader_agent_reply`、`_run_worker_turn`、`evaluate_job_feasibility`、`get_or_create_agent`。

#### Scenario: Named hotspots carry no exemption

- **WHEN** 执行 `uv run ruff check src --select C901`
- **THEN** 输出不包含上述 6 个函数名，且它们的定义行不带 `noqa: C901`

#### Scenario: New exemption fails

- **GIVEN** 有人给新函数加上 `# noqa: C901`
- **WHEN** 执行 `uv run pytest test/test_c901_exemption_ratchet.py`
- **THEN** 测试因数量超过上限而失败

### Requirement: Event rule tables preserve branch order and fall-through

把按事件类型分支的长函数改为规则表时，规则 MUST 按原代码中 `if` 的出现顺序排列；原分支以 `return` 结束的，对应处理函数 SHALL 返回"停止"；原分支不 `return`、继续执行后续 `if` 的，处理函数 SHALL 返回"继续"。重构前 MUST 先提交金标测试，重构后金标输出 MUST 逐字节一致。

#### Scenario: Observation golden output is unchanged

- **GIVEN** `test/test_observation_rules_golden.py` 在重构前生成并提交了 `test/golden/observation_rules.json`
- **WHEN** `observe_source_event` 改为规则表后重跑该测试
- **THEN** 每个事件之后的观察记录快照（去掉时间字段、ID 按出现顺序归一）与金标文件一致

#### Scenario: Team event golden output is unchanged

- **GIVEN** `test/test_team_event_rules_golden.py` 在重构前生成并提交了 `test/golden/team_event_rules.json`
- **WHEN** `TeamEventNormalizerMixin._team_event` 改为规则表后重跑该测试
- **THEN** 每条 Team outbox 事件产出的领域事件列表与金标文件一致

#### Scenario: A type handled by two branches still runs both

- **WHEN** 观察到 `HITL_REQUIRED` 事件
- **THEN** 先执行原"关闭当前生成观察"分支（`_close_open_generation`，不停止），再执行原"打开审批观察"分支（`kind="approval"`，停止），顺序与重构前相同

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
