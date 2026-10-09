# 引力 YINLI 项目进展

> 更新时间：2026-10-09。本文只保留当前完成度和缺口。日期日记已删除，历史在 git。

## 在线上

- Chat 只有 `react`。单 Agent 与一次性 TeamRun 由请求的 `mode` 决定。Plan Mode 是 middleware。Subagent 是 `DelegateTask` 文件任务。旧独立 Plan / Subagent / Memory Runtime 和持久 Team 已删除。
- 展示边界是 `RUNTIME_EVENT` 与 DisplaySnapshot V4。Trace 按 Chat Run 存在 `trace_observations`。通知不进 Chat SSE。
- 记忆是时间线工具、习惯全文和专业区全文。见 `docs/memory-system-design.md`。
- 群聊工作室是独立 pane。见 `docs/group-studio-v2.md`。
- 定时任务执行者必须是 Job Agent。能力合同见 `docs/scheduled-jobs-v2-agent-activation.md`。
- 持久 Agent、Skill、MCP、HTTP 和 Job 定义以文件为准。见 `docs/file-catalog-v1.md`。
- Web 托管要求登录，按账号隔离工作区，并在配置 key2api 后使用该用户的平台模型。桌面端不登录，数据留在本机。见 `docs/architecture-overview.md`。

## 缺口

1. 桌面端还没有托管登录入口。后端能保存令牌，工作台没有表单。
2. Team、Subagent、Plan、HITL、群聊和刷新恢复没有完整的浏览器回归。
3. Artifact 没有正式生命周期。跨 Project 的浏览器隔离和 Artifact ownership 未做。
4. Electron 正式签名、公证、Windows 真机、`userData` 迁移和自动更新未完成。
5. 群聊真并行、时间旅行、群事件进 Trace 未做。Team 的 Reviewer 与跨进程恢复未做。
6. WebSearch 未做。`WebFetch` 只读取已知 URL。邮箱、微信、飞书通知仍是占位。
7. 同一邮箱若已在 key2api 绑到另一个引力用户 ID，同步返回 409，不会交出旧 Key。删除引力用户也不会删除 key2api 账号。

待办只写在 `docs/project-todo.md`。
