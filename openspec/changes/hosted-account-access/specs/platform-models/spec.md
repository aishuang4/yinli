# Spec Delta

## Purpose

规定平台统一提供的模型如何发给 Web 用户和已登录桌面端，同时让未登录桌面端继续使用本机自己配置的模型。

## ADDED Requirements

### Requirement: Platform catalog never includes provider keys

系统 MUST 提供平台模型目录。目录 MUST 包含可供选择的模型标识、展示名和能力信息。响应 MUST NOT 包含供应商 Key。未登录请求 MUST 被拒绝。

#### Scenario: Logged-in client receives models without keys

- **WHEN** 已登录用户请求平台模型目录
- **THEN** 响应包含可选模型，且任何字段都不含供应商 Key

#### Scenario: Anonymous catalog request is rejected

- **WHEN** 没有有效会话的客户端请求平台模型目录
- **THEN** 请求被拒绝

### Requirement: Desktop model list follows login state

未登录桌面端的模型选择器 MUST 只列出本机模型配置。已登录桌面端 MUST 在本机模型之外额外列出平台模型。登录、刷新目录和退出 MUST NOT 改写本机模型配置文件中的供应商 Key 或模型条目。

#### Scenario: Logged-out desktop shows only local models

- **WHEN** 桌面端未登录并打开模型选择器
- **THEN** 可选项只来自本机模型配置

#### Scenario: Login adds platform models without rewriting local keys

- **WHEN** 桌面端登录成功并刷新模型选择器
- **THEN** 选择器同时包含本机模型和平台模型，本机配置文件中的 Key 与原条目保持不变

### Requirement: Platform completion keeps keys and transcripts apart

选择平台模型时，系统 MUST 用服务器上的供应商配置完成该轮模型调用。桌面端本机 MUST 保存这轮对话结果。托管服务 MUST NOT 把桌面端的会话正文写入自己的聊天记录。令牌失效、退出或账号停用后，平台模型调用 MUST 失败，本机模型调用 MUST 仍可进行。

#### Scenario: Desktop platform turn stores the reply locally

- **WHEN** 已登录桌面端选择平台模型并完成一轮对话
- **THEN** 本机聊天记录包含该回复，托管服务的聊天记录不包含这轮正文

#### Scenario: Logout removes platform models but keeps local ones

- **WHEN** 桌面端退出登录后再次发送
- **THEN** 平台模型不可选或调用失败，本机模型仍可发送，已有本机历史仍可读取

### Requirement: Web uses the deployment catalog

Web 托管用户 MUST 从部署级模型目录中选择模型，MUST NOT 在自己的响应里看到 Key。管理员对目录的修改 MUST 影响之后的新 Run，MUST NOT 改写任何桌面电脑上的本机模型配置。

#### Scenario: Web run uses the admin-maintained catalog

- **WHEN** 管理员启用一个平台模型，已登录 Web 用户选择它并发送
- **THEN** 该轮调用使用服务器上的供应商配置，用户收到的目录响应不含 Key
