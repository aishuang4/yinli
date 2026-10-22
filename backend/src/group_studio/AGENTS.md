# group_studio

`backend/src/group_studio/` 的群聊工作室领域。包：[../../AGENTS.md](../../AGENTS.md)。

- 这不是 Chat 的第三种模式。不要补进 `POST /api/chat/send`、`RUNTIME_EVENT` 或 `teams` 表。
- 路由在 `/api/group-studio`。SSE 事件名用 `GROUP_*`。不保存 `DELTA`。
- SQLite 的 `group_*` 表是事实源。一个 Chat 会话最多一间 `open` 或 `paused` 的房间。
- 对外状态是 `open`、`paused`、`done`。`deleted` 只留在内部。
- 第一条人类消息创建房间。不自动开跑。不显示 Host、接谈步骤或阶段机。
- 不用 `gstu_local_*` id 调用 API。
- `POST /messages` 的 `mentions` 非空时，按名单顺序让这些成员发言。都无法发言时返回 `no_eligible_speaker`。
- `mentions` 为空时，未静音的 Worker 按 `joinedAt` 升序各说一轮，然后停下。
- `maxParallelWorkers` 保持为 1。
- `default`、Job Agent 和 `group-studio-host` 不入座。
- Worker 工具只有 `group_post_message`。权限是 `PermissionMode.BYPASS`。不抄 Chat Agent 的审批设置。
- 不写 Chat 的 `PENDING_INTERACTIONS`。`group_approvals` 是兜底。`WORKER_SHELL_BLACKLIST` 仍然生效。
- 界面没有暂停和结束。停止只清空队列，房间保持 `open`。删除把房间标成 `deleted`。
- `close_studio(confirm=True)` 标成 `done`，并由 `archive_memory.py` 归档记忆。记忆归第一个 Worker。Host 不写记忆。
- `kind` 以 `group_studio_` 开头的通知打开工作室。它们不进 Chat SSE，也不进结果弹窗。
- `confirm-intake`、`confirm-restatement`、`start-execution`、`start-review` 返回 410。
- 开关是 `json_store` 的 `system_settings/group_studio`。默认 `enabled` 为 true。
- Chat 通过 `session_studio.py` 知道房间。它不导入本包。
- `rooms.py` 管进群、读取、暂停、结束、置顶和改模型。
- `members.py` 管拉人、静音和移出。
- `speaking.py` 管人类发言、打断、重跑和系统提示。
- `artifacts.py` 登记产物。目录在 `files.py`。
- `worker_spec.py` 构造 Worker。`worker_runtime.py` 跑一轮。

见 [docs/group-studio-v2.md](../../../docs/group-studio-v2.md) 和 [docs/group-studio-event-contract.md](../../../docs/group-studio-event-contract.md)。
