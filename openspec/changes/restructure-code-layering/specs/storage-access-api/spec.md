# Spec Delta

## Purpose

存储层只通过公开入口被使用：租户库用 `database.transaction()`，控制面库用 `control_db.control_transaction()`。入口负责加锁、提交或回滚，并保证连接关闭；公共小工具（UTC 时间、运行事件记录）只有一个公开实现。

## ADDED Requirements

### Requirement: Callers use public transaction entry points

`src/` 中除 `src/storage/database.py` 与 `src/storage/control_db.py` 外的模块 MUST NOT 访问 `database._lock`、`database._connect`、`_control_lock`、`control_connect`，需要连接时 MUST 使用 `database.transaction()` 或 `control_db.control_transaction()`。`test/` 中的读写同样 MUST 使用公开入口；唯一例外是用 `monkeypatch.setattr(database, "_connect", ...)` 统计或注入连接的探针。

#### Scenario: Private access is detected

- **GIVEN** 某模块写了 `with database._lock, database._connect() as conn:`
- **WHEN** 执行 `uv run pytest test/test_storage_private_access.py`
- **THEN** 测试失败并列出文件与行号

#### Scenario: Tests may instrument the private connector

- **GIVEN** `test/test_permission_records.py` 用 `monkeypatch.setattr(database, "_connect", counting_connect)` 统计打开的连接
- **WHEN** 执行 `uv run pytest test/test_storage_private_access.py`
- **THEN** 该写法不被判为违规

### Requirement: Transactions close their connections

`database.transaction()` 与 `control_db.control_transaction()` SHALL 在进入时获取对应的锁并打开连接；代码块正常结束时 MUST 提交，抛出异常时 MUST 回滚并原样抛出；两种情况下都 MUST 关闭连接并释放锁。

#### Scenario: Normal exit commits and closes

- **WHEN** 在 `with database.transaction() as conn:` 中插入一行后正常退出
- **THEN** 新连接能读到该行，且对已退出的 `conn` 执行 SQL 抛出 `sqlite3.ProgrammingError`

#### Scenario: Exception rolls back and closes

- **WHEN** 在代码块中插入一行后抛出 `RuntimeError`
- **THEN** `RuntimeError` 传播到调用方，新连接读不到该行，且 `conn` 已关闭

### Requirement: One UTC timestamp helper

生成"当前 UTC 时间的 ISO 字符串"MUST 使用 `src.common.time.utc_now_iso()`。`src/` 中 MUST NOT 再定义函数体为 `return datetime.now(timezone.utc).isoformat()`（或 `datetime.now(UTC).isoformat()`）的本地函数；返回 `datetime` 对象的函数不在此列。

#### Scenario: Duplicate helper is detected

- **WHEN** 执行 `uv run pytest test/test_storage_private_access.py::test_no_local_utc_now_iso_helpers`
- **THEN** 测试通过；若有人新写 `def _utc_now(): return datetime.now(timezone.utc).isoformat()` 则测试失败

### Requirement: Run event recording has a public name

运行事件记录函数 MUST 以 `record_run_event` 为名从 `src.runtime.chat.trace_recorder` 导出；`src/` 与 `test/` 中 MUST NOT 再出现 `_record_run_event`。

#### Scenario: Other packages record run events

- **WHEN** `src/subagents/manager.py`、`src/memory/trace.py` 或 `src/runtime/governance.py` 记录运行事件
- **THEN** 它们导入 `from src.runtime.chat.trace_recorder import record_run_event`，`grep -rn "_record_run_event" backend/src backend/test` 无输出
