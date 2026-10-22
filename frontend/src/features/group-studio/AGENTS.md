# group-studio

`frontend/src/features/group-studio/` 的群聊工作室面板。上层：[../../../AGENTS.md](../../../AGENTS.md)。

- 这个面板放在 Chat 上。它不是 Agent 或 Team 分段，也不是标题栏的第三个工作区。
- 从 Chat 顶栏打开。房间出现时不自动开跑。
- 输入区显示停止。停止清空队列，房间保持打开。
- 不加暂停按钮，也不加结束按钮。
- 不恢复可见 Host、接谈、阶段机、工作项或确认开始。
- `kind` 以 `group_studio_` 开头的通知打开这个面板。它不打开 Chat 结果弹窗。
- 使用工作室自己的 SSE。不要把这些事件折进 Chat SSE。

领域规则：[backend/src/group_studio/AGENTS.md](../../../../backend/src/group_studio/AGENTS.md)。
