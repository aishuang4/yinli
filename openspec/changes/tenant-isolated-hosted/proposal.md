# Proposal

## Why

产品有两种形态：

- **Web（托管）**：多租户系统。所有数据存在我们的服务器上，每个用户的数据与工作空间必须严格隔离。用户注册后自动在模型平台（key2api）开一把 Key 使用平台模型，也可以自己配置模型 URL 与 Key。
- **Client（桌面）**：本地优先。所有数据存在本机工作空间。可以不登录使用；登录与否的唯一区别是能不能用云端的平台模型。

上一版 `hosted-account-access` 用 5 张根表上的 `owner_user_id` 加"查询后在 Python 里过滤"做 Web 隔离，代码审查发现以下结构性问题：

1. **漏写一处归属就出事。** `ensure_chat_session`（群聊）和 `ensure_scheduled_job_session`（定时任务）创建会话时不写归属。中间件规定只要库里有一行空归属，所有用户的所有 `/api/*` 都返回 409。管理员执行 `assign_unowned` 时又会把别人的会话划给自己。
2. **只有根表有归属。** Run、通知、时间线、记忆候选、Trace、Workflow 都没有归属。定时任务除列表外的 get / patch / pause / run-now / delete 都不校验归属。拿到 ID 就能越权读写。
3. **全局资源不分人。** Agent、Skill、MCP、HTTP 工具对所有托管用户可写。Agent 专业区记忆按 `agent_id` 存，A 的沉淀会注入给 B。密钥存储按 `provider_id` 取 Key，不同用户同名供应商会串 Key。服务器 HOME 目录下的 Skill / Subagent 会被所有租户发现。
4. **后台执行没有用户身份。** ticker、群聊续跑不在请求里，`current_owner_id()` 为空：平台模型直接失败，习惯记忆写进 `default`。
5. **ChatStore 每次写入都整库重写**（`DELETE FROM chat_sessions/chat_messages` 后全量插入），多用户并发会丢消息；读取时被跳过的坏行会在下一次写入时被永久删除。另外 `files`、`clarification_requests`、`clarification_resolved` 根本没落库，重启后丢失。
6. **Web 端可以在服务器上执行任意命令。** 内置 MCP 都是 stdio 类型，Bash、脚本型 Skill、script 定时任务都在宿主上执行。

现有业务代码本来就是按"一个用户、一份数据根"写的。把 Web 改成**每个用户一份独立数据根**，隔离就从"每个查询都要记得过滤"变成"物理上看不到"，上面第 1、2 类问题整体消失；桌面端是固定的 `local` 租户，两端走同一条代码路径。

Web 仍在改造期，现有托管数据**直接清空，不做迁移**。

## What Changes

- **租户上下文**：新增 `src/tenancy/` 包，提供 `TenantContext`（`tenant_id`、`data_root`、`kind`）、`current_tenant()`、`use_tenant()`、`spawn_in_tenant()`。托管模式下取不到租户一律抛错，不回落到默认目录。
- **路径与存储按租户解析**：`runtime_paths.py` 中所有与数据相关的模块常量改为函数；`database.py` 按库文件路径维护初始化状态和锁；所有模块级缓存和注册表按 `tenant_id` 分桶。
- **控制面拆分**：账号、会话、验证码、key2api Key、租户表、平台调用审计放进独立的 `control/control.db`。租户库里不再有任何账号表或归属字段。桌面端不创建控制面。
- **删除行级归属方案**：移除 `server/auth/ownership.py`、`owner_user_id` 的读写、空归属 409 闸门、`assign_owners` 命令和 `/api/admin/ownership/assign`。
- **租户生命周期**：邮箱验证通过时创建租户目录并播种默认 Workspace / Project / 预置资源；停用时吊销会话并停止调度；删除时取消在途任务、清理沙箱容器、删除整个租户目录。
- **后台任务进入租户**：启动对账、ticker 扫描、firing、群聊续跑、记忆后处理都显式进入所属租户。
- **ChatStore 改为增量写**，补齐丢失的消息字段，然后再开启 `foreign_keys`。
- **模型来源分两层**：平台模型 `platform:*` 加自定义模型。Web 用户可编辑自己租户的 `models.json`，密钥按租户加密存储。桌面登录后经云端 OpenAI 兼容转发接口使用平台模型，走完整 Agent 运行时，Key 不下发到本机。删除 `platform_chat.py` 的纯文本转发和 `/api/models/complete`。
- **Web 执行安全**：只允许远程 MCP（SSE / WebSocket）；Bash、脚本型 Skill、script 定时任务只在容器沙箱里执行；沙箱接入方式可配置（本机 docker.sock 或远程 Docker 主机）；出站请求统一做 SSRF 校验；按租户配额；禁止发现服务器 HOME 下的 Skill / Subagent。
- **能力开关**：`/api/auth/config` 按运行形态下发 `capabilities`，前端隐藏入口，后端每个入口单独校验。
- **前端**：登录 / 退出 / 切换账号时重置所有按租户区分的状态；桌面端加可选登录入口；模型页分平台层与自定义层；Web 端隐藏本地路径、stdio MCP、本地执行等入口。

## Capabilities

### New Capabilities

- `tenant-isolation`：租户数据根、控制面、租户生命周期、后台任务的租户上下文、增量写会话。
- `model-sources`：平台模型与自定义模型两层；桌面登录经云端转发使用平台模型。
- `hosted-execution-safety`：Web 端远程 MCP、沙箱执行、SSRF 防护、配额、能力开关、禁止发现服务器 HOME。

### Modified Capabilities

- 无已归档的主规格受影响。未归档的 `hosted-account-access` 中 `hosted-user-data` 的行级归属要求（其 tasks 4.1、4.5）由本 change 的 `tenant-isolation` 取代；"只有管理员写模型 Key"由 `model-sources` 取代为"只有管理员写平台模型目录，用户可写自己的自定义模型"。两份 change 落地后一起归档，归档时以本 change 为准。

## Non-goals

- 不做 Web 与桌面之间的数据同步。
- 不做多进程或多机水平扩展。后台 Run 注册表在进程内存，Web 按**单进程 uvicorn（workers=1）多租户**运行。
- 不迁移现有托管数据；桌面端已有数据库原地保留（多出来的 `owner_user_id` 列与 `auth_*` 表保留不用，不做 DROP）。
- 不改 Chat SSE 帧格式与群聊事件字段。

## Impact

- 后端：新增 `src/tenancy/`、`src/storage/control_db.py`、`src/security/egress.py`、`src/sandbox/`；改造 `config/runtime_paths.py`、`storage/database.py`、`storage/chat_session_records.py`、`server/routes/chat/store.py`、`config/model_catalog.py`、`config/secret_store.py`、`config/runtime_settings.py`、`server/auth/*`、`server/main.py`、`scheduler/*`、`group_studio/*`、`memory/*`、`mcp/registry.py`、`skills/registry.py`、`catalog/*`、`subagents/*`、`agents/factory.py`、`tools/*`，以及约 28 处模块级缓存。
- 测试：44 个测试文件里的 `DB_PATH` / `_initialized` monkeypatch 改为租户 fixture；新增隔离测试矩阵。
- 前端：`features/auth/*`、`stores/*`、`pages/ModelsPage.tsx`、`pages/MCPPage.tsx`、`features/workspaces/ContextCreateModal.tsx`、`features/agents/sections/AgentGuardrailsSection.tsx`、`pages/ScheduledJobsPage.tsx`、`features/settings/*`。
- 部署：数据卷布局改为 `control/`、`shared/`、`tenants/`；新增 `AGENT_WORKSTATION_SECRET_KEY`（主密钥）和沙箱相关环境变量；Web 后端固定单 worker。
- 文档：`AGENTS.md` 项目概述、`docs/architecture-overview.md` 运行形态章节、`docs/web-service-plan.md`、`deploy/` 下的说明。
