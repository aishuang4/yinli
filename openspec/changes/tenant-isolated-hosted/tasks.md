# Tasks

> 按编号顺序执行。每个任务都引用 `design.md` 的章节（如 §5.1），实施细节以 design 为准。每组结束时有一个门槛（§17.3），门槛不通过不进入下一组。
>
> 开始之前：当前工作区有大量未提交改动（scheduler / runtime / chat 等约 75 个文件）。先由仓库负责人把它们提交或单独建分支，本 change 在干净的基线上开分支实施。

## 0. 准备

- [ ] 0.1 停掉现有 Web 部署并清空数据卷（§15 最后一条）。在 PR 描述中注明"托管数据已清空，不做迁移"
- [x] 0.2 在 `pyproject.toml` 显式加入 `cryptography` 依赖，执行 `uv sync`（§11.3）
- [x] 0.3 逐项核对 design 中标注"实施前核对"的假设，把结论记在 PR 描述里：model_catalog 能否接受空目录（§9.1-8）；AgentScope Docker 客户端是否读取 `DOCKER_HOST`（§12.4.2）；skill_paths 的挂载方式（§12.4.2）；workspace backend 能否单次执行命令（§12.4.5）；模型 SDK 能否注入 httpx client（§11.4）；`mcp/registry.py:437` 的线程回调是否访问租户数据（§10.7）；key2api 是否支持 tools 和 stream_options（§11.6）；桌面会话 TTL（§11.6）；`/api/sandbox/cases` 的用途（§12.2）；`ModelEntry.capabilities` 的字段集（§11.5）；firing 状态枚举（§12.6）

## 1. 租户上下文与路径

- [x] 1.1 新建 `backend/src/tenancy/__init__.py`、`context.py`，按 §3.1 实现。测试 `test_tenant_context.py`（§17.1）
- [x] 1.2 新建 `tenancy/registry.py`，实现 `TenantScoped`（含 `get` / `peek` / `replace` / `evict`）、`evict_tenant`、`evict_all`（§3.2）
- [x] 1.3 改写 `config/runtime_paths.py`（§4.1）：删除数据类常量，新增函数；拆分 `ensure_process_directories` / `ensure_tenant_directories`（后者按 tenant_id 只执行一次）
- [x] 1.4 按 §4.2 的表逐文件修改调用点。重点：所有默认参数里的路径常量改成 `None` 再在函数内解析；删除模块级 `DATA_DIR` / `WORKSPACE_DIR` / `SKILL_PACKAGES_DIR` / `SUBAGENT_USER_DIR` / `CHAT_DATA_PATH` / `SECRET_DIR`。完成后执行 §4.2 末尾的 grep，结果必须为空
- [x] 1.5 `user_home_root()` 在 hosted 下返回 `None`，按 §12.7 修改四处发现逻辑
- [x] 1.6 修改 `backend/test/conftest.py`：加入 autouse `local_tenant` fixture，改写 `isolated_model_catalog`（§5.3）
- [x] 1.7 新建 `backend/test/hosted_support.py`，提供 `hosted_env`、`make_user`、`tenant_of`（§5.3），暂时只实现到能建控制面为止，任务组 3 再补全
- [x] 1.8 测试 `test_tenant_paths.py`（§17.1）

## 2. 存储与缓存

- [x] 2.1 改写 `storage/database.py` 的连接与初始化（§5.1）：`db_path()`、`_lock_for`、线程安全的 `_TenantLockProxy`、`_initialized_paths`、`forget_db` / `forget_all`。**这一步先不要开启 `foreign_keys`**
- [x] 2.2 删除 `DB_PATH`、`PROJECT_ROOT`、`_initialized`；`DEFAULT_GUARDRAILS` 改为 `default_guardrails()`，并修改全部引用（§5.2）
- [ ] 2.3 从 `ensure_db()` 删除 `AUTH_SCHEMA_SQL` 和 `owner_user_id` 补列循环；默认 Project 根目录改用 `default_project_root()`（§5.2）。补列与账号表暂留，等 3.2 / 3.8 把写入迁走后再删，否则新库无法满足现有写入
- [x] 2.4 修改测试：删除约 87 处 `DB_PATH` / `_initialized` 的 monkeypatch，以及 25 个文件里对路径常量的 patch（§5.3）。`uv run pytest` 全绿后再继续
- [x] 2.5 ChatStore 增量写：先补 schema（§7.1），然后实现 `ChatSessionRecordStore` 的新方法（§7.2）
- [x] 2.6 改写 `server/routes/chat/store.py`（§7.3）及其他调用方（§7.4）；实现 `import_legacy_chat_store_once`（§7.5）；删除 `load_store` / `save_store` / `load_chat_store` / `save_chat_store`
- [x] 2.7 按 §5.4 的顺序开启 `foreign_keys`：先 grep 不带 WHERE 的整表 DELETE，逐一确认；开启后修正所有写入顺序问题；local 租户启动时执行 `foreign_key_check` 并记录日志
- [x] 2.8 测试 `test_chat_store_incremental.py`（§17.1）
- [x] 2.9 按 §6 的清单逐项处理缓存。重点：删除 `agents.py:279`、`mcp/registry.py:533`、`skills/registry.py:865` 三处 import 时加载，改为 `TenantScoped` 懒加载；`_agent_cache` 等的 key 加 `tenant_id`；`secret_store` 的缓存 key 加 `tenant_id`；向量库 backend 按租户；通知 hub 按租户投递
- [x] 2.10 后台 Run 按租户过滤（§10.6），`PENDING_INTERACTIONS` 记录并校验 `tenant_id`（§6）
- [x] 2.11 **门槛 A**（§17.3）

## 3. 控制面与租户生命周期

- [x] 3.1 新建 `storage/control_db.py` 和 `storage/control_records.py`（§8.1、§8.2）
- [x] 3.2 `storage/auth_records.py`（17 处）和 `platform_models.py` 的审计改用控制面（§8.3）；lifespan 中 hosted 才初始化控制面
- [x] 3.3 desktop 下账号接口和管理员接口返回 404（§8.4）
- [ ] 3.4 重写 `HostedSessionMiddleware`（§3.3）：会话 → 租户状态 → `set_tenant`；删除 409 闸门
- [ ] 3.5 新建 `tenancy/lifecycle.py::ensure_tenant`（§9.1），接入验证、登录、中间件兜底三个触发点。注意第 7 步要重新绑定资源白名单
- [ ] 3.6 按 §9.2、§9.3 重写 `server/routes/workspaces.py` 中 hosted 的 Project 根目录逻辑
- [ ] 3.7 实现 `disable_tenant` / `enable_tenant` / `delete_tenant`（§9.4），`server/auth/service.py` 改为调用它们
- [ ] 3.8 按 §8.5、§16 的清单删除行级归属的全部代码和测试。完成后执行 §16 的 grep，结果必须为空
- [ ] 3.9 补全 `hosted_support.py`；测试 `test_control_db.py`、`test_tenant_lifecycle.py`（沙箱部分先跳过，任务组 6 补上）

## 4. 后台任务

- [ ] 4.1 lifespan 改为按租户执行启动对账（§10.1），单个租户失败不阻塞其他租户
- [ ] 4.2 调度器改造（§10.2）：拆分 bootstrap、tick 遍历租户、记录 firing 所属租户、`set_running` 在 hosted 下的行为
- [ ] 4.3 核对群聊和记忆后处理的 `create_task` 调用点都在租户上下文内（§10.4、§10.5），不满足的改用 `spawn_in_tenant`
- [ ] 4.4 记忆用户固定为 `default`：删除 `_hosted_memory_user` 和 `_habit_user_id`（§10.5）
- [ ] 4.5 `cancel_tenant_activity` 接入 Run、firing、群聊（§9.4 第 4 步）
- [ ] 4.6 测试 `test_tenant_background.py`、`test_tenant_isolation.py`（§17.1）
- [ ] 4.7 **门槛 B**（§17.3）

## 5. 模型来源

- [ ] 5.1 secret_store 增加 hosted 的 `tenant_encrypted` 后端，缺少主密钥时启动失败（§11.3）。测试 `test_secret_store_tenant.py`
- [ ] 5.2 新建 `models/platform_layer.py`；改写 `resolve_model_binding` 的 platform 分支和默认模型解析顺序（§11.2）
- [ ] 5.3 `server/routes/settings.py` 去掉管理员限制，加 `baseUrl` 出站校验（§11.4；egress 模块在 6.1 实现，这里可以先写调用，6.1 完成后再补测试）
- [ ] 5.4 平台模型能力表与管理员接口（§11.5）；删除部署级 `models.json` 作为平台目录的分支；hosted 缺少 key2api 令牌时启动失败
- [ ] 5.5 云端转发网关 `server/platform_gateway.py`（§11.6）。测试 `test_platform_gateway.py`
- [ ] 5.6 桌面端新建 `server/auth/cloud_client.py`，改写 `/api/auth/desktop/session` 和 `_desktop_provided_models`，兼容旧文件里的 token，新增 `cloud_base_url` 设置（§11.6）。测试 `test_cloud_client.py`
- [ ] 5.7 删除 `platform_chat.py`、`send.py` 中的特殊分支、`/api/models/complete`、`invoke_platform_model`、`complete_with_platform_key`（§11.6）
- [ ] 5.8 **门槛 C**（§17.3）

## 6. Web 执行安全

- [ ] 6.1 新建 `security/egress.py`，把 web_fetch 里的校验逻辑迁过来并接入各调用点（§12.5）。测试 `test_egress.py`
- [ ] 6.2 新建 `tenancy/capabilities.py`，`/api/auth/config` 下发能力（§12.1）
- [ ] 6.3 MCP：hosted 下内置 stdio 不可见，拒绝 stdio 的注册 / 安装 / 启用，远程 URL 走出站校验（§12.2、§12.3）
- [ ] 6.4 沙箱配置项和 hosted 启动检查（§12.4.1）
- [ ] 6.5 新建 `sandbox/hosted_workspace.py`、`sandbox/provider.py`、`sandbox/fake.py`（§12.4.2、§12.4.8）。容器名必须包含租户和 Project 的哈希
- [ ] 6.6 `agents/factory.py` 接入（§12.4.3），核对 `create_agent` 的全部调用点并传入 project_id；Agent 保存和运行时都强制改写 guardrails（§12.2）
- [ ] 6.7 hosted 下 Bash 只能在沙箱中执行的断言，以及系统提示词说明（§12.4.4）
- [ ] 6.8 script 定时任务走沙箱（§12.4.5）
- [ ] 6.9 删除 `_hosted_personal_project`；hosted 下不初始化宿主 venv（§12.4.6、§12.4.7）
- [ ] 6.10 删除租户时清理沙箱容器（§9.4 第 2 步），补上 `test_tenant_lifecycle.py` 中的沙箱用例
- [ ] 6.11 配额（§12.6）。测试 `test_tenant_quota.py`
- [ ] 6.12 测试 `test_hosted_execution.py`（§17.1）
- [ ] 6.13 **门槛 D**（§17.3）

## 7. 前端

- [ ] 7.1 `authStore` 加入 `capabilities` 和 `cloudAccount`；`HostedSessionGate` 读取它们（§14）
- [ ] 7.2 新建 `stores/resetTenantState.ts` 并接入登录、退出、切换账号；依赖用户的数据加载加上 `user?.id`（§14）
- [ ] 7.3 按能力开关隐藏入口：Project 路径、执行后端、stdio MCP、script 任务提示（§14）
- [ ] 7.4 模型页分为平台层和我的模型，hosted 下普通用户可编辑；管理员面板加"平台模型"页签（§14）
- [ ] 7.5 桌面云端登录入口和 expired 状态提示；`useProvidedModels` 改用 `cloudAccount`（§14）
- [ ] 7.6 Key 引导弹窗的 localStorage key 按用户区分；hosted 下不弹出（§14）
- [ ] 7.7 前端测试：`authRules.test.ts`、`resetTenantState` 测试、能力开关渲染测试。`npm test` 全绿

## 8. 部署、文档与验收

- [ ] 8.1 按 §15 修改 `docker-compose.web.yml`、`Dockerfile`、`backend.env.example`，新增 `deploy/SANDBOX.md`、`deploy/BACKUP.md`
- [ ] 8.2 更新 `AGENTS.md` 的项目概述（删除 `owner_user_id`、`assign_owners`、"Web 与桌面数据各自独立"的旧说法，改写为租户模型）、`docs/architecture-overview.md` 的运行形态章节、`docs/web-service-plan.md`
- [ ] 8.3 在 PR 描述中说明 `hosted-account-access` 的 4.1、4.5 已被取代，两份 change 一起归档
- [ ] 8.4 **门槛 E**（§17.3）：全量后端和前端测试通过；在全新机器上按文档部署，两个账号完成手动隔离验收
