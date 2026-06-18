# 后台 Run 生命周期规划

> 状态：第一版已开始实现。目标是让 Agent/Team 执行不再依赖前端页面生命周期。

## 问题背景

早期 Chat 执行模型是“请求即执行”：前端调用 `/api/chat/send` 后保持 SSE 连接，后端在这条连接里执行 `agent.reply_stream()`。这会导致一个风险：如果用户刷新页面、关闭窗口、网络中断或浏览器取消请求，后端执行可能被取消，助手消息、Team 状态、记忆总结也可能无法完整落库。

## 目标模型

新的目标是“后台 Run + 前端订阅”：

```text
用户发送消息
  -> 后端创建 run_id
  -> 后台 task 独立执行 Agent/Team
  -> 前端通过 SSE 订阅 run 事件
  -> 前端断开只取消订阅，不取消后台执行
  -> 执行完成后消息、trace、memory 正常落库
```

## 第一版实现边界

第一版只解决进程内断线问题：

- `/api/chat/send` 创建进程内后台 run。
- 原有 Agent/Team 事件流由后台 task 消费。
- SSE 响应改为订阅后台 run 事件。
- 订阅断开不会取消后台 task。
- 新增 `/api/chat/runs/{run_id}/stream`，允许按 run 重新订阅当前进程内事件。

第一版暂不解决：

- 后端进程重启后的 run 恢复。
- 多进程/多实例之间的 run 迁移。
- run 状态持久化表。
- 前端自动发现并重连未完成 run。
- 按用户维度隔离 run 权限。

## 后续增强方向

### 持久化 run 状态

新增 `chat_runs` 表：

- `id`
- `session_id`
- `status`
- `agent_id`
- `team_id`
- `created_at`
- `updated_at`
- `completed_at`
- `error`
- `request_payload`

### 增量消息草稿

当前助手消息仍在执行结束后落库。后续可增加草稿写入：

- 文本增量定期写入草稿。
- 工具调用和 Team Activity 边执行边保存。
- 用户刷新后能看到当前已生成内容。

### 前端自动重连

Chat 页面可维护 active run：

- 发送后保存 `run_id`。
- 页面切换回来后如果 run 未完成，自动订阅 `/api/chat/runs/{run_id}/stream`。
- 如果订阅丢失，从 `run_events` 补齐历史事件。

### 取消与超时

需要新增：

- `POST /api/chat/runs/{run_id}/cancel`
- run 超时策略
- Worker 超时策略
- 前端“停止执行”按钮真正取消后台 task

## 验收标准

- 用户发送长任务后切换页面，后端仍继续执行。
- 用户刷新页面后，后台 run 不因原 SSE 连接断开而被取消。
- 任务完成后，历史消息中能看到助手最终结果。
- Team worker、工具调用、Skills、记忆总结仍正常落库。
- 后续新增自动重连后，前端能继续显示未完成 run 的实时事件。
