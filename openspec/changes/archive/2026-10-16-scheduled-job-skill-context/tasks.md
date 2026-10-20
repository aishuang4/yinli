# Tasks

## 1. Job Context 合同与存储

- [x] 1.1 在 `backend/src/scheduler/models.py` 增加冻结 Skill manifest、Skill 版本记录和 `context_policy` 的 Pydantic 类型；限制策略值为 `fresh`（为未来策略保留可扩展解析点），为旧数据提供空 manifest/fresh 默认值；用模型单测验证 alias、去重输入和非法策略返回明确校验错误
- [x] 1.2 扩展 `ScheduledJob`、REST create/update schema、Job 文件 serializer/deserializer 和 Chat `ScheduleJob` 工具输入/输出，暴露 `requiredSkills`、`contextPolicy`、manifest hash 与预算诊断；验证旧 payload 不带新字段仍能读取，script Job 仍拒绝 agent/skill/context 字段
- [x] 1.3 在 `backend/src/storage/database_schema.py` 与 `backend/src/storage/database.py` 用 `_ensure_column()` 增加 `scheduled_jobs.required_skills`、`scheduled_jobs.context_policy` JSON/TEXT 列，默认分别为 `[]`、`fresh`；运行迁移测试并确认旧 SQLite 可启动、现有 scheduled_jobs 行不被自动扩容
- [x] 1.4 更新 `backend/src/storage/scheduled_job_records.py` 的 insert/update/read/default 字段集合和 JSON 编码；验证原子保存 manifest 与 context policy、空/畸形 JSON 的兼容回退，以及旧字段 `skill_ids` 仍可 round-trip
- [x] 1.5 为 `scheduled_job_runs.output_manifest.skillContext` 约定稳定的 JSON 结构（canonical IDs、hash、policy、预算、校验阶段和错误码），不保存 Skill 正文；用存储测试验证 Unicode、缺省字段和失败 firing 也能持久化 manifest

## 2. Skill 权威来源与冻结 manifest

- [x] 2.1 在 `backend/src/catalog/skills.py` 提供读取包内原始 `SKILL.md` UTF-8 字节、完整 SHA-256、source scope/path、enabled 状态和资源元数据的纯函数；保留现有 external Skill ID 生成规则，测试换行/尾随空格会改变 hash 但不会意外改变既有 stable ID
- [x] 2.2 新增 scheduler 可调用的无路由依赖模块，负责合并 `job.skill_ids` 与 capability contract `skill_ids`（Job 顺序优先、contract 追加、去重保序）、解析 required Skills、检查安装/启用/Project binding/来源可读性，并生成 manifest；测试 contract 有 Skill 而旧 Job `skill_ids` 为空时不会漏加载
- [x] 2.3 在同一模块实现 confirm/firing 共用的 hash 校验与错误分类（`skill_missing`、`skill_unbound`、`skill_version_changed`、`skill_metadata_missing`）；验证 SQLite `content` 为空或与包文件不一致时仍以包内 `SKILL.md` 为准
- [x] 2.4 计算 required Skill 是否需要 Bash/其他受限工具时复用 `frozen_skills_require_bash`、Job Agent 资格和现有 unattended policy；验证 Skill 正文含脚本不会自动把 Bash/tool scope 改成全开

## 3. 创建、确认和资源变更流程

- [x] 3.1 在 `ScheduledJobService.create_job()` 保存用户显式 Skill ID 和 `fresh` policy，但不从 Agent profile 或 `availableToAllAgents` 自动追加；验证创建阶段仍可返回 pending_confirmation，且共享开关变化不会改写草稿
- [x] 3.2 在 `confirm()`、编辑后重新确认和 `resume()` 接入 manifest 冻结：先校验 Project/Agent/Job Agent，再合并 contract、读取包正文、计算 hash 和预算元数据，最后原子写入 active Job；验证任一 Skill 缺失、未启用、未绑定、正文不可读或策略非法都会 422/保持非 active
- [x] 3.3 把 `required_skills`、`context_policy` 及其依赖字段加入 `_RECONFIRM_FIELDS`；验证修改 Skill、Agent、Prompt、tool scope 或 policy 后 Job 不会继续沿用旧 confirmed manifest，必须重新确认
- [x] 3.4 更新 `_assert_resources_available()`、`pause_for_resource_change()`、Project binding 变更和 Skill 删除/停用钩子，使其同时索引 canonical required Skill 与旧 `skill_ids`；验证资源解绑会主动暂停匹配 Job，并写现有 `job_paused` 通知
- [x] 3.5 在 `backend/src/scheduler/capability_contract.py`/`feasibility.py`/`firing_toolkit.py` 保持资源合同职责边界：Skill canonical 集合共用解析模块，MCP/HTTP/knowledge 仍按 claimed ∩ live 裁剪；验证 scheduler 不 import `src.server.routes.*`，且合同外新资源不会到期注入

## 4. Scheduled firing Context 装配

- [x] 4.1 重构 `build_scheduled_run_context()` 的冻结 Skill 路径，使用 canonical manifest 解析和 hash 校验，生成 `used_skills`、`skill_paths`、`used_skill_details` 与 `forced_skills`；验证任何校验失败都发生在 Agent 创建/模型调用前，并返回可分类的 `ScheduledChatLaunchError`
- [x] 4.2 在 `scheduled_launch.py`/`firing.py` 把 Context 校验失败映射到 `scheduled_job_runs.error_code/error_message/output_manifest.skillContext`，沿 `failure_policy`、firing 状态机和 scheduler notification 收口；验证失败 firing 没有有效 assistant 运行，且 ticker 不会把它误报为成功
- [x] 4.3 确认 `react_runtime.py` 在 `create_main_agent_from_spec()` 后、第一次 `UserMsg`/模型调用前调用 `activate_forced_skills()`；对 required Skill 使用不会被普通 Chat 的 oldest-skill eviction 淘汰的标记/路径；测试首轮 context 直接含 `<activated-skill>`，无需先调用 `Skill`
- [x] 4.4 为 scheduled runtime 增加稳定的 Skill Context 规则 prompt（可复用 `_build_skill_context_prompt()` 的规则部分），明确已激活正文、hash 和 references/scripts 按需读取；验证最终 `system_prompt` 不包含任何 `SKILL.md` 正文，只包含稳定规则和常规 Project/Job 段
- [x] 4.5 保持 Job 的 `SCHEDULED_JOB_UNATTENDED_DENYLIST`、Write scope、Project∩Agent filesystem scope、MCP/HTTP/knowledge 裁剪、无 `AskUser`/Plan/DelegateTask/Job 管理工具；用权限测试验证 Skill 指令不能扩大工具集合或越过 denylist

## 5. Context policy、预算与恢复

- [x] 5.1 把 `fresh` policy 接入 scheduled session 创建与 AgentScope session key，确保每次 firing 不读取上一 firing 的完整 chat/tool history；验证连续两次 daily firing 的 context、session state 和 output manifest 相互隔离
- [x] 5.2 实现 required Skill 单项、总量和 Context reserve 的预算检查，字符数只用于诊断，token 估算复用 AgentScope `count_tokens`/现有 trace 统计；验证超限返回 `skill_context_budget_exceeded`，不静默截断、不删除 required Skill、不启动模型
- [x] 5.3 将 required Skill 的 manifest/hash 与 `session_context_state`/`middle_context` 恢复衔接到现有 `SkillContextMiddleware`；验证压缩、实例重建和进程重启后仍恢复同一正文，无法恢复时以明确错误终止，而不是无 Skill 继续
- [x] 5.4 保留 `references/` 和 `scripts/` 按需读取，不把整包文件灌入 Context；测试仅有 `SKILL.md` 预加载、引用文件仍受 Read/Bash 权限和工具结果预算限制
- [x] 5.5 在 `CONTEXT_USAGE`/trace payload 中增加 required Skill 数量、预加载 token、总预算、policy 和 compression/recovery 结果；验证指标与 AgentScope token 计数同源，不引入 bytes/字符折算的第二套口径

## 6. API、文件定义和产品投影

- [x] 6.1 更新 `/api/scheduled-jobs` 的 serialize/create/update/confirm 响应，展示 required Skill IDs、hash、版本状态、context policy 和待重新确认原因；验证 hash 只读展示、正文和 secret 不出现在 API
- [x] 6.2 更新 `.agents/jobs/<id>.md` 的读写同步和 catalog job hydration，确保 SQLite 与文件使用相同 canonical manifest；验证文件写失败不会返回一个缺少冻结元数据的 active Job，且旧文件仍可导入为 fresh/待确认
- [x] 6.3 更新 Job 编辑/确认 UI 或其 API 消费层，明确“required Skill 已冻结”“正文版本变化需要重新确认”“fresh”语义和预算错误；验证 `availableToAllAgents` 切换不会出现在 Job required Skill 列表中，也不会自动增删 Job 能力
- [x] 6.4 更新 `docs/scheduled-jobs-v2-agent-activation.md`、`docs/architecture-overview.md` 和相关 Job/Skill 契约，写清正文进入 Context、不进 system prompt、hash 漂移、权限边界和失败通知；通过文档链接/搜索检查不残留“Job 继承 Agent 全量 Skills”的旧表述

## 7. 回归与集成验证

- [x] 7.1 增加模型/存储单测覆盖 manifest schema、raw hash、canonical merge、旧行迁移、content 为空、Project/Agent binding 变化和 context policy 校验；运行 `uv run pytest backend/test/<相关测试>` 并确认原有 scheduler/skill 测试不回归
- [x] 7.2 增加 scheduled Context 单测覆盖：首轮 `<activated-skill>`、system prompt 无正文、多个 required Skill 顺序、contract-only Skill、hash/version 失败、Skill unbound、Skill deleted、空 required Skill 和 script Job；验证失败均发生在模型调用前
- [x] 7.3 增加 Context Governance 集成测试覆盖压缩、middle_context/session_context_state 恢复、fresh 隔离、预算超限和 `CONTEXT_USAGE` token 对齐；运行现有 `test_skill_context_middleware.py`、`test_context_middleware.py`、`test_session_context_state.py` 全套
- [x] 7.4 增加安全边界测试：Skill 中的 Bash/HTTP/MCP/AskUser/DelegateTask 指令不能改变 Job toolkit、filesystem scope、approval mode 或 unattended denylist；验证 `availableToAllAgents` 只影响合格 Chat，不影响 Job、Group Worker、基础设施 Agent
- [x] 7.5 增加 firing/notification/API 集成测试：校验失败进入正确 firing 终态、failure policy pause/keep schedule、通知 payload 含可操作原因、runs API 可读 `skillContext` 审计；验证重复 ticker/manual trigger 仍遵守现有幂等约束
- [ ] 7.6 完成全量验证：在 `backend/` 运行 `uv run pytest`，执行项目规定的 Ruff/Mypy/import 检查；随后运行 `openspec validate --change "scheduled-job-skill-context" --strict`，确认所有规划产物和场景格式有效
