# Spec Delta

## Purpose

规定使用者如何用邮箱注册并登录托管服务，以及桌面本机在不登录时如何继续使用本机工作台。

## ADDED Requirements

### Requirement: Email registration stays pending until verified

系统 MUST 接受邮箱和密码的注册。新账号状态 MUST 为待验证，在验证成功前 MUST NOT 登录。密码 MUST 只保存哈希。对已存在邮箱的注册请求，系统 MUST 返回与新注册相同的成功响应，且 MUST NOT 暴露该邮箱是否已注册。

#### Scenario: New email receives a verification code

- **WHEN** 使用者用一个尚未注册的邮箱和符合要求的密码注册
- **THEN** 账号处于待验证，并且该邮箱收到一次性验证码

#### Scenario: Existing email does not reveal the account

- **WHEN** 使用者用一个已经注册的邮箱再次注册
- **THEN** 响应与新注册成功相同，且不会新开一个可登录账号

#### Scenario: Unverified account cannot log in

- **WHEN** 待验证账号使用正确密码登录
- **THEN** 登录失败，且不会建立会话

### Requirement: Verification activates the account

系统 MUST 提供验证入口。正确且未过期的验证码 MUST 把对应账号变为可登录。错误、过期或已使用的验证码 MUST 失败且不改变账号状态。

#### Scenario: Valid code activates login

- **WHEN** 待验证用户提交正确且未过期的验证码
- **THEN** 账号变为可登录，该验证码不能再次使用

#### Scenario: Wrong code leaves the account pending

- **WHEN** 待验证用户提交错误验证码
- **THEN** 账号仍为待验证

### Requirement: Login issues a session for web and desktop

只有可登录账号使用正确密码时，系统 MUST 建立会话。Web 登录 MUST 设置 HttpOnly 会话 Cookie。桌面登录 MUST 返回可单独保存的会话令牌，供后续平台请求放在 `Authorization` 头。错误密码、待验证和已停用账号 MUST NOT 建立会话。

#### Scenario: Web login sets a cookie

- **WHEN** 可登录用户在 Web 提交正确密码
- **THEN** 响应设置 HttpOnly 会话 Cookie，后续同源请求可以据此识别用户

#### Scenario: Desktop login returns a bearer token

- **WHEN** 可登录用户从桌面端提交正确密码
- **THEN** 响应包含会话令牌，且该令牌可以在后续平台请求的 `Authorization` 头中使用

#### Scenario: Disabled account cannot log in

- **WHEN** 已停用用户提交正确密码
- **THEN** 登录失败，且已有会话不再被接受

### Requirement: Logout and reset revoke access

系统 MUST 提供退出、忘记密码和重置密码。退出 MUST 使当前会话立即失效。重置密码 MUST 先验证发到邮箱的一次性验证码，成功后 MUST 使该用户现有会话失效。

#### Scenario: Logout rejects the old session

- **WHEN** 用户退出当前 Web 或桌面会话
- **THEN** 该会话之后的工作台请求被拒绝

#### Scenario: Password reset signs out other sessions

- **WHEN** 用户用邮箱验证码完成重置密码
- **THEN** 新密码可以登录，重置前发出的会话全部失效

### Requirement: Hosted workbench requires a session

托管模式下，除健康检查、注册、验证、登录、忘记密码和重置密码外，工作台与管理员接口 MUST 要求有效会话。只来自回环地址 MUST NOT 代替会话。桌面本机模式的工作台接口 MUST 继续只允许本机回环访问，且 MUST NOT 因为未登录而拒绝本机使用。

#### Scenario: Proxied hosted request without a session is rejected

- **WHEN** 请求经本机反向代理到达托管服务，来源是回环地址，但没有有效会话
- **THEN** 工作台接口拒绝该请求

#### Scenario: Desktop local chat works without login

- **WHEN** 桌面本机后端收到来自回环地址、且用户未登录的对话请求
- **THEN** 请求按现有本机工作台处理，不要求账号会话
