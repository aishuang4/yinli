# 上下文归属与存储重构 · 发布说明

本版本不保留旧对话、旧 Workspace 和旧 Project 绑定。

启动时如果发现旧版 `agent_workstation.db`、带 `workspaces` 表的 `state.db`，或旧的 `workspaces/` 目录，会整份移到当前租户的 `backups/legacy-<时间>/`。新程序不读取这个目录，也不会自动删除它。需要查看旧对话时，用任意 SQLite 客户端打开封存目录里的数据库文件。

桌面端首次发现封存后，工作台会弹出一次提示，内容来自 `GET /api/db/legacy-notice`。确认后不再弹出。

开发机要清空当前库时，在非托管模式下执行：

```bash
yinli db reset --dev
```

该命令只删除当前 `state.db` 和还留在数据目录里的旧库文件，不删除 `backups/`。托管模式会拒绝执行。
