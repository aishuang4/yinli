# Spec Delta

## Purpose

规定管理员如何查看注册用户的在线状态，以及如何停用或恢复账号。

## ADDED Requirements

### Requirement: Only admins can read and change accounts

用户列表、在线状态、停用和恢复 MUST 只对管理员开放。普通用户调用这些接口 MUST 得到拒绝。公开注册 MUST NOT 把新账号创建为管理员。环境变量指定的管理员邮箱在对应账号可登录后 MUST 拥有管理员角色。

#### Scenario: Regular user cannot open the admin list

- **WHEN** 已登录的普通用户请求用户列表
- **THEN** 请求被拒绝，且响应不包含其他用户资料

#### Scenario: Registration cannot self-promote

- **WHEN** 注册请求试图指定管理员角色
- **THEN** 新账号仍是普通用户，或该请求被拒绝

#### Scenario: Configured admin email gains the role

- **WHEN** 环境变量中的邮箱完成验证并变为可登录
- **THEN** 该账号拥有管理员角色

### Requirement: Admin sees presence by client

管理员 MUST 能看到每个用户的邮箱、角色、账号状态、注册时间和最近活跃时间。最近 90 秒内有心跳、普通请求或流式连接活动的未过期会话 MUST 显示为在线，并标明来源是 Web、桌面或两者。超过 90 秒 MUST 显示为离线，同时保留最近活跃时间。

#### Scenario: Web and desktop both online

- **WHEN** 同一用户的 Web 会话和桌面会话都在最近 90 秒内有活动
- **THEN** 管理员列表将该用户标为在线，并同时显示 Web 和桌面

#### Scenario: Idle session becomes offline

- **WHEN** 某用户最后一次活动早于 90 秒，且没有更新的会话活动
- **THEN** 管理员列表将该用户标为离线，并仍显示该最近活跃时间

### Requirement: Disable revokes hosted access without deleting local desktop data

管理员 MUST 能停用和恢复其他用户。停用 MUST 使该用户现有托管会话立即失效，Web 工作台和平台模型调用 MUST 被拒绝。系统 MUST NOT 因停用而删除该用户桌面电脑上的本地数据。管理员 MUST NOT 停用自己。

#### Scenario: Disabled user loses the next hosted request

- **WHEN** 管理员停用一个正在使用 Web 或平台模型的用户
- **THEN** 该用户随后的托管请求被拒绝

#### Scenario: Admin cannot disable self

- **WHEN** 管理员对自己执行停用
- **THEN** 操作失败，该管理员会话仍然有效
