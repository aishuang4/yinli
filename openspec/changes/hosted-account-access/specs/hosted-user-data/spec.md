# Spec Delta

## Purpose

规定 Web 托管下工作台数据如何按登录账号隔开，以及桌面端数据为何继续只留在本机。

## ADDED Requirements

### Requirement: Hosted workbench data belongs to the signed-in user

托管模式下，Workspace、Project、Chat、群聊、定时任务和习惯记忆的列表与读取 MUST 只返回当前会话用户的数据。使用他人资源标识的读取 MUST 返回不存在，MUST NOT 返回该资源内容。新建资源 MUST 归属于当前用户。

#### Scenario: Two users do not see each other's chats

- **WHEN** 用户甲已有会话，用户乙请求会话列表
- **THEN** 列表不包含甲的会话

#### Scenario: Cross-user identifier is hidden

- **WHEN** 用户乙使用甲的 Project 标识请求该 Project
- **THEN** 响应为不存在，且不含甲的文件或配置

### Requirement: Hosted project files stay inside the user directory

托管模式下，新建 Project 的根目录 MUST 位于该用户自己的数据目录内。把根目录改到其他用户目录、数据根之外或系统目录 MUST 被拒绝。上传与产物下载 MUST 只作用于当前用户有权访问的 Project。

#### Scenario: Create project lands in the user directory

- **WHEN** 已登录用户在 Web 创建一个新 Project
- **THEN** 该 Project 根目录位于该用户的数据目录下

#### Scenario: Path escape is rejected

- **WHEN** 已登录用户把 Project 根目录改成其他用户目录或数据根之外的绝对路径
- **THEN** 更新被拒绝，原根目录保持不变

### Requirement: Only admins write deployment model secrets

托管模式下，模型供应商 Key 的写入 MUST 只允许管理员。普通用户读取模型目录时，响应 MUST NOT 包含 Key。桌面本机模式 MUST 继续允许使用者维护自己的本机模型配置。

#### Scenario: Regular web user cannot save a provider key

- **WHEN** 已登录普通用户提交包含供应商 Key 的模型配置
- **THEN** 写入被拒绝，服务器上的 Key 不变

#### Scenario: Desktop local model file remains editable without an account

- **WHEN** 未登录的桌面端使用者修改本机模型配置
- **THEN** 修改只影响本机配置，不要求托管账号

### Requirement: Desktop data is not uploaded by login

桌面端登录、心跳和领取平台模型 MUST NOT 把本机会话、Project、文件或习惯记忆上传到托管服务。同一邮箱在 Web 与桌面的数据 MUST 各自独立。

#### Scenario: Desktop chat stays on the computer

- **WHEN** 桌面端用户登录并完成一轮使用本机数据的对话
- **THEN** 托管服务不新增这条对话记录，本机仍能读到它
