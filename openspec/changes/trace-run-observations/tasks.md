# Tasks

## 1. 存储与写入内核

- [x] 1.1 新增 `trace_observations` 表（`run_id` / `kind` / `parent_id` / 起止 / input/output/metadata）及 `(run_id, started_at)`、`(project_id, started_at)` 索引，旧库 `_ensure` 升级；用存储单测验证往返与按 Run 列出
- [x] 1.2 提供 `open_observation` / `close_observation`（失败只打日志、不抛给 Chat）；`persist=false` 不落库；单测覆盖截断 input/output 与 sandbox 跳过

## 2. Runtime 开闭

- [x] 2.1 Run 开始写根 `run`（input=用户任务），终态关闭并写 output=最终回答或错误；`waiting_*` 不关闭根节点
- [x] 2.2 单 Agent：一次模型调用一条 `generation`，一次工具一条 `tool`（兄弟、parent 为根或当前 agent 步）；HITL/AskUser 写 `approval`；用 pytest 断言不出现 `TOOL_CALL_START` 级 Observation
- [x] 2.3 Skill / Subagent / Team 任务按 design 表开闭；并行 tool 或 team-task 时间可重叠；群聊 Worker 路径不写 Chat Observation（现有 group-studio 测试不回退）
- [x] 2.4 同一 Run 的 resume 追加 Observation，不新建 Run；新 send 新 Run。单测覆盖 AskUser 恢复后时间线连续

## 3. Trace API

- [x] 3.1 `GET /api/traces` 改为当前 Project 的 Chat Run 摘要：`id=chat_runs.id`，`status` 来自 Run 生命周期，带 `durationMs` / 任务摘要；用 session id 打 `GET /api/traces/{id}` 返回 404
- [x] 3.2 `GET /api/traces/{runId}` 返回该 Run 摘要 + `observations[]`，不再把 `run_events` 分页和 `messages.toolCalls` 当主 payload；产品档关 traces 仍 404 `/api/traces`，Chat `/api/chat/runs` 可用
- [x] 3.3 补 API 测试：两轮 send 两条 Trace、取消态、等待态与 Chat Run 一致

## 4. 前端与文档

- [x] 4.1 `traceTypes` 改为 Run 摘要 + Observation；列表按 Run 展示状态与总耗时；`frontend` 下 `tsc -b` 通过
- [x] 4.2 详情第一层为墙钟时间线（总条 + 可重叠时段，approval 分色）；点选第二层只渲染该步 input/output/metadata；默认选中根 `run`
- [x] 4.3 删除以 `RUNTIME_EVENT` 筛选和空 `toolCalls` 消息卡为主路径的 UI；进行中 Run 可用刷新或短轮询详情（不接 Chat SSE）
- [x] 4.4 新增 `docs` Trace 契约：Observation 类型、与 Chat Block 对应、不进时间线的审计项；`AGENTS.md` / docs 索引补一条入口

## 5. 回归

- [x] 5.1 `cd backend && uv run pytest` 覆盖新增 Trace 测试及既有 chat HITL / team / group-studio 相关用例，全过
- [x] 5.2 手工或脚本确认：Chat 送出仍只收 `RUNTIME_EVENT` Frame；Memory 页 `MEMORY_AUDIT` 横查仍可用
