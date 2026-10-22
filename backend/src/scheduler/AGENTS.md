# scheduler

`backend/src/scheduler/` 的定时任务。包：[../../AGENTS.md](../../AGENTS.md)。

- 执行者是 Job Agent（`role=job`）。它不出现在 Chat 选择器里。
- 不要把 `default` 助手改成 Job Agent。
- `system-websearch-job`（名称 `WebSearch`）是内置的。它始终带 `WebFetch` 和 `HttpRequest`。不要删除它，也不要改成 Chat Agent。
- 这个 Agent 跳过自己的 Project 绑定。它的 Skill、MCP 和 HTTP 工具仍按项目白名单裁剪。
- 它上面的 `WebFetch` 读取已知 HTTP(S) URL。这不是网页搜索。
- Job Agent 有必注入和可选注入两层工具。触发时必注入层一定在。`CheckJobFeasibility` 只认领可选层。
- 创建或修改 `agentId` 时，执行者不存在就返回 422。能力门禁开没开都一样。
- 门禁关闭时跳过完整可行性检查。执行者仍然必须存在。
- 交互式单 Agent Chat 注入 `ScheduleJob`、`ListJobs`、`PauseJob`、`ConfirmJob`、`CheckJobFeasibility`。Team 和触发执行不注入。
- 不要再加一套 CRUD 工具。
- Job 文件是 `<tenant>/agents/jobs/<id>.md`。`scheduled_jobs` 保存运行实例。
- 一次触发新建 `kind=scheduled_job` 的 Chat 会话。不复用登记时的会话。
- 触发时去掉 AskUser、Plan Mode 和 `DelegateTask`。
- 通过 `src.runtime.scheduled_chat` 启动 Run。
- 终态由 `scheduler/notifications.py` 交给 `src/notifications/`。
- 只有应用内收件箱是已实现的通道。邮箱、微信和飞书仍未实现。不要把它们报成已送达。

见 [docs/scheduled-jobs-v2-agent-activation.md](../../../docs/scheduled-jobs-v2-agent-activation.md)。
