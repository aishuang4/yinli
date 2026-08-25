# 消息通知

> 状态：已落地。应用内通知在线；邮箱 / 微信 / 飞书仅预留通道，未接入投递。

定时任务完成后通过通知分发器推送消息。当前只实现产品内 inbox；外部通道实现时只需新增 `NotificationChannel`，不必改调度器或 Chat Runtime。

## 边界

- 不新增 Chat SSE 事件，不写 `JOB_EVENT`，不改 `docs/chat-sse-event-contract.md`。
- 应用内推送走独立 `/api/notifications/stream`。
- 调度器只构造 `NotificationMessage`，不直接写 `user_notifications`。
- 邮箱、微信、飞书可在设置页预填配置，但不能启用以免用户误以为已经投递。

## 模块

```text
src/notifications/protocol.py      NotificationChannel
src/notifications/dispatcher.py    dispatch_notification()
src/notifications/channels/in_app.py
src/notifications/channels/external.py   email / wechat / feishu 占位
src/notifications/settings.py      json_store system_settings/notifications
src/notifications/hub.py           进程内 SSE 订阅
src/notifications/inbox.py         列表 / 已读
scheduler/notifications.py         Job 终态适配器
routes/notifications.py            REST + SSE
```

新增外部通道的步骤：

1. 在 `CHANNEL_CATALOG` 登记 spec，并把 `implemented` 设为 true。
2. 实现 `NotificationChannel.deliver()`。
3. 在 `channels/__init__.py` 注册实例。
4. 密钥走 `secret_store`，不要写入 `json_store`。

## API

```http
GET    /api/notifications?projectId=&unreadOnly=&limit=&before=
GET    /api/notifications/unread-count?projectId=
GET    /api/notifications/settings
PUT    /api/notifications/settings
GET    /api/notifications/stream?projectId=
POST   /api/notifications/{id}/read
POST   /api/notifications/read-all?projectId=
```

SSE 事件名：`snapshot`、`notification`、`read`、`read_all`。心跳为注释行 `: ping`。

## 前端

Chat 工作台顶栏设置按钮左侧是铃铛。点击打开当前 Project 的定时任务通知；未读徽标来自独立 SSE，断开后 15s 轮询 unread-count。点击带 `jobId` 的通知打开运行结果弹窗，页脚可跳转到定时任务详情。通道配置在 Settings 页「消息通知」卡片。

完成时的即时提醒不走 Chat SSE：

- Web：右上角应用内提醒框（`NotificationToastHost`），约 8 秒后自动消失，点击打开同一结果弹窗。
- Electron 客户端：主进程 `Notification` 弹出系统通知；点击后聚焦窗口并打开结果弹窗。系统通知不可用时回退到应用内提醒框。
- SSE 由 `NotificationToastHost` 全局订阅一次，不依赖 Chat 铃铛是否挂载。
