# Proposal

## Why

2026-10-20 代码质量审查发现三处可被外部直接利用的安全缺口：桌面内置后端只看"请求是否来自回环地址"，不校验 Host、Origin，也没有令牌，而且打包版 CORS 白名单包含 `null`，用户浏览器里任意网页都能通过沙箱 iframe 或 DNS 重绑定读写本机后端（包括 `PATCH /api/chat/sessions/{session_id}/approval-mode`（可改成 `full_access`） 切换审批模式）；托管服务的 6 位邮箱验证码不限错误次数、发新码不作废旧码、登录和发码接口没有限流；Electron 主窗口把任意协议的链接交给系统打开，也没有导航拦截和 CSP。这三处修复面小、相互独立，必须先于结构重构单独上线。

## What Changes

- 桌面模式后端新增请求守卫：Host 主机名只能是 `127.0.0.1`、`localhost` 或 `::1`；带 Origin 的请求必须命中已配置的允许来源；配置了启动令牌时，除 CORS 预检和 `GET /api/health` 外的所有请求都必须携带正确令牌。托管模式不经过这个守卫。
- Electron 主进程每次启动生成 256 位随机令牌，只通过环境变量交给自己拉起的后端，并在会话网络层给发往内置后端的请求自动加令牌头（覆盖 fetch、EventSource、图片、iframe、下载）。渲染进程脚本读不到令牌，前端业务代码不需要修改。
- **BREAKING**（仅影响绕过 Electron、直接调用打包版内置后端的本机脚本或工具）：打包版后端拒绝没有令牌的请求，返回 401。开发模式（`scripts/dev.sh`、`electron:dev`，不配置令牌）不要求令牌，只新增 Host 和 Origin 校验。
- 托管验证码加固：同一邮箱同一用途签发新码时作废旧码；单个验证码累计输错 5 次立即作废；同一邮箱同一用途 60 秒内最多签发 1 次、1 小时内最多 5 次，超限时接口仍返回与成功相同的响应，不暴露邮箱是否存在。
- 托管认证接口按客户端限流：`/api/auth/login`、`/verify`、`/reset-password` 每个客户端每 60 秒最多 10 次，`/register`、`/forgot-password` 每 60 秒最多 5 次，超限返回 429 和 `Retry-After`。
- Electron 外链与导航：`window.open`、`target=_blank` 和 IPC `open-external` 共用同一份协议白名单（`http:`、`https:`、`mailto:`、`obsidian://open`），其他协议一律拒绝；主窗口禁止导航或重定向离开应用自身页面，命中白名单的地址改由系统浏览器打开。
- 打包版页面注入 Content-Security-Policy，禁止内联脚本、远程脚本、`object` 和 `base` 改写；开发模式不注入，保证 Vite 热更新可用。
- `open-path` 先解析真实路径（跟随符号链接）再校验允许目录；目标是可执行文件、脚本、应用包或安装包时，只在访达/资源管理器中定位，不直接运行。

## Capabilities

### New Capabilities

- `desktop-backend-access`: 桌面内置后端只接受本应用发出的请求，包括 Host、Origin 校验与每次启动的访问令牌，以及令牌在桌面应用内的传递方式。
- `desktop-shell-security`: Electron 外壳的外链协议白名单、主窗口导航限制、打包版 CSP，以及打开本地路径时不能启动程序的边界。
- `account-access`: 在未归档变更 `hosted-account-access` 已定义的同名能力上追加验证码作废、错误次数上限、签发频率与认证接口限流要求。该能力尚未进入 `openspec/specs/`，本变更只使用 ADDED，不修改已有要求。

### Modified Capabilities

（无。`openspec/specs/` 现有的 `group-studio`、`scheduled-job-skill-context`、`skills` 三个能力的要求都不变。）

## Impact

- 后端：新增 `backend/src/server/desktop_guard.py`、`backend/src/server/auth/rate_limit.py`；修改 `backend/src/server/main.py`（中间件注册）、`backend/src/config/settings.py`（新增 `desktop_access_token`）、`backend/src/storage/auth_schema.py`、`backend/src/storage/control_db.py`（`auth_email_codes.failed_attempts` 列及旧库补列）、`backend/src/storage/auth_records.py`、`backend/src/server/auth/service.py`、`backend/src/server/auth/routes.py`。
- Electron：新增 `electron/desktopAccess.ts`、`electron/navigationPolicy.ts`；修改 `electron/main.ts`（令牌生成与注入、`setWindowOpenHandler`、`will-navigate`/`will-redirect`、`open-path`、主进程访问后端的 fetch）。
- 前端：修改 `frontend/vite.config.ts`（仅构建时注入 CSP 的插件）、`frontend/tsconfig.app.json`（排除新的 Electron 策略测试文件）；新增 `frontend/src/features/desktop/navigationPolicy.test.ts`、`frontend/src/features/desktop/desktopAccess.test.ts`。前端业务请求代码不改。
- 测试：新增 `backend/test/test_desktop_request_guard.py`、`backend/test/test_account_code_protection.py`。
- 数据：托管控制面库 `auth_email_codes` 增加一列，启动时对旧库幂等补列；没有数据迁移。
- 与进行中变更的关系：`simplify-global-session-context` 的 19.3 会改写 Electron 可信目录的来源（`refreshTrustedProjectRootsFromBackend`），本变更不改可信目录的来源，只在其结果上加真实路径校验和可执行文件拦截，两者可按任意顺序合入；合入后者时，主进程新的后端请求必须走本变更提供的 `backendFetch`。建议先归档 `hosted-account-access`，再归档本变更。
- 依赖：不新增第三方依赖。
