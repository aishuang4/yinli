# Web 服务化改造计划

> 状态：2026-10-08 起按 `openspec/changes/hosted-account-access` 落地。桌面安装包的签名、公证和 SmartScreen 仍由 `docs/electron-packaging-plan.md` 负责。托管会话、按账号隔离和平台模型以该变更为准；本文保留当时的产品决定。

## 要解决的问题

macOS 公证和 Windows 代码签名证书还没就绪，安装包给别人用时要手动放行。开发时浏览器已经能打开工作台：`./scripts/dev.sh` 起 Vite `5173` 和 FastAPI `8765`，页面走 `/api` 代理。这还不是可交付的服务。

当前后端默认只听 `127.0.0.1`，`LocalAccessMiddleware` 拒绝非回环来源。这个边界挡不住反向代理。Nginx、Caddy 或 Vite 从本机把请求转到 `127.0.0.1:8765` 时，后端看到的客户端就是回环地址，中间件会放行。仓库里没有登录、注册和管理员页面。Chat、Workspace、Project 和习惯记忆现在都落在固定的 `default` 用户上，任何人打开页面都能看到同一份数据。

Electron 生产模式用 `loadFile` 打开 `frontend/dist/index.html`，前端 `base` 是 `./`，接口改写依赖 `window.electronAPI`。浏览器生产包必须由 HTTP 同源提供页面和 `/api`。

## 已确认的产品决定

- Web 托管要有独立账号。使用者用邮箱注册，验证邮箱后用该邮箱登录。
- 同一套站点里要有管理员端。管理员能看到用户列表、在线或离线、最近活跃时间，并能停用账号。
- 桌面端可以不登录直接使用，数据仍在本机。登录是可选项，用来领取平台统一提供的模型，这样使用者不必自己配置供应商和 Key。
- 未登录时，桌面端只用本机 `models.json` 里用户自己配置的模型。登录不改写这份文件，也不把会话、Project 和记忆上传到服务器。

## 本轮采用的实现约定

下面几条用来把上面的决定落到现有架构上。若要改，阶段划分跟着改。

- 登录凭证是邮箱加密码。密码只存哈希。注册后状态为待验证，验证邮件里的一次性验证码通过后才能登录。
- 忘记密码走同一套邮件通道，不另做一套身份系统。
- 公开注册只创建普通用户。管理员由环境变量 `AGENT_WORKSTATION_ADMIN_EMAILS` 指定；启动时把这些已存在邮箱标成管理员。不在注册接口里开放管理员角色。
- 会话使用 HttpOnly、Secure、SameSite Cookie。Chat、群聊、通知的 `EventSource` 不能自带 `Authorization` 头，同源 Cookie 可以由浏览器自动带上。
- 在线的定义：该用户有未过期会话，且最近 90 秒内有心跳、普通 API 或 SSE 活动。超过则显示离线，并保留最近活跃时间。
- Web 上的模型 Key 属于这一次部署，由管理员配置，普通用户接口继续只看到脱敏结果。Web 上的对话、Workspace、Project、上传文件和习惯记忆属于账号。
- 桌面端的对话、Workspace、Project、上传文件和习惯记忆始终属于本机数据目录，不因登录改到服务器。
- 平台模型的 Key 只放在托管服务器。桌面端登录后拿到的是模型目录和调用资格，本机后端把这一轮请求转到服务器上的模型网关。退出登录、会话过期或账号被停用后，平台模型从选择器消失，本机自配模型仍可使用。
- 桌面本机 API 继续只接受回环访问，不强制 Cookie。桌面登录调用的是托管服务的账号接口，令牌存在本机用户数据目录。

## 目标形态

```text
浏览器
  |
  | HTTPS，同一域名
  v
反向代理（只发布 443）
  |-- /                 frontend/dist
  |-- /login /register  登录与注册
  |-- /admin            管理员端，role=admin
  |-- /api/auth/*       注册、验证、登录、退出、心跳
  |-- /api/admin/*      用户与在线状态
  |-- /api/*            已登录用户的工作台 API，含 SSE
  v
AGENT_WORKSTATION_DATA_DIR
  SQLite（账号、会话、业务表）
  models.json（部署级，仅管理员可改）
  users/{user_id}/      该用户的 Project、习惯记忆、上传文件
```

公网只看到反向代理。`8765` 继续绑在回环地址。`hosted` 模式下，回环来源本身不算登录。

桌面端是另一条运行路径。本机后端仍由 Electron 拉起，SQLite 和文件留在 `AGENT_WORKSTATION_DATA_DIR`。只有登录和平台模型调用会访问托管服务：

```text
桌面端未登录
  本机 UI --> 本机 FastAPI --> 本机 models.json --> 模型供应商

桌面端已登录
  本机 UI --> 本机 FastAPI --> 本机数据（会话、文件、记忆）
                  |
                  +--> 托管服务 /api/auth/*     登录、心跳、在线状态
                  +--> 托管服务 /api/models/provided
                       管理员配置的模型目录（响应不含 Key）
                  +--> 托管服务 /api/models/complete
                       本机带着会话令牌转发这一轮模型请求，Key 留在服务器
```

同一个邮箱可以同时登录 Web 和桌面。两边的会话和文件不合并，Web 数据在服务器，桌面数据在各自电脑上。

## 账号与会话

SQLite 新增三张表，沿用现有同步 `sqlite3` 和 `_ensure_column()` 风格，不引入迁移框架。

| 表 | 用途 |
| --- | --- |
| `auth_users` | `id`、规范化邮箱、密码哈希、`role=user\|admin`、`status=pending\|active\|disabled`、创建时间、验证时间 |
| `auth_sessions` | 会话 id、用户 id、token 哈希、客户端类型 `web\|desktop`、过期时间、`last_seen_at`、撤销时间 |
| `auth_email_codes` | 邮箱、用途（注册验证或重置密码）、验证码哈希、过期时间、已使用时间 |

接口：

- `POST /api/auth/register`：邮箱、密码。写入 `pending` 用户并发送验证码。已注册邮箱返回同样的成功提示，避免用来探测账号。
- `POST /api/auth/verify`：邮箱、验证码。通过后变为 `active`。
- `POST /api/auth/login`：邮箱、密码。仅 `active` 可登录。Web 成功后写 Cookie。桌面端另获一枚会话令牌，保存在本机用户数据目录，请求平台接口时放在 `Authorization` 头；桌面页面和托管域名不同源，不能靠 Cookie。
- `POST /api/auth/logout`：撤销当前会话。
- `GET /api/auth/me`：当前用户 id、邮箱、角色。
- `POST /api/auth/forgot-password` 与 `POST /api/auth/reset-password`。
- `POST /api/auth/heartbeat`：前端登录后每 30 秒调用一次，刷新 `last_seen_at`。

密码哈希使用新增依赖的 argon2id。验证码和会话 token 入库前哈希。登录、注册和发信按邮箱与来源地址做简单频率限制。

邮件通过环境变量配置 SMTP。开发时 `AGENT_WORKSTATION_AUTH_EMAIL_MODE=log` 把验证码打到服务端日志。`hosted` 且模式不是 `log` 时，未配置 SMTP 则进程拒绝启动。

`AGENT_WORKSTATION_SERVE_MODE=desktop|hosted`，默认 `desktop`。

- `desktop`：本机 `LocalAccessMiddleware` 只允许回环客户端。本机工作台 API 不要求登录。
- `hosted`：除 `/api/health`、注册、验证、登录、忘记密码、重置密码外，工作台和管理员 API 都要求有效会话。停用用户的托管会话立即失效。桌面端若仍持有该会话，平台模型调用一并失效；本机自配模型不受影响。

桌面登录不在本机数据库里另建一套用户表。本机只保存托管服务发下的会话令牌、服务地址和当前邮箱，供心跳和模型网关使用。服务地址由使用者或安装配置指定，未配置时登录入口说明当前只能使用本机模型。

## 管理员端

管理员端是同一前端里的一个页面，入口只对 `role=admin` 显示。普通用户访问 `/api/admin/*` 得到 403。

第一版页面：

- 汇总：注册用户数、当前在线数、待验证数、已停用数。
- 表格：邮箱、角色、状态、注册时间、最近活跃、在线或离线、在线端（Web、桌面，可同时出现）。
- 操作：停用、恢复。停用后该用户不能再使用 Web 工作台和平台模型。桌面端回到未登录时的本机模型。
- 列表按最近活跃排序，可按在线、待验证、已停用筛选。

在线状态由服务端根据 `auth_sessions.last_seen_at` 计算，前端管理员页每 15 秒重新拉取。不把「在线」做成另一条长连接产品。

管理员在托管设置页维护平台模型目录，沿用现有 `models.json` 的供应商、模型、fallback 和参数。这份目录只服务 Web 用户和已登录桌面端。管理员不查看别人的对话正文，也不改写任何桌面电脑上的 `models.json`。用量计费留到后续。

## 数据归谁

登录如果只挡在 API 门口，所有 `active` 用户仍会读写同一批 Workspace、会话和 `users/default/habits.md`。所以托管模式在开放注册前，要把现有调用链上的 `user_id="default"` 换成当前会话用户。

| 数据 | Web 托管 | 桌面端 |
| --- | --- | --- |
| 账号、会话、在线状态 | 服务器账号表 | 不在本机复制用户库；只保存托管会话令牌 |
| 平台模型与 Key | 服务器 `models.json`，仅管理员可写 | 登录后远程读取目录，Key 不下发 |
| 自配模型 | Web 普通用户不写 Key | 本机 `models.json`，未登录时作为唯一模型来源 |
| Workspace、Project、Chat、群聊、定时任务、Trace | 当前登录用户 | 本机现有数据，保持单机 `default` 归属 |
| 习惯记忆与 Project 文件 | `users/{user_id}/` | 本机数据目录，不上传 |

专业区记忆仍按 Agent。Web 托管下，Agent 定义若会执行工具，第一版先放在该用户自己的 Workspace catalog 里。部署级共享 Agent 目录留到后续。

桌面端不因为登录把历史本机库拆成多账号，也不把本机库同步成 Web 上的那份数据。

## 本轮要交付的能力

桌面端使用者能够：

- 不登录，继续用本机模型、本机会话和本机文件。
- 登录后在模型选择器里看到平台提供的模型，无需填写供应商地址和 Key。
- 退出登录后回到本机模型；已经写在本机的会话和文件还在。

Web 使用者拿到 HTTPS 地址后，能够：

- 用邮箱注册、查收验证码、登录、退出、重置密码。
- 只看到自己的 Project、会话和习惯记忆。
- 在自己的目录里上传文件，让 Agent 处理，再下载产物。
- 使用浏览器里已经能工作的 Chat、Team、群聊、记忆、Skills 和定时任务。

管理员能够：

- 登录后进入管理员端。
- 看到用户规模、谁在线、谁离线、最近什么时候活跃，以及在线的是 Web、桌面或两端。
- 停用或恢复某个账号。停用收回 Web 访问和平台模型，不删除对方电脑上的本地数据。

## 本轮明确留到后面

- 邀请码、第三方登录、团队共享一个 Workspace。
- 管理员查看用户对话正文、远程操作某个用户的 Agent。
- 按人计费、配额，以及普通用户自己保存平台 Key。
- 桌面与 Web 之间同步会话、文件和记忆。
- 把使用者本机磁盘挂进服务器。
- 用 Web 替代 Electron，或在本轮完成签名、公证、自动更新。
- 把 SQLite 换成服务器数据库。

## 现状里已经能沿用的部分

| 能力 | 现状 |
| --- | --- |
| 浏览器 UI | Vite 开发服务器和 `npm run build` 已覆盖主流程。设置、通知、预览里对 `window.electronAPI` 为空有分支。 |
| 记忆路径 | 习惯文件已经按 `users/{user_id}/habits.md` 区分，只是运行时把 `user_id` 写成 `default`。 |
| 文件进出 | Project 上传和产物下载接口已存在，托管时改到该用户目录下。 |
| 预览与通知 | 浏览器保留文本、Markdown、图片、PDF、Word 抽取和 Excel 抽表。页内提醒继续用 `/api/notifications/stream`。 |
| 数据根 | `AGENT_WORKSTATION_DATA_DIR` 仍是各自运行时的唯一入口。Web 指向服务器数据卷，桌面指向本机用户目录。 |
| 模型选择 | Chat 已按 `modelConfigId` 在 Run 开始时绑定连接。平台模型使用单独的配置 ID 前缀，避免和本机 `models.json` 的 ID 混用。 |
| 产品档 | `config/product-features.json` 可以关掉暂不在 Web 提供的页面。管理员页不进产品档开关，由角色控制。 |

## 必须补上的缺口

### 1. 登录先于公网

全站 API 今天没有登录。模型 Key 在 `USER_DATA_ROOT/models.json`，Bash 能在 Project 所在机器上执行命令。`GuardedBash` 的黑名单只拦截明显误操作，注释里已经写明它不是安全沙箱。

托管模式的门禁是账号会话。只加反向代理、不校验会话，后端仍会把代理看成回环地址并放行。

### 2. 同域静态站

- 前端执行现有 `npm run build`。
- 反向代理把 `/` 指到 `frontend/dist`，把 `/api` 反代到本机后端。
- SSE 路径关闭代理缓冲，读超时覆盖单轮 Agent 和群聊。
- `vite.config.ts` 的 `base: './'` 在站点根路径下可以继续用。

Web 未登录访问工作台时进入登录页。桌面端未登录仍进入工作台，登录入口放在模型选择器或设置页，不挡住本机使用。

### 3. 每个用户的 Project 目录

创建 Project 的界面现在调用 `electronAPI.selectProjectDirectory()`。后端 `_validated_project_paths()` 只检查路径是已存在目录。

`hosted` 模式下，新建 Project 落在该用户的 `projects/` 目录，页面只收集名称。解析后的 `rootPath` 必须位于该用户目录内。桌面模式仍可用系统文件夹选择器。

### 4. 桌面端的平台模型

现有合同是：模型连接以本机 `models.json` 为唯一配置源，Key 明文在本机，接口响应只返回已配置标记。桌面登录若把平台 Key 写进这个文件，退出登录后 Key 仍会留在电脑上。

因此平台模型不进入本机 `models.json`。已登录时，选择器把服务器目录作为额外分组。选中平台模型后，本机 `runtime_builder` 仍在 Run 开始时绑定一份不可变配置，但这份配置的 Key 和上游地址留在服务器；本机只保留模型 ID、展示名和「由平台网关完成调用」的标记。同一 Run 的重试、fallback、Team 和记忆后处理沿用这份绑定。未登录或令牌失效时，该分组为空，Run 只能解析本机文件。

### 5. 工具执行范围

Write / Edit 已有 scope。Bash 仍可能写出这个范围。托管模式把 Bash、Read、Grep、Glob 的工作目录收在当前用户的 Project 与该用户的 Workspace catalog 内。

## 分阶段

### 阶段一：邮箱注册和登录

- 增加 `SERVE_MODE`、管理员邮箱、SMTP 和邮件模式配置。
- 增加账号、会话、验证码表，以及注册、验证、登录、退出、当前用户、忘记密码接口。
- `hosted` 用会话 Cookie 保护工作台 API。`desktop` 维持回环检查。
- Web 前端增加注册、验证、登录、退出。Web 未登录不能进入 Chat。桌面端此阶段仍只使用本机模型。
- 补测试：错误密码、未验证不能登录、停用后 Cookie 失效、回环代理在 `hosted` 下仍要会话。

验收：新邮箱能注册并在验证后登录；退出后原 Cookie 不能再调用 Web 的 `/api/chat`；桌面开发模式不登录也能打开 Chat 并使用本机模型。

### 阶段二：管理员端和在线状态

- Web 登录后每 30 秒心跳。SSE 连接存续期间也更新 `last_seen_at`。会话记录客户端类型，第一版先标成 Web；桌面心跳在阶段五接入。
- `GET /api/admin/users` 返回用户列表和由最近活跃时间算出的在线状态。
- `POST /api/admin/users/{id}/disable` 与 `enable`。禁止管理员停用自己。
- 前端管理员页展示汇总、表格、筛选和停用操作。普通用户看不到入口。

验收：两个浏览器分别登录时，管理员能看到两人在线；一方关闭页面超过 90 秒后变为离线；被停用的一方下次请求得到 401。

### 阶段三：按账号隔开工作台数据

- Workspace、Project、Chat、群聊、定时任务的查询和写入带上当前 `user_id`。
- 习惯记忆和 Project 文件改到 `users/{user_id}/`。
- 普通用户不能调用模型密钥写入接口。管理员仍用现有设置页维护 `models.json`。
- 补跨用户读取测试：甲的会话 id 和 Project id 对乙返回 404。

验收：两个账号的会话、Project 和习惯记忆互不可见。

### 阶段四：收住工具边界并完成部署

- `hosted` 拒绝用户目录之外的 Project root。Bash 与读取工具的根限制在该用户目录。
- 路径测试覆盖 `..`、符号链接和绝对路径。
- 新增 `deploy/`：Compose、反向代理、SMTP 环境变量样例，不含真实密钥。`8765` 不映射到公网。
- 备份说明覆盖 SQLite、`models.json` 和 `users/`。

验收：公网扫描看不到 `8765`；API 不能把 Project 指到其他用户目录或 `/etc`；一份空数据卷可以独立注册自己的用户。

### 阶段五：桌面端可选登录与平台模型

- 桌面设置服务地址。未配置时不显示平台模型，本机模型流程保持不变。
- 登录、退出、心跳调用托管账号接口。令牌只写入本机用户数据目录，不写入聊天记录。
- `GET /api/models/provided` 返回不含 Key 的平台目录。`POST /api/models/complete` 校验会话后，用服务器 `models.json` 调用供应商，并把流式结果回给本机后端。
- Chat 选择器在已登录时增加平台分组。退出、过期和停用后分组消失，已产生的本机消息保留。
- 管理员列表把带桌面心跳的会话显示为桌面在线。

验收：同一台桌面端，未登录只能选本机模型；登录后可以选平台模型并完成一轮对话，本机 `models.json` 的 Key 不变，服务器上不出现这台电脑的会话正文；退出后平台模型不可选，本机历史还在。停用账号后桌面端平台调用失败，本机自配模型仍能发送。

## 桌面能力在浏览器中的处理

| 桌面能力 | Web 行为 |
| --- | --- |
| 不登录使用本机模型和本机数据 | 桌面端保留；Web 必须登录 |
| 选择本机 Project 文件夹 | 在当前用户的服务器目录内新建 |
| Quick Look / `IPreviewHandler` | 浏览器预览或下载 |
| 系统通知 | 页内提醒 |
| 打开数据目录或 `models.json` | 管理员在设置页修改部署级配置 |
| Electron 拉起后端 | 由进程管理器或 Compose 拉起 |

## 风险

- 阶段一结束时大家仍共享 `default` 数据。开放真实注册要等阶段三完成。
- SQLite 写锁和进程内 Agent 缓存决定并发上限。第一版按少量同时在线交付。
- 在线状态依赖心跳和 SSE，用户停在前台但网络中断时，最多 90 秒后显示离线。
- 邮件进垃圾箱会造成「注册了却登不上」。管理员列表里的待验证状态用来发现这种情况。
- 平台模型费用记在服务器 Key 上，Web 用户和已登录桌面端都会消耗它。账号隔离解决不了互相消耗额度，配额留到后续。
- 桌面端离线时不能使用平台模型。本机自配模型不依赖托管服务。

## 建议的第一刀

只做阶段一，先不要做管理员页和数据拆分：

- `backend/src/config/settings.py`：托管模式、管理员邮箱、SMTP、邮件模式。
- 新的 `backend/src/server/auth/`：密码哈希、验证码、会话、路由。
- `backend/src/server/main.py`：按模式挂上会话校验；桌面路径保持回环检查。
- `backend/src/storage/database.py`：三张账号表。
- 前端登录、注册、验证页面，以及未登录时离开工作台。
- `backend/test/` 覆盖注册、验证、登录和托管模式拒绝无会话请求。

## 和现有文档的关系

- 桌面签名、公证、Windows 真机安装包继续看 `docs/electron-packaging-plan.md`。
- 本文落地前，不要按本文改 Chat SSE、群聊事件或记忆注入的事件字段。
- 当前事实仍以 `AGENTS.md` 和 `docs/architecture-overview.md` 为准。阶段三上线后再把账号归属写回那两处。
