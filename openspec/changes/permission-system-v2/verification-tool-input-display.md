# 工具入参丢失回归验证

验证日期：2026-10-15。

## 本地证据与原因

只读检查桌面数据目录 `~/.agent-workstation/data/agent_workstation.db`：最新一轮抽查的 10 条 Bash / Write 工具 Observation 入参均为 `{}`，权限决定仍存在。没有修改本地数据库。部分后续 generation 快照仍有先前工具入参，不能据此假定所有历史调用均可恢复。

权限展示层清空了 `TOOL_CALL_DELTA`，而真实 AgentScope 2.0.5 的 `TOOL_CALL_END` 只携带调用 ID，没有完整 input。原有累计链路失去输入，Chat 的 `inputPreview`、ChatToolRun.args 和 Trace 同时为空。原展示方法还会把所有普通值替换为占位符。

## 修复与验证

- 完整参数在服务端按调用 ID 累计，JSON 完成后统一隐藏凭据。普通路径、命令、正文、查询、数字和布尔保持可查看；执行参数和授权快照不改变。
- `tool.call.updated` 经既有 projector 和 delta Frame 替换 `inputPreview`，不重复追加、不新增 SSE 类型。Team 使用 `member.tool.input`；Trace 和历史使用同一份脱敏参数。
- Chat 工具行展开可查看 JSON 入参，普通工具不再因缺少摘要而没有详情。
- 新增真实 SDK 模型输出回归，验证 end 只有 ID 时，参数仍贯穿 ChatToolRun、Trace、Snapshot 和 compact/replay；补交错参数片段与凭据脱敏回归。
- 后端全量：1135 passed、2 skipped。前端：18 files / 91 tests passed；build 通过；lint 0 errors / 2 条既有群聊 Hook warnings；git diff --check 通过。
- ego-browser 在临时组件验证页点击展开实际 ToolCallRow，确认 Bash 命令及 timeout、Read 路径及 offset/limit、HTTP URL/body 可见，Authorization 隐藏；Trace ObservationPayload 可显示相同 JSON。截图调用超时，没有截图产物。该验证是组件浏览器 QA，不替代整个权限方案的端到端验收。

## 边界

已有空历史未回填；恢复需逐调用核对留存 generation 快照，不能从脱敏权限快照凭空生成原参数。本修复不代表 OpenSpec 其余任务完成。
