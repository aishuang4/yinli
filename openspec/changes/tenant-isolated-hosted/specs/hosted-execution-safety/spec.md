# Spec Delta

## Purpose

规定 Web 托管下 Agent 的执行边界：只允许远程 MCP，命令与脚本只在容器沙箱中执行，出站请求防 SSRF，按租户配额，按运行形态下发能力开关。

## ADDED Requirements

### Requirement: Only remote MCP in hosted mode

托管模式下 MUST 拒绝注册、安装或启用 stdio 类型 MCP，内置 stdio MCP MUST NOT 出现在托管用户的列表中。远程 MCP 地址 MUST 通过出站地址校验。

#### Scenario: Stdio MCP rejected

- **WHEN** 托管用户尝试安装 stdio MCP
- **THEN** 请求被拒绝，不在服务器上启动任何进程

#### Scenario: Builtin stdio servers hidden

- **WHEN** 托管用户查看 MCP 列表
- **THEN** 列表不包含内置的 filesystem、git、sqlite、fetch 等 stdio 服务

### Requirement: Commands and scripts run only in sandbox

托管模式下 Bash 工具、脚本型 Skill 和 script 定时任务 MUST 在容器沙箱中执行。沙箱 MUST 只挂载当前租户的当前 Project 目录，MUST 删除全部 Linux capabilities、禁止提权，并限制内存、CPU 和进程数。不同租户或不同 Project MUST NOT 共用同一个沙箱容器。Agent 配置为本地执行后端时 MUST 被强制改为沙箱执行。沙箱接入方式（本机 Docker daemon 或远程 Docker 主机）MUST 可通过配置切换；托管服务的沙箱配置不可用时 MUST 拒绝启动。

#### Scenario: Local backend overridden

- **WHEN** 托管用户把 Agent 执行后端设为本地并保存
- **THEN** 保存后的配置为沙箱执行

#### Scenario: Sandbox cannot see other tenants

- **WHEN** 沙箱内命令尝试读取数据根下其他租户的目录
- **THEN** 该路径在沙箱内不存在

#### Scenario: Default agents do not share containers

- **WHEN** 两个托管用户同时使用各自的 `default` Agent 执行命令
- **THEN** 两次执行运行在不同的容器中

#### Scenario: Script job runs in sandbox

- **WHEN** 托管用户的 script 定时任务到点触发
- **THEN** 脚本在一次性沙箱容器中执行，输出写回该次触发记录

### Requirement: Outbound requests are SSRF-checked

托管模式下，自定义模型地址、HTTP 工具、WebFetch、知识库工具与远程 MCP 的出站请求 MUST 拒绝解析到回环、私网、链路本地、CGNAT、组播、保留、未指定地址和云元数据地址的目标，包括经重定向到达的目标。部署可配置例外主机。桌面模式 MUST NOT 施加此限制。

#### Scenario: Metadata address blocked

- **WHEN** 托管用户把自定义模型地址设为 `http://169.254.169.254/`
- **THEN** 保存被拒绝

#### Scenario: Redirect to private network blocked

- **WHEN** 托管用户的 HTTP 工具请求公网地址，而该地址重定向到 `http://10.0.0.5/`
- **THEN** 重定向那一跳被拒绝，工具返回错误

#### Scenario: Desktop can reach local model

- **WHEN** 桌面用户把自定义模型地址设为 `http://127.0.0.1:11434`
- **THEN** 保存并调用成功

### Requirement: Capabilities are advertised and enforced

服务 MUST 按运行形态下发能力开关（本地路径、stdio MCP、本地执行、沙箱执行与可用镜像、云端登录、自定义模型）。前端 MUST 隐藏不可用的入口，后端 MUST 对每个入口独立校验。

#### Scenario: Hosted config hides local path picker

- **WHEN** Web 前端读取运行配置
- **THEN** 能力开关表明不可选择本地路径，且后端忽略客户端传入的 Project 路径

#### Scenario: Backend enforces without frontend

- **WHEN** 托管用户绕过前端直接调用接口安装 stdio MCP
- **THEN** 后端拒绝该请求

### Requirement: Per-tenant quotas

托管模式 MUST 按租户限制磁盘占用、单文件上传大小、并发 Run 数、定时任务数量与每日触发次数，超限 MUST 返回带配额名称和上限的明确错误。桌面模式 MUST NOT 施加配额。

#### Scenario: Concurrent run limit

- **WHEN** 租户已达到并发 Run 上限后再次发送消息
- **THEN** 返回超限错误，已有 Run 不受影响

#### Scenario: Oversized upload

- **WHEN** 托管用户上传超过单文件上限的附件
- **THEN** 上传被拒绝，已写入的部分被清理

### Requirement: Frontend state resets on account change

前端在登录、退出或切换账号时 MUST 清空所有与租户相关的内存状态和缓存，MUST NOT 用上一个账号的 Workspace、Project、Agent 或通知发起请求。

#### Scenario: Switch account in the same tab

- **WHEN** 用户甲退出后，用户乙在同一个浏览��标签页登录
- **THEN** 界面不显示甲的任何数据，首个请求使用乙自己的默认 Project
