# Spec Delta

## Purpose

规定 Web 托管按租户分数据根隔离、控制面与租户数据分离、租户生命周期、后台任务如何进入租户，以及桌面端作为固定 local 租户的行为。

## ADDED Requirements

### Requirement: Each hosted user has an isolated data root

托管模式下，每个已验证用户 MUST 拥有独立的数据根目录，其中包含自己的数据库、自定义模型配置、加密密钥、记忆、Skill、Subagent、Workspace catalog 和 Project 文件。任何工作台请求 MUST 只读写当前会话用户的数据根。使用他人资源标识 MUST 返回不存在，且响应中 MUST NOT 含有该资源的任何内容。

#### Scenario: Cross-tenant run is hidden

- **WHEN** 用户乙使用用户甲的 runId 请求 Run 状态、订阅、取消、插话或重跑
- **THEN** 响应为不存在，甲的 Run 不受影响

#### Scenario: Cross-tenant scheduled job is hidden

- **WHEN** 用户乙对用户甲的定时任务 ID 执行读取、修改、暂停、立即运行或删除
- **THEN** 响应为不存在，甲的任务不变

#### Scenario: Notifications do not cross tenants

- **WHEN** 用户甲的定时任务完成并产生通知，此时用户乙已订阅通知流
- **THEN** 乙的通知列表和通知流都不出现这条通知

#### Scenario: Agent professional memory does not leak

- **WHEN** 用户甲的对话写入 `default` Agent 的专业区记忆，随后用户乙使用自己的 `default` Agent 对话
- **THEN** 乙的提示词中不包含甲写入的内容

#### Scenario: Same provider id keeps separate keys

- **WHEN** 用户甲和用户乙各自配置一个 ID 为 `deepseek` 的自定义供应商，并填写不同的 Key
- **THEN** 两人的模型调用各自使用自己的 Key

#### Scenario: Server home directory is not discovered

- **WHEN** 服务器运维账号的 HOME 目录下存在 Skill 或 Subagent 定义
- **THEN** 任何托管用户的 Skill 和 Subagent 列表都不包含它们

### Requirement: Hosted requests without a tenant fail closed

托管模式下，代码在没有租户上下文时访问租户数据 MUST 抛出错误，MUST NOT 回落到进程级目录或 local 租户。公开接口 MUST NOT 访问租户数据。

#### Scenario: Background code without tenant

- **WHEN** 托管进程中一段后台逻辑未进入租户就读取会话
- **THEN** 抛出租户缺失错误，不读写任何租户的数据

### Requirement: Account data lives in a separate control store

账号、会话、验证码、平台 Key、租户表和平台调用审计 MUST 存放在独立的控制面存储中。租户数据库 MUST NOT 包含账号表或用户归属字段。管理员操作 MUST 只读写控制面。桌面模式 MUST NOT 创建控制面，账号与管理员接口在桌面模式下 MUST 返回不存在（云端登录状态接口除外）。

#### Scenario: Admin lists users without reading tenant data

- **WHEN** 管理员查看用户列表和在线状态
- **THEN** 数据来自控制面，不打开任何租户数据库

#### Scenario: Desktop has no account tables in use

- **WHEN** 桌面端启动并完成一轮对话
- **THEN** 不创建控制面数据库，本地注册接口返回不存在

### Requirement: Tenant lifecycle

用户邮箱验证通过时 MUST 创建租户，并播种默认 Workspace、默认 Project、系统 Skill 和系统 Agent；默认 Project 的资源白名单 MUST 包含这些系统 Agent 和 Skill。创建过程 MUST 幂等。停用 MUST 吊销会话、取消在途 Run 和定时任务触发，并停止该租户的调度。删除 MUST 在停用基础上清理该租户的沙箱容器，并移除整个租户数据根。

#### Scenario: New user can chat immediately

- **WHEN** 新用户完成验证后首次进入工作台
- **THEN** 存在可用的默认 Project，且能选择系统 Agent 发起对话

#### Scenario: Disabled tenant stops running

- **WHEN** 管理员停用一个正在运行对话且有到期定时任务的用户
- **THEN** 该用户的请求返回 403，在途 Run 被取消，到期任务不再触发

#### Scenario: Deleted tenant leaves nothing behind

- **WHEN** 管理员删除一个用户
- **THEN** 该用户的数据根目录不存在，带该用户标签的沙箱容器被删除，再次登录失败

### Requirement: Hosted project roots are server-assigned

托管模式下，新建 Project 的根目录 MUST 由服务端分配在当前租户的 `projects/` 目录下，客户端传入的路径 MUST 被忽略；修改已有 Project 根目录 MUST 被拒绝。

#### Scenario: Client path is ignored

- **WHEN** 托管用户创建 Project 时提交 `rootPath=/etc`
- **THEN** 创建成功，Project 根目录位于该用户租户的 `projects/` 下

#### Scenario: Root change rejected

- **WHEN** 托管用户修改已有 Project 的根目录
- **THEN** 请求被拒绝，根目录不变

### Requirement: Background tasks run inside their tenant

服务启动对账、定时任务触发、群聊续跑和记忆后处理 MUST 在所属租户的上下文中执行，读写所属租户的数据，并以所属用户身份使用平台模型。单个租户的后台任务失败 MUST NOT 影响其他租户。

#### Scenario: Scheduled job uses platform model

- **WHEN** 托管用户的定时任务使用 `platform:` 模型到点触发
- **THEN** 任务以该用户的平台 Key 调用模型，会话写入该用户的数据库

#### Scenario: Two tenants fire independently

- **WHEN** 两个托管用户各有一个同时到期的定时任务
- **THEN** 两个任务各自执行，结果分别写入各自的数据库

### Requirement: Desktop is the local tenant

桌面模式 MUST 固定使用 local 租户，数据根为本机数据目录，文件布局与改造前保持一致。登录与否 MUST NOT 改变本地数据的位置或可见性，MUST NOT 上传本地会话、Project、文件或记忆。

#### Scenario: Existing desktop data still loads

- **WHEN** 使用改造前创建的桌面数据目录启动新版本
- **THEN** 历史会话、Project、Agent、Skill 和模型配置都能正常读取和使用

#### Scenario: Logout keeps local data

- **WHEN** 桌面用户退出云端登录
- **THEN** 本地会话、Project 和文件仍全部可见

### Requirement: Chat history writes are incremental

会话与消息写入 MUST 按会话增量进行，MUST NOT 整表删除后重写。并发追加 MUST NOT 丢失消息。读取时 JSON 损坏的记录 MUST 以默认值返回，MUST NOT 因后续写入而被删除。消息的附件列表和澄清状态 MUST 持久化。

#### Scenario: Concurrent appends

- **WHEN** 同一租户的两个请求同时向同一会话追加消息
- **THEN** 两条消息都被保存，且顺序号不重复

#### Scenario: Attachments survive restart

- **WHEN** 用户发送带附件的消息后服务重启
- **THEN** 重新读取的历史消息仍包含附件列表

#### Scenario: Session delete cascades

- **WHEN** 用户删除一个会话
- **THEN** 该会话的消息和运行事件一并删除，其他会话不受影响
