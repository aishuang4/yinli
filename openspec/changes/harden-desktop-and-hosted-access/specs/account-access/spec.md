# Spec Delta

## Purpose

规定使用者如何用邮箱注册并登录托管服务，以及桌面本机在不登录时如何继续使用本机工作台。

## ADDED Requirements

### Requirement: Issuing a new code invalidates earlier codes

系统为同一邮箱、同一用途（注册验证 `register` 或重置密码 `reset`）签发新验证码时，MUST 在同一事务内把该邮箱该用途所有尚未使用的旧验证码标记为已作废。任一时刻同一邮箱同一用途 MUST 最多只有一个可用验证码。

#### Scenario: Old reset code stops working

- **GIVEN** 用户先后两次申请忘记密码，收到码 A 和码 B，两次申请间隔超过 60 秒
- **WHEN** 用户用码 A 提交重置密码
- **THEN** 重置失败，用码 B 提交仍可成功

### Requirement: A code is invalidated after repeated wrong attempts

同一个可用验证码累计被提交错误值达到 5 次时，系统 MUST 立即作废该验证码；作废后即使提交正确值也 MUST 失败。错误计数 MUST 持久化在数据库中，服务重启后仍然有效。失败响应 MUST 与普通错误码的响应相同，不提示剩余次数。

#### Scenario: Sixth attempt with the right code fails

- **GIVEN** 待验证用户的验证码已被提交错误值 5 次
- **WHEN** 用户提交正确验证码
- **THEN** 验证失败，账号保持待验证

#### Scenario: Fewer than five mistakes still allow success

- **GIVEN** 验证码已被提交错误值 4 次
- **WHEN** 用户提交正确验证码
- **THEN** 验证成功

### Requirement: Code issuing is rate limited per email

同一邮箱同一用途 MUST 在 60 秒内最多签发 1 个验证码，1 小时内最多签发 5 个。超过限制时系统 MUST NOT 签发或发送新码，且接口响应 MUST 与正常签发时完全相同，不暴露邮箱是否存在或是否被限制。

#### Scenario: Repeated forgot-password does not send another email

- **GIVEN** 用户刚申请过忘记密码
- **WHEN** 60 秒内再次申请忘记密码
- **THEN** 接口返回 `{"ok": true}`，邮箱没有收到第二封邮件，第一个码仍然可用

### Requirement: Authentication endpoints are rate limited per client

托管模式下，系统 MUST 按客户端地址限制认证接口的调用次数：`POST /api/auth/login`、`POST /api/auth/verify`、`POST /api/auth/reset-password` 每个客户端每 60 秒最多 10 次；`POST /api/auth/register`、`POST /api/auth/forgot-password` 每个客户端每 60 秒最多 5 次。超限请求 MUST 返回 429，携带以秒为单位的 `Retry-After`，且 MUST NOT 执行密码校验、验证码校验或发信。客户端地址在直连对端为本机反向代理时 MUST 取 `X-Forwarded-For` 的最后一跳，否则 MUST 取直连对端地址。桌面模式不启用该限流。

#### Scenario: Eleventh login within a minute is throttled

- **GIVEN** 同一客户端在 60 秒内已调用 10 次登录
- **WHEN** 该客户端第 11 次调用登录（无论密码是否正确）
- **THEN** 接口返回 429 和 `Retry-After`，没有创建会话

#### Scenario: Different clients are counted separately

- **GIVEN** 客户端 A 已被限流
- **WHEN** 客户端 B（`X-Forwarded-For` 最后一跳不同）调用登录
- **THEN** 客户端 B 的请求正常处理
