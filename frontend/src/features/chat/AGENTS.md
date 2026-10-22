# chat

`frontend/src/features/chat/` 的 Chat 界面。上层：[../../../AGENTS.md](../../../AGENTS.md)。

- `pages/ChatPage.tsx` 管页面外壳。输入框、消息列表和 Skill 选择留在本目录。
- 整理非 Chat 表单时不改这里的文件。
- 斜杠 Skill 命令留在用户原文里并高亮。最多 5 个。
- 不加 Skill 查看器。后端读取的是和定时任务同一条消息。
- 产物从预览 API 在右侧打开。切换会话或新建对话时关闭预览。
- macOS 和 Windows 嵌入只读的系统预览。Linux 和浏览器只提供下载。
- 嵌入失败时，Word 回退 `docx-preview`，Excel 回退抽出的表格。
- 不订阅 [docs/chat-sse-event-contract.md](../../../../docs/chat-sse-event-contract.md) 以外的事件。
- token 流式输出时减少整棵树重渲染。
- Team 模式沿用选中的 Agent 作为 Leader，并隐藏 Agent 选择器。不加 Team、Member 或 Leader 选择器。

流式规则：[../runtime-stream/AGENTS.md](../runtime-stream/AGENTS.md)。
