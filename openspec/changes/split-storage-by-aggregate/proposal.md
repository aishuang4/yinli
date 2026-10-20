# Proposal

> 状态：占位。只有 proposal；specs / design / tasks 在 `restructure-code-layering` 归档后再写（届时存储访问已统一为公开事务入口，向上导入基线剩 15 条且全在存储/配置层）。

## Why

`src/storage/database.py` 有 2373 行，同时拥有会话、消息、运行、观察、Team、技能、记忆、租户等十几张表；19 个存储层以外的文件自己写 SQL，26 个文件使用存储层私有成员。"每张表只有一个主人"不成立，所以改一张表的结构要全仓找谁在读写它；`storage` 反过来导入 `catalog` 等上层包（`restructure-code-layering` 收尾时保留的 15 条向上导入基线全部在这里）。

## What Changes

- 按聚合拆分为 `src/storage/<aggregate>/{model.py,sql.py,repo.py}`（参考 DeerFlow `persistence/<agg>/` 的布局）：`sessions`、`messages`、`runs`（含 `run_events`，与 `unify-chat-runtime-events` 协调）、`observations`、`team`、`skills`、`memory`、`tenancy`、`agents`、`project_files`。每张表只出现在一个 `sql.py` 里。
- 每个聚合有一个抽象基类（`XxxStore`）与 SQLite 实现；上层只依赖抽象，测试可用内存实现。
- 存储层以外 MUST NOT 出现 `sqlite3`、`INSERT`、`SELECT` 等字样；由一个结构测试 + import-linter 契约保证。
- 向上导入基线从 15 → 0；`storage` 成为只依赖 `common`/`config` 的真正底层。
- 迁移脚本与 `schema_version` 管理集中到 `src/storage/migrations/`；表结构不变，用户数据无需迁移。

## Capabilities

### New Capabilities

- `storage-aggregates`: 每张表有且只有一个拥有者模块；存储层之外没有 SQL。

### Modified Capabilities

- `storage-access-api`（由 `restructure-code-layering` 引入）：公开事务入口保留，按聚合补充。
- `backend-module-layering`（由 `restructure-code-layering` 引入）：分层基线上限改为 0。

## Impact

- 前置：`restructure-code-layering` 已归档。可与 `unify-chat-runtime-events` 并行，但 `runs` 聚合的表结构以那边的 `run_events` 为准。
- 涉及：`src/storage/**` 全部；19 个含裸 SQL 的上层文件；26 个使用私有成员的文件；`test/` 中直接建表的 fixture。
- 不改变：SQLite 文件结构、表名、列、HTTP、用户可见行为。
