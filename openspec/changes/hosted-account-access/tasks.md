# Tasks

## 1. 账号存储与模式配置

- [x] 1.1 在 `backend/src/config/settings.py` 增加 `serve_mode`、管理员邮箱、SMTP 和邮件模式；默认 `desktop`。补测试：`hosted` 在非 log 模式且缺少 SMTP 时启动失败，`desktop` 不因此失败
- [x] 1.2 在 `backend/src/storage/database.py` 用现有启动迁移创建 `auth_users`、`auth_sessions`、`auth_email_codes`；会话表含 `client_kind` 与 `last_seen_at`。补测试：空库启动后三张表存在，令牌与验证码只保存哈希
- [x] 1.3 增加 argon2id 依赖和哈希助手。补测试：正确密码通过、错误密码失败，库里读不到明文密码

## 2. 注册、登录与托管门禁

- [x] 2.1 实现注册、验证、登录、退出、当前用户、忘记密码、重置密码和心跳。Web 登录写 HttpOnly Cookie，桌面登录在响应体返回令牌。补测试：待验证不能登录、重复邮箱响应与新注册相同、重置密码后旧会话失效
- [x] 2.2 `hosted` 对白名单以外的工作台请求要求有效会话，回环来源不能代替会话；`desktop` 保持 `LocalAccessMiddleware` 且本机对话不要求登录。扩展 `backend/test/test_local_access.py` 覆盖这两条
- [x] 2.3 公开 `GET /api/auth/config` 返回当前 `serveMode`，不返回密钥。补测试：两种模式的返回值与进程配置一致

## 3. 管理员与在线状态

- [x] 3.1 实现管理员用户列表、停用和恢复。环境变量中的邮箱在账号可登录后成为管理员；注册请求不能自设管理员；不能停用自己。补测试覆盖 403、自停用失败和停用后会话失效
- [x] 3.2 心跳、已认证请求和 SSE 活动更新 `last_seen_at`。列表把 90 秒内的未过期会话标为在线，并聚合 `web` 与 `desktop`。补测试：双端在线、超时离线仍保留最近活跃时间
- [x] 3.3 前端增加仅管理员可见的用户页：汇总、表格、在线和停用操作。用组件测试或页面测试确认普通用户看不到入口，管理员停用会调用对应接口

## 4. Web 数据按账号隔离

- [x] 4.1 为 Workspace、Project、Chat、群聊和定时任务补 `owner_user_id`，托管查询和新建都绑定当前用户；跨用户标识返回 404。补测试：两个用户的列表互不可见
- [x] 4.2 托管新建 Project 落在 `users/{user_id}/projects/`，拒绝数据根之外和其他用户目录；上传与下载沿用该边界。补测试覆盖 `..` 和绝对路径
- [x] 4.3 托管习惯记忆读写使用会话 `user_id`，不再写死 `default`。补测试：两个用户的习惯文件路径不同且内容互不可读
- [x] 4.4 托管下普通用户不能写入模型 Key，管理员可以；读取目录的响应不含 Key。桌面本机保存自己的 `models.json` 仍不要求账号。补 API 测试
- [x] 4.5 增加一次性归属命令：托管启动发现空 `owner_user_id` 时，业务接口返回明确错误，直到命令把旧行划给指定用户。补测试：未归属时读取失败，归属后只有该用户可见

## 5. 平台模型

- [x] 5.1 实现 `GET /api/models/provided`，模型 ID 使用 `platform:` 前缀，响应不含 Key；无会话时拒绝。补测试
- [x] 5.2 实现 `POST /api/models/complete`：用服务器 `models.json` 完成流式调用和 fallback，不写入 `chat_messages`。补测试：调用审计存在，聊天表无桌面端这轮正文
- [x] 5.3 桌面端保存托管令牌和服务地址；`ModelSelector` 在已登录时合并本机模型与平台分组，不改写本机 `models.json`。补前端测试：退出后平台分组消失，本机条目还在
- [x] 5.4 桌面发送时，`platform:` 模型走网关，本机模型仍走 `resolve_model_binding()`。补测试：未登录只能发送本机模型；登录后平台一轮的回复写入本机会话

## 6. Web 登录界面与部署

- [x] 6.1 Web 在 `serveMode=hosted` 时提供注册、验证、登录、退出和重置密码；未登录不能进入工作台。`serveMode=desktop` 不出现这道拦截。补前端测试覆盖两种模式
- [x] 6.2 新增 `deploy/` Compose 与反向代理样例：只发布 443，不映射 `8765`，SSE 关闭缓冲，不含真实密钥。用配置审查或启动检查确认端口声明符合该边界
- [x] 6.3 行为落地后更新 `AGENTS.md`、`docs/architecture-overview.md` 和 `docs/web-service-plan.md` 的状态，写明托管会话、按账号隔离，以及桌面登录只领取平台模型。对照四份 delta spec 核对没有把桌面数据同步写进去
