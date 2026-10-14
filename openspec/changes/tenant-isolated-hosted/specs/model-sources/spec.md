# Spec Delta

## Purpose

规定平台模型与自定义模型两层来源、密钥存储，以及桌面端经云端转发使用平台模型。

## ADDED Requirements

### Requirement: Two model layers

可选模型 MUST 由平台层（`platform:*`）与自定义层合并而成。托管用户 MUST 能编辑自己租户的自定义模型。平台模型能力表 MUST 只允许管理员修改。未指定模型时，MUST 依次使用租户默认模型、平台层第一个模型；两者都没有时 MUST 返回明确错误。

#### Scenario: Web user adds own model

- **WHEN** 托管普通用户保存自己的模型 URL 与 Key
- **THEN** 保存成功，只有该用户可见可用，读取响应只表明 Key 已配置

#### Scenario: Other user cannot see it

- **WHEN** 另一用户读取模型目录
- **THEN** 不包含前一用户的自定义模型

#### Scenario: New user defaults to platform model

- **WHEN** 新托管用户未配置任何自定义模型就发起对话
- **THEN** 对话使用平台层的第一个模型

#### Scenario: Regular user cannot edit platform capabilities

- **WHEN** 托管普通用户提交平台模型能力表修改
- **THEN** 请求被拒绝

### Requirement: Hosted secrets are encrypted per tenant

托管模式下，自定义模型 Key 和工具密钥 MUST 以部署主密钥加密后存放在所属租户的数据根内，MUST NOT 以明文落盘。托管服务缺少主密钥时 MUST 拒绝启动。

#### Scenario: Secret file is not plaintext

- **WHEN** 托管用户保存一个模型 Key
- **THEN** 租户目录下的密钥文件内容不包含该 Key 的明文

#### Scenario: Missing master key

- **WHEN** 以托管模式启动且未配置主密钥
- **THEN** 服务启动失败并给出明确提示

### Requirement: Desktop uses platform models through cloud forwarding

桌面端登录后，平台模型 MUST 经云端 OpenAI 兼容转发接口调用，并进入完整 Agent 运行时（工具调用、流式输出、Team、记忆）。平台 Key MUST NOT 下发到本机。云端登录令牌 MUST 存放在系统密钥存储中，MUST NOT 写入 `models.json` 或明文账号文件。未登录时平台模型 MUST NOT 出现在可选列表中。

#### Scenario: Logged-in desktop uses tools with platform model

- **WHEN** 桌面已登录用户用平台模型发起需要工具调用的对话
- **THEN** 对话在本机运行时完成工具调用，模型请求经云端转发，会话保存在本机

#### Scenario: Logout removes platform models only

- **WHEN** 桌面用户退出登录
- **THEN** 平台模型从列表消失，本机自定义模型仍可用

#### Scenario: Expired cloud session

- **WHEN** 桌面端的云端令牌过期后用户选择平台模型发起对话
- **THEN** 对话失败并提示重新登录，本地状态显示登录已失效

#### Scenario: Legacy token file migrated

- **WHEN** 桌面端账号文件中存在旧版明文令牌
- **THEN** 首次读取时令牌迁入系统密钥存储，账号文件不再包含令牌

### Requirement: Cloud forwarding is authenticated and audited

云端转发接口 MUST 校验登录令牌，用该用户的平台 Key 调用上游，并记录调用审计（用户、模型、客户端类型、用量、状态），MUST NOT 记录请求或响应正文。令牌无效 MUST 返回 401；账号停用 MUST 返回 403；请求的模型不在该用户可用列表中 MUST 返回不存在；上游 Key 失效 MUST 返回网关错误而不是 401。流式请求 MUST 逐块透传。

#### Scenario: Disabled account

- **WHEN** 已停用账号的桌面端调用转发接口
- **THEN** 返回 403，不调用上游模型

#### Scenario: Upstream key rejected

- **WHEN** 上游拒绝该用户的平台 Key
- **THEN** 转发接口返回网关错误，桌面端不把它当作登录失效

### Requirement: Plain-text platform relay is removed

桌面端把平台模型对话转成纯文本请求的旧路径 MUST 被移除，所有平台模型对话 MUST 经过完整 Agent 运行时。

#### Scenario: Platform chat creates a normal run

- **WHEN** 桌面已登录用户用平台模型发送消息
- **THEN** 产生一个正常的 Chat Run，带有运行轨迹，且写入用户所选会话而不是共享会话
