# Design

## Context

见 `proposal.md` 的 Why。当前后端默认绑定 `127.0.0.1`，`LocalAccessMiddleware` 只放行回环客户端；反向代理从本机转发时，这个检查会通过。账号不存在，Chat、Workspace 和习惯记忆都写在 `default`。模型连接的现行合同是本机 `models.json`：`resolve_model_binding()` 在 Run 开始时读文件，响应只返回 Key 是否已配置。桌面生产包由 Electron 打开本地 `dist`，和未来的托管域名不同源。

## Goals / Non-Goals

**Goals:**

- 用显式 `desktop` / `hosted` 模式把本机回环行为和托管会话边界分开。
- 让 Web 在同一 SQLite 里按账号隔离现有业务表，而不是先换数据库。
- 让桌面端用令牌领取平台模型，模型调用走服务器网关，本机文件格式保持不变。

**Non-Goals:**

- 把桌面历史迁移成多用户，或和 Web 做双向同步。
- 替换 Chat SSE、群聊事件或 Skill 目录规则。
- 在本变更里做配额、计费和第三方登录。

## Decisions

### 1. 托管门禁是会话，回环检查继续保留

`AGENT_WORKSTATION_SERVE_MODE` 默认 `desktop`。两种模式都保留回环中间件，避免 `8765` 被直接暴露到局域网。`hosted` 再加会话校验：白名单只有健康检查、注册、验证、登录、忘记密码和重置密码。代理把来源变成 `127.0.0.1` 时仍然要有会话。

备选是只在反向代理上放共享口令。那样没有用户身份，管理员端和按人隔离都做不了。

### 2. 账号表放在现有 SQLite，Web 用 Cookie，桌面用令牌

新增 `auth_users`、`auth_sessions`、`auth_email_codes`。密码用 argon2id。验证码和会话令牌入库前哈希。`auth_sessions` 记录 `client_kind=web|desktop` 和 `last_seen_at`。

Web 登录写 HttpOnly、Secure、SameSite=Lax Cookie，因为 `EventSource` 不能自带 Authorization。桌面页面与托管域名不同源，登录响应体返回令牌；本机把它放在用户数据目录，请求平台接口时放进 `Authorization`。本机不复制用户表。

管理员邮箱来自 `AGENT_WORKSTATION_ADMIN_EMAILS`。账号变为可登录时，若邮箱在名单里则角色改为管理员。注册请求里的角色字段忽略。

邮件用 SMTP 环境变量。`AGENT_WORKSTATION_AUTH_EMAIL_MODE=log` 时把验证码打到服务端日志，供开发使用。`hosted` 在既不是 log 模式、又没有 SMTP 时拒绝启动。

### 3. 在线状态由最近活动计算

心跳、已认证的普通请求和 SSE 连接存续都更新 `last_seen_at`。管理员列表把 90 秒内仍有未过期会话的用户标为在线，并按会话的 `client_kind` 聚合 Web 与桌面。页面轮询列表即可，不另建在线通道。停用用户时撤销其全部会话；接口拒绝停用自己。

### 4. Web 隔离用 owner 列和用户目录，桌面继续单机

托管模式下给 Workspace、Project、Chat、群聊和定时任务补 `owner_user_id`，列表和单条读取都带当前用户条件；跨用户标识返回 404。Project 根目录只允许落在 `AGENT_WORKSTATION_DATA_DIR/users/{user_id}/projects/`。习惯记忆沿用已有 `users/{user_id}/habits.md`，把现在写死的 `default` 换成会话用户。

桌面模式不读这些 owner 条件，本机数据仍按今天的单机库工作。登录不会上传这些表。

现有开发库切到 `hosted` 时，空 owner 的业务行不能直接暴露给第一个注册用户。启动托管服务时若发现空 owner 行，要求先执行一次性归属命令，指定一个已有用户；未归属前业务接口返回明确错误。全新数据卷没有旧行，不需要迁移。

备选是每个用户一个 SQLite 文件。隔离更硬，但现有 `database.py` 的单连接和启动迁移都要重做，超出本变更。

### 5. 平台模型使用独立 ID，Key 留在服务器

平台模型 ID 使用 `platform:` 前缀，避免和本机 `models.json` 的 ID 冲突。`GET /api/models/provided` 返回服务器目录的脱敏结果。

Web 用户本来就在服务器上，Run 继续走 `resolve_model_binding()`，只是普通用户不能调用保存 Key 的接口。桌面端选中 `platform:` 模型时，本机不把该 ID 交给本地 catalog；本机后端带令牌调用 `POST /api/models/complete`，由服务器读取自己的 `models.json` 并流式返回模型输出。同一 Run 的重试和 fallback 都在这次网关调用里完成，桌面只把最终文本写入本机 `chat_messages`。

退出、过期和停用只删除或拒绝令牌，不改本机 `models.json`。

备选是登录后把 Key 写进本机文件。实现更接近现行读取路径，但退出后 Key 仍留在电脑上，和「领取使用资格」不一致。

### 6. 部署只公开反向代理

Compose 中后端只监听容器或本机回环，宿主只发布 443。SSE 路径关闭代理缓冲。静态站和 `/api` 同源，Web Cookie 才能带上。

## Risks / Trade-offs

- [先开放注册、后补 owner 过滤会让用户互相看见 `default` 数据] → 任务顺序把隔离放在公开注册验收之前；隔离测试未通过时不把托管入口交给真实用户。
- [SQLite 单写者和进程内 Agent 缓存限制并发] → 第一版按少量同时会话交付，不在本变更做多进程。
- [平台模型多一跳，桌面离线时不可用] → 未登录路径仍直接调用本机供应商；选择器把平台模型标成需要网络。
- [验证邮件进入垃圾箱] → 管理员列表保留待验证状态；开发模式允许日志验证码。
- [网关若把桌面提示词写入服务器聊天表，就打破本地数据边界] → 网关只转发模型供应商并写调用审计，不调用 Chat 落库。

## Migration Plan

1. 默认仍是 `desktop`，现有本机和 Electron 流程不要求账号。
2. 新表由现有启动迁移创建。`owner_user_id` 对旧行可空。
3. 准备好 SMTP、管理员邮箱和数据卷后，用 `hosted` 启动。有空 owner 行时先跑归属命令。
4. 回滚时把模式改回 `desktop` 并停掉公网代理。账号表可留在库里，桌面不读取它们。

## Open Questions

无。SMTP 供应商和域名证书属于部署配置，不改变上述接口和数据边界。
