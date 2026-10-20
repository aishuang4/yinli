# Spec Delta

## Purpose

规定桌面模式下内置后端如何确认请求来自本应用，而不是用户浏览器里的任意网页或其他本机程序借用回环地址发来的请求，以及桌面应用如何在不暴露给页面脚本的前提下携带访问凭据。

## ADDED Requirements

### Requirement: Desktop backend rejects foreign Host headers

桌面模式下，后端 MUST 在任何路由处理之前检查请求的 Host 主机名（忽略端口）。主机名不是 `127.0.0.1`、`localhost`、`::1` 之一时，后端 MUST 返回 403，且 MUST NOT 执行路由逻辑。托管模式 MUST NOT 做这项检查。

#### Scenario: DNS rebinding request is rejected

- **GIVEN** 后端以桌面模式运行
- **WHEN** 一个来自回环地址的请求携带 `Host: attacker.example:8765`
- **THEN** 后端返回 403，响应体 `detail` 为 `desktop_host_rejected`，目标路由没有被调用

#### Scenario: Local host names are accepted

- **GIVEN** 后端以桌面模式运行
- **WHEN** 请求的 Host 是 `127.0.0.1:39100`、`localhost:8765` 或 `[::1]:39100`
- **THEN** Host 检查通过，请求继续进入后续检查

#### Scenario: Hosted mode is unaffected

- **GIVEN** 后端以托管模式运行
- **WHEN** 请求的 Host 是公网域名
- **THEN** 本要求的检查不生效，请求按托管会话规则处理

### Requirement: Desktop backend rejects untrusted Origins

桌面模式下，携带 `Origin` 头的请求 MUST 只在 Origin 与已配置的允许来源（`cors_origins`）完全相等时放行，否则后端 MUST 返回 403。不带 `Origin` 头的请求 MUST 通过本项检查。

#### Scenario: Cross-site page cannot submit a request

- **GIVEN** 后端以桌面模式运行，允许来源为默认值
- **WHEN** 一个请求携带 `Origin: https://attacker.example`
- **THEN** 后端返回 403，响应体 `detail` 为 `desktop_origin_rejected`

#### Scenario: Same-origin request without Origin passes

- **WHEN** 一个不带 `Origin` 头的 GET 请求到达后端
- **THEN** Origin 检查通过

### Requirement: Packaged desktop backend requires the launch token

配置了桌面访问令牌（环境变量 `AGENT_WORKSTATION_DESKTOP_ACCESS_TOKEN` 非空）时，除 `OPTIONS` 请求和 `GET /api/health` 外，每个请求 MUST 在 `X-Yinli-Desktop-Token` 头中携带与配置完全相同的令牌；缺失或不匹配时后端 MUST 返回 401。令牌比较 MUST 使用恒定时间比较。令牌 MUST NOT 出现在任何响应体、响应头或日志中。未配置令牌时（开发模式），后端 MUST NOT 要求令牌。

#### Scenario: Request without token is rejected

- **GIVEN** 桌面后端配置了令牌
- **WHEN** 一个 Host 和 Origin 都合法、但不带 `X-Yinli-Desktop-Token` 的请求调用 `PATCH /api/chat/sessions/{session_id}/approval-mode`
- **THEN** 后端返回 401，响应体 `detail` 为 `desktop_token_required`，会话的审批模式不变

#### Scenario: Request with the launch token is accepted

- **GIVEN** 桌面后端配置了令牌
- **WHEN** 请求携带正确的 `X-Yinli-Desktop-Token`
- **THEN** 请求按原有路由逻辑处理

#### Scenario: Health check stays available without token

- **GIVEN** 桌面后端配置了令牌
- **WHEN** Electron 启动时对旧实例端口发送不带令牌的 `GET /api/health`
- **THEN** 后端返回健康信息，响应中不包含令牌

#### Scenario: Development backend does not require a token

- **GIVEN** 后端由 `scripts/dev.sh` 启动，未配置令牌
- **WHEN** Vite 开发代理转发一个不带令牌的请求
- **THEN** 令牌检查不生效，请求正常处理

### Requirement: Desktop app supplies the token without exposing it to page scripts

Electron 主进程 MUST 在每次应用启动时用密码学安全随机数生成至少 256 位的新令牌，只通过环境变量交给自己拉起的后端进程。主进程 MUST 在会话网络层给目标 origin 等于内置后端 origin 的请求附加令牌头，覆盖 fetch、EventSource、图片、iframe 和下载；对其他 origin（包括其他本机端口）MUST NOT 附加。令牌 MUST NOT 通过 preload、IPC 或任何渲染进程可读的位置暴露。主进程自身访问后端业务接口时 MUST 显式携带令牌。

#### Scenario: Images and streams from the backend carry the token

- **GIVEN** 打包版应用已启动内置后端
- **WHEN** 页面加载指向内置后端的 `<img>`、`<iframe>`、下载链接或 EventSource
- **THEN** 这些请求都携带正确令牌并成功返回

#### Scenario: Token is not sent to other local servers

- **WHEN** 页面请求 `http://127.0.0.1:5173` 或其他不是内置后端端口的地址
- **THEN** 请求中没有 `X-Yinli-Desktop-Token` 头

#### Scenario: Page scripts cannot read the token

- **WHEN** 渲染进程脚本遍历 `window.electronAPI` 的全部方法和可调用的 IPC 通道
- **THEN** 没有任何方法或通道返回令牌

#### Scenario: Each launch uses a new token

- **WHEN** 用户退出并重新启动应用
- **THEN** 新后端进程使用与上一次不同的令牌，旧令牌对新后端无效
