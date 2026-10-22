# storage

`backend/src/storage/` 的 SQLite。包：[../../AGENTS.md](../../AGENTS.md)。

- `engine/` 只封装连接、锁、事务、`ensure_db`、schema、封存和备份。用 `RLock` 打开同步的 `sqlite3`。不要把单个调用改成 `aiosqlite`。
- 业务表读写放在 `records/`，例如 `records/scheduled_job_records.py`。不要往 `engine/database.py` 加查询。
- `config/` 放配置。`config/json_document_store.py` 读写 `json_store`。`config/agent_defaults.py` 是 Agent 默认值。模型配置在 `USER_DATA_ROOT/models.json`。
- `control/` 是托管控制面，保存账号、租户和平台审计。桌面模式没有这份库。
- Agent 正文由 `src.catalog.loader` 写入。`engine/database.py` 不写 profile。
- 加列走 `engine/database_schema.py` 的 `reconcile_columns()`。
- 不恢复 `_ensure_column`、`migrate_json_*_file`、`migrate_agents`、`assign_owners` 或 `schema_migrations`。
- 新库从 `schema_version = 1` 开始。
- Project 使用 `slug`。启动时创建 `slug=default`。没有 `workspaces` 表，也没有 `project_resource_bindings` 表。
- 发现旧库或旧的 `workspaces/` 目录时，整份封存到 `backups/legacy-<ts>/`。封存后不再读取。成功后写 `backups/legacy-notice.json`。
- 桌面命令 `yinli db reset --dev` 删除当前 `state.db`，保留 `backups/`。托管模式拒绝这条命令。
- 技能目录不在 SQLite。扫描缓存在 `src/skills/registry.py` 的 `cached_skill_records()`。写入技能包用 `write_skill_packages()`。
- Chat Trace 步骤在 `trace_observations`，按 `run_id` 查询。`run_events` 是审计日志，不是 Trace 时间线。
- 群聊步骤不写入 `trace_observations`。
- 路径来自 `src/config/runtime_paths.py`。可写数据留在租户根目录下。

见 [docs/context-storage-refactor.md](../../../docs/context-storage-refactor.md) 和 [docs/trace-run-observations.md](../../../docs/trace-run-observations.md)。
