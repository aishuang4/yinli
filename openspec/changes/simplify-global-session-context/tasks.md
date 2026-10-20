# Tasks

## 1. 基线、依赖矩阵与迁移护栏

- [ ] 1.1 建立 Workspace/Project 使用矩阵，列出后端 112 个、前端 47 个现有依赖模块及其“全局目录 / Session owner / 外部目录 / 可删除”目标归属，并用仓库脚本验证矩阵覆盖所有 `workspaceId/projectId/workspace_id/project_id` 命中。
- [ ] 1.2 为禁止新增 Workspace/Project 依赖建立静态基线测试，只允许清单中的历史引用；验证新增未登记引用会使测试失败。
- [ ] 1.3 实现 tenant 只读 migration inventory，输出 Workspace、Project、Session、Job、群聊、资源定义、binding 和外部内部文件清单；使用完整历史数据库夹具验证数量与来源路径一致。
- [ ] 1.4 实现迁移备份目录和脱敏导出，覆盖数据库相关表、全局/Workspace/Project catalog、policy、Job 定义及文件 manifest；验证备份不含明文 secret 且可重复生成。
- [ ] 1.5 实现迁移 conflict 预检，覆盖同 ID 不同正文、同名多 ID、路径越界、缺 Project、缺 Session、active Job 无来源；验证存在阻断冲突时不会写新 schema 事实。
- [ ] 1.6 增加 migration feature flag、tenant schema version 和 cutover 状态 API；验证默认部署仍可读取历史用户且不会隐式开始迁移。
  - 复核（2026-10-19 第二轮）：design §16.6 新增启动门，每次启动会自动执行迁移。“不会隐式开始迁移”此后只约束状态 API 和普通读取，对应用例由 18.2 改为 `test_status_api_does_not_start_migration`。

## 2. Session 数据模型与存储

- [ ] 2.1 扩展 Session 记录，增加 kind、status、working directory、directory/resource grant version、归档时间和迁移版本；用 SQLite 原地升级测试验证旧行可读且不伪造 Project root。
- [ ] 2.2 新增 `session_allowed_roots` 存储与领域 record API，包含规范路径、读写模式、来源和授权时间；测试唯一约束、tenant 边界与删除级联。
- [ ] 2.3 新增 `session_resource_grants` 存储与领域 record API，按 session/resource type/resource id 保存 enabled 和有限 override；用并发更新测试验证 grant version 单调递增且不丢写。
- [ ] 2.4 新增 `run_context_snapshots`，冻结目录、资源、模型和审批 snapshot；验证 Run 创建后 Session grant 变化不修改既有 snapshot。
- [ ] 2.5 为 Job、通知、Artifact 和需要迁移的审计记录增加 `owner_session_id/session_id` 新列与索引，不移除旧列；验证外键/索引和历史空值读取。
- [ ] 2.6 将 Session 删除前置检查下沉到 Session 领域 service，统一检测 active Run、HITL、Job 和群聊；用事务竞争测试验证依赖出现时删除失败且不产生半删状态。
- [ ] 2.7 增加 Session archive 和 hard-delete 的独立状态转换，验证 archive 停止新 Run、hard-delete 只允许 idle Session 并保留外部目录。

## 3. Tenant 全局路径与能力目录

- [ ] 3.1 在 `runtime_paths` 定义唯一 global catalog 与 Session roots，新增 `session_root/session_run_root` 等安全 helper；路径测试覆盖桌面、hosted、非法 ID、符号链接和跨 tenant。
- [ ] 3.2 将持久 Agent 权威从 Workspace profiles 收敛到 `<tenant-root>/agents`，实现只读旧目录 adapter；测试相同 Agent 在多个 Session 引用同一 ID。
- [ ] 3.3 将 Skill policy 收敛到 tenant 全局 policy，移除 workspace ID 对稳定 Skill ID 和启停状态的影响；测试跨 Session 编辑正文和开关保持一致。
- [ ] 3.4 将 MCP 与 HTTP Tool YAML 收敛到 tenant 全局目录，停止扫描 Project/Workspace 定义；测试外部 working directory 中的 `.agents/mcp` 和 `.agents/tools` 不进入目录。
- [ ] 3.5 明确 Job Agent/系统 Agent 的全局 seed 与用户 Agent 更新路径，验证重复启动幂等且不写默认 Project。
- [ ] 3.6 将 Agent、Skill、MCP、HTTP、Models 管理 API 改为 global context，不再要求 Session/Workspace/Project query；API 测试覆盖零 Session 用户可以完整管理 catalog。
- [ ] 3.7 实现全局 catalog conflict mapping 和 legacy alias，验证内容相同才自动合并、内容不同不覆盖、Session grant 可按映射迁移。
- [ ] 3.8 检查 secret_store 与 YAML/Session snapshot 的边界，增加扫描测试保证明文 token 不进入 Session root、迁移报告或外部目录。

## 4. SessionContext 与目录授权

- [ ] 4.1 定义不可变 `SessionContext` 和 `GlobalContext`，提供唯一构造入口并禁止业务域直接查询 Workspace/Project 路由；类型与 import-boundary 测试验证依赖方向。
  - 复核（2026-10-19）：`SessionContext` 已定义，但 Chat send / run_context、群聊和 Scheduler 仍通过 `resolve_runtime_context` 查询 Project / Workspace；由 14.4–14.7、16.1–16.2、17.2 完成。
- [ ] 4.2 实现从 Session record 解析 internal root、working directory 和 allowed roots，缺 Session、归档、目录失效或 tenant 不符时返回稳定错误码；单元测试覆盖所有拒绝分支。
- [ ] 4.3 将 Agent filesystem scope 与 Session allowed roots 做规范交集，移除 `/`、cwd 和 `default_project_root()` 空值回退；权限测试覆盖不相交、只读和符号链接绕行。
  - 复核（2026-10-19）：Chat 仍用 `scope_guardrails_to_project(..., runtime_context.project_root)`，Session allowed roots 未参与交集；由 14.4、15.1 完成，20.2 删除 `scope_guardrails_to_project`。
- [ ] 4.4 新增桌面目录授权记录和 Electron 选择目录契约，保存 realpath/访问模式而不写内部状态；主进程测试验证任意前端路径不能扩大白名单。
- [ ] 4.5 实现 hosted allowed-root 枚举与 Docker mount snapshot，只接受 tenant 内服务端路径；测试拒绝宿主 HOME、其他 tenant 和客户端伪造绝对路径。
- [ ] 4.6 实现无外部 working directory 的内部 Artifact 工作区，验证 Read/Write 默认落在 Session root 且前端仍可下载预览。
- [ ] 4.7 实现 Session 目录 grant 更新 API，禁止 active Run 中途改 scope；并发测试验证旧 Run snapshot 不变、下一 Run 使用新版本。

## 5. Session 资源 grant 与 Run snapshot

- [ ] 5.1 实现 Session resources GET/PUT service，校验全局资源存在、类型正确、Agent 资格和 override schema；API 测试覆盖缺失、停用和跨 tenant 引用。
- [ ] 5.2 定义从全局 enabled/shared、Agent profile、Session pinned resources 和 permission mode 计算 Run 候选的确定性顺序；参数化测试覆盖 Skill/MCP/HTTP/知识库组合。
- [ ] 5.3 在 Run 创建事务中原子冻结目录、资源、模型、审批和 Agent snapshot；故障注入验证没有 snapshot 就不会启动模型。
  - 复核（2026-10-19）：`begin_run` / `freeze_run_snapshot` 只在测试中调用，生产 Run 不写 `run_context_snapshots`，factory 无快照时回落旧路径；由 15.1、15.5 完成。
- [ ] 5.4 将恢复、HITL resume、Context compression 和 Agent cache 重建改为读取同一 Run snapshot；重启测试验证不重新扩权或换资源版本。
  - 复核（2026-10-19）：依赖 5.3：生产中 `bind_run_snapshot` 读不到快照，恢复路径实际未绑定快照；随 15.1 重新验收。
- [ ] 5.5 实现全局资源停用后的新 Run fail-closed，进行中 Run 继续使用冻结 snapshot；并发测试证明不反向扫描或主动改写历史 Run。
  - 复核（2026-10-19）：依赖 5.3：生产 Run 没有冻结快照，fail-closed 与“进行中 Run 使用冻结快照”均未生效；随 15.1 重新验收。
- [ ] 5.6 为 snapshot 增加可观测 Trace/审计摘要，隐藏 secret 和正文；验证 UI 能说明实际 Agent/Skill/工具/目录版本。
  - 复核（2026-10-19）：依赖 5.3：生产 Trace 中快照摘要为空；随 15.1 重新验收。

## 6. Chat、Team、Subagent 与 Sandbox Runtime 切换

- [ ] 6.1 新增先创建 Session 再发送第一条消息的 Chat API 流程，旧客户端兼容入口只用于历史 Session；端到端测试验证没有 default Project 写入。
  - 复核（2026-10-19）：前端未调用 `POST /api/sessions`，Session 由首条消息隐式创建；全新 tenant 首条消息返回 422 `Project 不存在: default`，测试夹具自动预置 default Project 掩盖了该问题；由 14.6、19.1、20.1 完成。
- [ ] 6.2 将 Chat send/resume/steer、readiness、runtime builder 和 completion 改用 SessionContext；现有单 Agent 全链路测试验证消息、Run、Trace 和 Artifact 归属一致。
  - 复核（2026-10-19）：`send.py` 与 `run_context.py` 仍以 `RuntimeContext` 作为资源、目录、记忆和 Prompt 来源，`SessionContext` 只做归属校验；由 14.4–14.6 完成，20.2 删除 `RuntimeContext`。
- [ ] 6.3 将 Agent factory 的 cwd、filesystem、Skill loader、MCP/HTTP 注入和 cache key 改用 Run snapshot；缓存测试覆盖两个 Session 使用同一 Agent 但不同目录和资源。
  - 复核（2026-10-19）：factory 的快照分支在生产中不会进入（见 5.3）；由 15.5 完成。
- [ ] 6.4 将 TeamRun 文件状态迁入 `<session-root>/team/<run>`，Leader/Member/综合继承父 snapshot 并继续保持 Member 私有目录；Team 回归覆盖并发、重派和部分成功。
- [ ] 6.5 将 Subagent task store 迁入 Session root，父子只传显式 snapshot 子集；测试 wait/read、HITL、重启对账和结果路径不依赖 Project root。
- [ ] 6.6 将 Plan 文件、todo 工具和审批恢复迁入 Session root；验证刷新恢复和旧 Plan 迁移不读外部 `.agent-workstation/plans`。
- [ ] 6.7 将 Sandbox 本地/Docker工作目录和挂载改用 Session grants；测试无目录、外部目录、hosted 容器和非法 mount。
  - 复核（2026-10-19）：`resolve_sandbox_layout` 只在有 Run 快照时调用，生产中回落旧目录；随 15.1、15.5 重新验收。
- [ ] 6.8 更新 Chat SSE 契约中受影响的 context/resource payload，再修改后端 projection；契约测试验证不新增顶层事件且 replay/Snapshot 一致。

## 7. 文件、Artifact、Trace、Memory 与通知

- [ ] 7.1 用 Session path service 替换 `project_files.py` 的上传、产物、预览、删除路径；文件测试覆盖旧相对路径、越界、软链、外部引用和 Windows 路径。
- [ ] 7.2 建立正式 Session Artifact 记录，包含 producer Run、managed/external 类型、hash、MIME 和路径；验收测试验证 Run 完成不等于 Artifact 验收成功。
- [ ] 7.3 将 Chat completion、工具产物和 Team/Subagent/Job 输出写入 Artifact catalog；测试同一外部目录的两个 Session 不混淆产物。
- [ ] 7.4 将 Trace 列表与详情按 session_id/run_id 授权，移除 Project 过滤；API 测试覆盖 Session 内查询和显式 tenant 全局查询。
- [ ] 7.5 将 timeline retrieve/ingest、Memory audit 和 Skill evolution 来源改为 session_id/run_id；测试长期习惯/专业区仍全局，Session 原文不会跨 tenant。
- [ ] 7.6 将通知改为 owner Session + 可选 Run/Job/Studio 引用，点击行为打开 Session；Web/Electron 通知测试覆盖已删除/归档 Session。
- [ ] 7.7 为 Session 删除实现受管文件/Artifact 清理和外部引用保留；故障注入验证数据库与文件 manifest 可恢复而不删 working directory。

## 8. Scheduled Job 归属和 firing Context

- [ ] 8.1 为 Job create/edit/confirm API 要求 owner Session；从 Chat 创建复用当前 Session，管理页直接创建时建立 `job-owner` Session，验证不能创建 ownerless Job。
- [ ] 8.2 将 Job definition 写入 `<session-root>/jobs/<job>`，移除 Workspace/Project Job 文件扫描和同步递归；测试 list/create 不重入且定义 round-trip。
- [ ] 8.3 将 feasibility、required Skill manifest、MCP/HTTP/tool scope 校验改用 owner Session grant 与全局 catalog；测试删除/停用资源只在 confirm/resume/firing 自检。
  - 复核（2026-10-19）：启用 Job 与 firing（`scheduler/service.py`、`scheduler/chat_bridge.py`、`build_scheduled_run_context`）仍要求 Project 存在并校验 Project binding，与 owner Session grant 双重校验；由 16.1–16.4 完成。
- [ ] 8.4 将 firing 改为 owner Session 下的新 Run，停止创建 `sched-*` 顶层 Session；测试 Run、Trace、通知、Artifact 都能从 owner Session 查询。
- [ ] 8.5 将 firing 模型消息与工具记录保存在 Run/Trace，不写普通 Chat 主消息流；daily 两次 firing 测试验证 `fresh` 不继承 Chat 或前次完整历史。
- [ ] 8.6 将 Job workspace 迁入 Session job/firing 目录并应用 allowed roots；脚本 Job、Agent Job、Windows 路径和 hosted mount 测试通过。
- [ ] 8.7 实现 active Job 的 Session 删除保护及 ticker lease 协调；竞态测试覆盖删除、手动 firing、到点 firing 和进程重启。
- [ ] 8.8 迁移 legacy Job：优先 `created_from_session_id`，缺失时建专用 owner Session，冻结 hash 与历史 firing 不改；完整历史夹具验证无能力扩大。
- [ ] 8.9 增加 runtime-specific Skill loading mode，至少区分 `interactive_on_demand` 与 `scheduled_preloaded`，并把 mode 纳入 Agent/runtime cache key；用两个同 Agent、不同触发源的 schema 测试验证不会复用错误工具集。
- [ ] 8.10 修改 scheduled Toolkit 构造：不注册 AgentScope 内置 `SkillViewer`，不能只依赖 denylist；保留 Skill package mount 供 references/scripts 使用；用工具 schema 断言 scheduled firing 没有 `Skill`/`SkillViewer`，普通 Chat 仍有 `Skill`。
- [ ] 8.11 为 scheduled runtime 增加专用 Skill instruction template，删除普通 Chat 中“发现 Skill 后调用 Skill”的冲突文本；用 prompt snapshot 测试确认 scheduled prompt 不含该旧指令且包含 preloaded-only 规则。
- [ ] 8.12 更新全局 Skill catalog 的模型可见条目，同时输出稳定 `id`、name、description 和无歧义目录；更新 `ScheduleJob.skill_ids` schema/description，验证模型可根据稳定 ID 选择 Skill，唯一名称兼容解析不会产生歧义。
- [ ] 8.13 收窄 `ScheduleJob` / `ConfirmJob` 工具契约：模型只提交显式 `skill_ids`，`required_skills` 改为服务端生成或兼容只读字段；ConfirmJob 返回冻结 Skill ID/hash、owner grant 版本和“首轮自动预加载”状态；增加伪造 manifest、hash 不一致和旧客户端 round-trip 测试。
- [ ] 8.14 固化 scheduled firing 的首轮顺序：preflight/hash 校验、scheduled Toolkit、Run snapshot 恢复、forced Skill activation、Context 完整性断言、Job prompt、首次模型调用；用 fake model/toolkit 集成测试确认任一注入失败都不会调用模型。
  - 复核（2026-10-19）：首轮顺序已实现；但生产 firing 的 Run snapshot 为空，“Run snapshot 恢复”一步实际为空操作，随 15.1 补齐。
- [ ] 8.15 增加 required Skill 防重复调用与防越权测试：scheduled firing 收到旧缓存或模型尝试调用 Skill 时必须在工具层拒绝并审计；references/scripts 只能经已有 Read/Bash 且不扩大 filesystem、网络、MCP、审批或 unattended denylist。
- [ ] 8.16 将实际运行时 Skill Context 审计写入 firing Run/trace：记录 required Skill ID、hash、注入顺序、正文 token、预算、恢复/压缩结果和拒绝的 viewer 调用；验证审计不保存重复正文且与首次 Context 一致。
- [ ] 8.17 增加端到端回归：创建 daily Job 并选择固定 Skill，ConfirmJob 后连续两次 firing；验证两次首轮均包含同一 frozen `<activated-skill>`、system prompt 不含正文、普通 Chat 仍按需调用 Skill，Skill 文件变化则 firing 在模型前失败。

## 9. 群聊工作室归属切换

- [ ] 9.1 从 GroupStudio API/模型/存储投影移除 Workspace/Project 字段，所有上下文由 chat_session_id 反查；契约测试覆盖旧 payload 兼容和跨 Session 拒绝。
  - 复核（2026-10-19）：`GroupStudio` 模型仍含必填 `workspaceId`，删除 / 暂停接口要求 `projectId`，列表仍按 Project 查询，新建群聊仍向 Session 写旧字段；由 17.1–17.4 完成。
- [ ] 9.2 将群聊文件迁到 owner Session root，更新 Artifact 下载和 archive memory；测试不再写外部 `.agent-workstation/group-studios`。
- [ ] 9.3 将 Worker Runtime、目录 scope、资源 snapshot 和模型配置改为每波从 Session grant 冻结；测试波次间更新和波次内不可变。
  - 复核（2026-10-19）：波次 grant 冻结已实现，但 Worker 仍经 `resolve_runtime_context` 与 Project binding 求交集，Project 不存在时无法运行；由 15.2 完成。
- [ ] 9.4 保持独立 Group SSE、V2 状态机、handoff、rerun 和停止语义；运行现有 group-studio 全量测试证明归属改造无行为回归。
- [ ] 9.5 实现 Session archive/delete 对 active studio 的冲突与收尾策略；测试发言中删除失败、idle room 可归档。

## 10. 前端产品模型切换

- [ ] 10.1 从 appStore、TitleBar、管理侧栏和 Settings 移除 active Workspace/Project 与创建选择器；前端测试验证启动不请求 Workspace/Project API。
- [ ] 10.2 新增 Session 创建/编辑 working directory 交互，桌面使用目录选择器、Web 使用服务端允许选项或内部目录；组件测试覆盖取消、失效和只读授权。
  - 复核（2026-10-19）：工作目录只能在 Session 已存在后通过 `SessionScopePanel` 编辑，新建对话时无法指定；由 19.1 完成。
- [ ] 10.3 将 Chat 历史改为 tenant Session 列表，Session 切换原子清理投影、附件和订阅；浏览器测试覆盖不同目录、不同资源和 active Run 防切换。
- [ ] 10.4 新增 Session 资源与目录面板，使用 swatch/checkbox/select 等现有控件显示全局资源、显式 grants 和拒绝原因；桌面视觉 QA 验证无嵌套卡片和文本遮挡。
- [ ] 10.5 将 Skills、Agents、MCP、Tools、Models 管理页改为全局 catalog，不携带 hidden Session/Workspace/Project query；接口 spy 测试覆盖零 Session 状态。
- [ ] 10.6 将 Jobs、Trace、Memory、通知、群聊、文件预览和 Artifact 导航改为 Session owner；浏览器回归验证深链和刷新恢复。
- [ ] 10.7 删除 Workspace/Project 类型、hooks、modal 和文案，并运行 `rg` 证明业务前端无遗留 activeProject/activeWorkspace 使用；保留的 hosted tenant 文案须逐项说明。

## 11. 文件与数据库迁移执行

- [ ] 11.1 实现 Workspace/Project catalog 到全局 catalog 的幂等迁移及 conflict mapping；历史多 Workspace 夹具验证相同内容合并、不同内容阻断。
- [ ] 11.2 实现每个历史 Session 的 Project root → working directory、binding → Session grant 回填；验证 archived/missing Project 不伪造可用目录。
- [ ] 11.3 实现 Session/Job/群聊文件跨卷 staging、fsync、hash 校验和 rename，记录 file manifest；中断点参数化测试覆盖每一步重跑。
- [ ] 11.4 回填 Trace、Memory、通知、Artifact 和 Job owner Session，生成计数与孤立记录报告；验证所有 active 记录有唯一 Session owner。
- [ ] 11.5 实现 tenant cutover 门：暂停新 Run/ticker、校验 active 状态、写 complete marker、切读并恢复；失败注入验证自动保留旧读路径。
  - 复核（2026-10-19）：cutover 门只有库函数和测试，没有 CLI、启动 gate 或 API 执行入口，`migration_enabled()` 也未驱动读路径切换；由 18.1–18.3 完成；第二轮修订取消运行时双读，改为启动门 + 未迁移 Session 准入（design §13、§16.5、§16.6）。
- [ ] 11.6 实现 rollback 工具，把 flag 切回旧读并生成旧版可导入副本，不覆盖新 Session 数据；使用迁移后夹具演练完整回滚。
- [ ] 11.7 提供旧外部 `.agent-workstation` 和 `.agents` 清理报告/显式工具，默认 dry-run 且不删除用户业务文件；安全测试覆盖根目录、软链和未知文件。

## 12. 移除旧行为、文档和最终验收

- [ ] 12.1 在所有入口切换后删除 default Workspace/Project bootstrap、Project binding 和 `default_project_root()` 运行时依赖；启动测试验证全新用户只创建 tenant root。
  - 复核（2026-10-19）：bootstrap 已删除，但 Project binding 与 `RuntimeContext` 运行时依赖仍在，导致全新 tenant 无法发送消息、无法新建群聊；在第 14–17 节完成后重新验收。
- [ ] 12.2 停止旧 API/字段写入并返回明确 deprecation/410；契约测试验证旧参数不能更改 Session 归属或扩大目录。
- [ ] 12.3 删除旧 Project/Workspace catalog 扫描与外部隐藏状态写入；全仓 `rg` 与 import-boundary 测试只允许迁移/回滚模块读取旧来源。
  - 复核（2026-10-19）：`reference_allowlist.json` 仍含 119 个后端文件（含 chat、scheduler、group_studio），测试夹具还用字符串拼接字段名绕过 `rg`；由 13.3、20.1、20.5 完成。
- [ ] 12.4 更新 AGENTS、架构、文件 catalog、Memory、Job、群聊、通知、安装/卸载和项目 TODO，统一使用 tenant root / Session root / working directory / Run workspace 术语。
- [ ] 12.5 运行后端全量 pytest、import boundary、迁移故障注入和多 tenant 隔离测试，记录通过数量与任何既有基线。全量 1294 passed / 2 skipped / 4 failed（2026-10-19 复核日志 `tmp/opsx-apply/backend-final.log`）。import boundary、section 11 故障注入、tenant isolation/background/lifecycle 共 24 passed。
  - 复核（2026-10-19）：4 个失败均为执行环境的 `PermissionError: [Errno 1] Operation not permitted`（socketserver / multiprocessing），不是本变更引入的回归。测试通过不代表生产调用链已切换，见 13.2、20.1。
- [ ] 12.6 运行前端 unit/lint/build、Electron compile，并用 ego-browser 完成桌面和 hosted 的新建 Session、目录授权、Chat、Job、群聊、删除保护与刷新恢复 QA。
- [ ] 12.7 对真实脱敏历史数据执行 dry-run、迁移、校验、cutover 和 rollback 演练，逐项核对 Session/Run/Job/文件/资源计数及 hash。
- [ ] 12.8 运行 `openspec validate simplify-global-session-context --type change --strict`、`git diff --check` 和规格—实现—测试映射审查；存在任何未验证迁移路径时保持任务未完成并禁止旧表清理。
- [ ] 12.9 完成 scheduled Skill 工具契约专项验收：运行 Job/Skill/Context/Runtime 全套测试、Ruff、Mypy、import 检查，并人工核对工具 schema、scheduled prompt、ConfirmJob 响应和 firing audit 四处语义一致；任何仍出现“scheduled Job 先调用 Skill”文本时不得标记通过。

## 13. 复核修复（第二轮）：基线、验收用例与 import 棘轮

> 第二轮复核（2026-10-19）结论：以下行为已经正确，后续任务不得回退：
> - 全新用户首条消息不再返回 422；
> - Session 工作目录有效时优先使用；
> - Session 有 grant 时以 Session 为准；
> - Chat 与 scheduled firing 在模型调用前冻结快照，且快照的 Run ID 与实际 Run 一致；
> - `resource_disabled` 返回 409；
> - Job 创建、启用不要求 Project 行存在。
>
> 第二轮仍未解决的问题由第 13–20 节修复：无 grant 的新 Session 丢失 Agent 资源；scheduled firing 仍要求 Project；工作目录失效时回落 Project root；快照失败被静默跳过；工具复核与 Subagent 仍走 Project；群聊、前端、Electron 和迁移入口未切换；全局测试夹具仍预置 default Project。
>
> 实现规则全部以 design §16 为准。§16 没有覆盖的情况，先修订 design 再写代码。
>
> 每项任务的完成条件：
> 1. 所列测试通过；
> 2. 删除该任务负责的 strict xfail 标记；
> 3. `cd backend && .venv/bin/python -m pytest -q` 除 design §16.7 列出的 4 个环境性失败外全部通过；
> 4. 勾选时在任务下追加一行“证据：<测试名>；<生产调用点 file:line>”。
>
> 执行顺序固定为 13 → 14 → 15 → 16 → 17 → 18 → 19 → 20，节内按编号顺序执行。第 14–18 节全部完成前，本分支不得发布：14.4 之后、18.2 之前，历史 Session 暂时没有工作目录回填和准入检查。

- [ ] 13.1 修复第二轮引入的 3 个失败测试
  - `test/test_chat_runtime_regressions.py` 的 `test_chat_run_context_includes_memory_prompt` 和 `test_chat_run_context_keeps_history_out_of_system_prompt`：monkeypatch 目标由 `run_context.resolve_project_resource_access` 改为 `run_context.resolve_run_resource_access`（14.5 再改为 `run_context.run_resource_access`）。
  - 删除 `test/test_fresh_session_runtime.py`。该文件替换了被验证的函数，并用 `"workspace" + "_id"` 拼接绕过静态护栏；覆盖内容由 13.2 的验收用例取代。
  - 完成判定：上述 2 个用例通过；`test_global_session_foundation.py::test_historical_reference_allowlist_matches_repository` 通过；全量只剩 4 个环境性失败。
- [ ] 13.2 建立全新 tenant 夹具、legacy 夹具和验收用例（先标 strict xfail）
  - 新建 `backend/test/fresh_tenant.py`，按 design §16.7 提供 `fresh_tenant` fixture 和辅助函数。
  - 新建 `backend/test/legacy_fixtures.py`，提供显式 fixture `legacy_project`：创建 Workspace `w-legacy`、Project `p-legacy`（root 在 tmp 下），以及一条 `projectId=p-legacy`、无工作目录、无 grant 的 Session，返回这三个 ID 和 root。内容从 `test/conftest.py::_seed_historical_project` 改写而来，不得 autouse。
  - 新建 `backend/test/test_fresh_tenant_acceptance.py`（全部使用 `fresh_tenant`）和 `backend/test/test_legacy_scope_isolation.py`（使用 `legacy_project`），写入下表全部用例，每个用例标 `@pytest.mark.xfail(strict=True, reason="待 <负责任务>")`。下文的 X、M、H 分别指测试 Agent 配置的非共享 Skill、MCP 和 HTTP 工具。

    | 文件 | 用例 | 断言 | 负责任务 |
    | --- | --- | --- | --- |
    | acceptance | `test_session_without_grants_keeps_agent_resources` | 注册配置 X、M、H 的 Agent，Session 没有任何 grant 行；`candidate_resource_access` 与冻结后的 `run_resource_access` 都包含 X、M、H，且两者相等 | 14.5 |
    | acceptance | `test_fresh_desktop_first_message_reaches_model_boundary` | `POST /api/sessions` 返回 201，`POST /api/chat/send/start` 返回 200；`wait_run` 结束；`run_context_snapshots` 有该 Run 的行；模型边界实参中 `run_snapshot` 非空、`Path(workspace_dir).resolve() == session_artifacts_root(sid).resolve()`、`skill_paths` 含 X 的包路径、`mcp_servers` 含 M、`knowledge_tools` 含 H；该 Session 在 `chat_sessions` 中 `workspace_id == project_id == ""` | 14.6 |
    | acceptance | `test_fresh_hosted_first_message_reaches_model_boundary` | 同上，用 `test/hosted_support.py` 的 `hosted_env` 和 `make_user` 建 hosted tenant，不调用 `seed_legacy_project` | 14.6 |
    | acceptance | `test_missing_working_directory_fails_closed` | Session 的工作目录被删除后，`send/start` 返回 422 且 `detail.code == "directory_unavailable"`；没有 Run 行和快照行；模型边界未被调用 | 14.6 |
    | acceptance | `test_capability_reader_follows_run_snapshot` | M 只通过 Session grant 授权并冻结快照后，reader 的 MCP 列表含 M；撤销 grant 后 reader 结果不变；全局停用 M 后 reader 返回的 capability version 改变 | 14.7 |
    | acceptance | `test_snapshot_failure_creates_no_run` | Session 的 Agent 不在 catalog 时，`send/start` 返回 409 且 `detail.code == "resource_not_found"`；该 Session 没有 `chat_background_runs` 行；模型边界未被调用 | 15.1 |
    | acceptance | `test_main_spec_requires_snapshot` | `build_main_agent_runtime_spec(..., run_snapshot=None)` 抛 `SessionError` 且 `code == "snapshot_required"` | 15.5 |
    | acceptance | `test_subagent_requires_parent_snapshot` | `build_subagent_runtime_context(parent_snapshot=None, ...)` 抛 `snapshot_required` | 15.4 |
    | acceptance | `test_fresh_job_confirm_enable_fire_once` | 确认并启用 Job 后，用 `start_scheduled_chat_run` 触发一次：到达模型边界，有该 Run 的快照行，全过程不出现 `project_archived` | 16.4 |
    | acceptance | `test_fresh_group_studio_create_and_one_worker_run` | `POST /api/group-studio` 请求体不含 `projectId` / `workspaceId`，返回成功；跑一次 Worker 发言到达模型边界；该 Worker Run 有快照行 | 17.4 |
    | acceptance | `test_fresh_tenant_startup_gate_cuts_over` | `ensure_tenant_cutover()` 返回 `{"status": "fresh_cutover"}`，`cutover_state()["cutover"] == "cutover"`，`migration_dir()/complete.json` 存在 | 18.2 |
    | isolation | `test_runtime_never_reads_project_root` | 历史 Session 有 grant、工作目录为 B、旧 Project root 为 A 时，`build_session_context(...).cwd == B`；清空工作目录后 `cwd == session_artifacts_root(sid)`，不是 A | 14.4 |
    | isolation | `test_legacy_binding_never_gates_resources` | 旧 Project binding 关闭 X，而 Agent 配置了 X、Session 有任一 grant 行时，`candidate_resource_access` 含 X | 14.5 |
    | isolation | `test_unmigrated_session_rejected_before_cutover` | 先调用 `set_migration_flag(False, schema_version_value=0)`；不经过 HTTP 与 lifespan（lifespan 会运行启动门并完成迁移），直接调用 `begin_run(<legacy Session>, "run-legacy", agent_id=<该 Session 的 agentId>, start_model=...)`：抛 `SessionError` 且 `code == "session_not_migrated"`，`start_model` 未被调用，`get_run_snapshot("run-legacy") is None`；同一 tenant 新建 Session 调用 `begin_run` 成功；`session_error_http(SessionError("session_not_migrated", ""), default_status=409).status_code == 409` | 18.2 |

  - 完成判定：两个新文件全部收集成功，14 个用例全部处于 xfail 状态；全量只剩 4 个环境性失败。
- [ ] 13.3 建立旧引用 import 棘轮测试
  - 新建 `backend/test/test_legacy_scope_boundary.py`，用 AST 扫描 `backend/src/**/*.py`。违规定义：
    - 导入 `src.runtime_context`、`src.project_resources`、`src.project_files` 中的任意名字；
    - 从 `src.storage.database` 导入或调用 `get_project`、`get_workspace`、`list_projects`、`list_workspaces`、`list_project_resource_bindings`、`upsert_project_resource_binding`、`DEFAULT_PROJECT_ID`、`default_project_root`。
  - 豁免前缀：`src/session_migration/`、`src/storage/`、`src/server/routes/workspaces.py`，以及上述三个被导入模块自身。
  - 测试内定义常量 `LEGACY_IMPORT_ALLOWLIST: frozenset[str]`，取值为编写时扫描到的违规文件全集，并断言“扫描结果 == 清单”。这样新增违规会失败，消除违规后不删条目也会失败。
  - 同一文件增加第二个测试：在 `backend/src` 与 `backend/test` 中查找正则 `["'](workspace|project)["']\s*\+\s*["'](_id|Id)["']`，命中即失败（豁免 `src/session_migration/`）。
    - `src/server/routes/sessions.py:29` 的拼接改为从新文件 `src/session_migration/legacy_fields.py` 导入常量 `LEGACY_SCOPE_FIELDS = frozenset({"projectId", "workspaceId", "rootPath"})`。
    - `test/conftest.py`、`test/hosted_support.py` 中的拼接先列入测试内常量 `CONCAT_PENDING_REMOVAL`，由 20.1 清空。
    - `test/test_session_section12.py` 中的拼接改为从 `src.session_migration.legacy_fields` 导入同一常量。
  - 完成判定：两个测试通过；任务证据中写明 `LEGACY_IMPORT_ALLOWLIST` 的条目数。

## 14. Run 上下文与资源的单一来源

- [ ] 14.1 扩展 Session 侧入口（design §16.1、§16.5）
  - `src/runtime/session_context.py`：`SessionContext` 增加 property `cwd`。
  - `src/sessions/candidates.py`：`select_run_candidates` 增加关键字参数 `agent_id: str | None = None`。
  - `src/sessions/run_binding.py`：`begin_run` 增加必填关键字参数 `agent_id`，并按 §16.1 的顺序执行第 1、3、4、5、6 步；第 2 步由 18.2 接入。`background_runs._freeze_run_snapshot` 改为传 `agent_id`（15.1 删除该函数）。
  - 新建 `src/sessions/legacy_gate.py`，实现 `require_migrated_session`，并在 `reference_allowlist.json` 登记该文件。本任务只实现并单测，不接入任何调用点。
  - 测试：新建 `test/test_run_binding_contract.py`，覆盖三点：
    - `agent_id` 与 Session 的 `agentId` 不同时，候选按传入 Agent 计算；
    - 工作目录失效时，`begin_run` 抛 `directory_unavailable` 且不写快照；
    - `require_migrated_session` 的五个条件逐一取反时都不抛错，全部满足时抛 `session_not_migrated`。
- [ ] 14.2 新增 `src/sessions/run_access.py`（design §16.1 契约）
  - 测试：新建 `test/test_run_access.py`，覆盖：资源类型映射（含 `knowledge` 计入 `http_tool_ids`）；`filter_ids` 保序去重；`allows("agent", ...)`；快照缺失时抛 `snapshot_required`；同一输入下 `candidate_resource_access` 与 `run_resource_access` 结果相等。
- [ ] 14.3 Memory 检索不再按 Workspace / Project 过滤（对齐 design §8 “新的长期记忆路径不依赖它们”）
  - `src/storage/memory_records.py::_query_nodes`：删除 `workspace_id = ? AND project_id = ?` 条件和对应参数。
  - `src/memory/nodes.py::_node_matches_query_filters`：删除 Workspace / Project 比较。
  - `src/memory/nodes.py` 中 `_query_vector` 传给向量库的过滤条件：删除 `workspace_id`、`project_id`。
  - 同日聚合查询（`memory_records.py` 第 155 行附近）和写入保持不变。
  - 测试：新建 `test/test_memory_tenant_scope.py`，写入 `('w1', 'p1')` 和 `('default', 'default')` 两个节点后，以 `workspace_id=""`、`project_id=""` 检索，词法和向量两条路径都能召回这两个节点。
- [ ] 14.4 `resolve_runtime_context` 改为过渡实现（只委托 `build_session_context`）
  - `src/runtime_context.py::resolve_runtime_context` 签名不变，函数体改为：
    - 只调用 `build_session_context(session_id, agent_id=agent_id, run_id=run_id)`；
    - 返回 `RuntimeContext(workspace_id="", project_id="", project_root=ctx.cwd, session_id=ctx.session_id, run_id=run_id, agent_id=agent_id, project_name="", instructions_file="")`；
    - 忽略 `project_id` 实参；`session_id` 为空时抛 `SessionError("session_not_found", "缺少 Session")`；
    - 删除对 `get_project`、`get_session`、`DEFAULT_PROJECT_ID` 和 Project root 的全部读取。
  - 同一任务内必须一并完成的调用方调整（否则旧数据回归）：
    - `group_studio/service.py:226`：删除这次 `session_id=""` 调用，`_maybe_seat_worker` 第二个实参改为 `""`，`ensure_chat_session` 的 `workspace_id`、`project_id` 实参改为 `""`；
    - `scheduler/chat_bridge.py:180`：删除 `runtime_context.workspace_id != req.workspace_id` 检查；
    - `server/routes/chat/react_runtime.py:174`：`scheduled_job_prompt_addendum()` 不再传参；
    - `server/routes/chat/completion.py:205`：条件改为 `resolved_runtime_context is None and persist and session_id`；`_default_runtime_context` 的 `workspace_id`、`project_id`、`project_name` 改为 `""`；
    - `server/routes/chat/run_context.py:251`：错误文案改为“Skill {id} 已不在当前 Session 的可用资源中，不能继续当前 Skill Run。”。
  - 因 cwd 改变而失败的现有用例：凡断言旧 Project root 的，改为先给 Session 设置工作目录再断言，并把用例名写进证据；不得在生产代码中恢复 Project root。
  - 完成判定：去掉 `test_runtime_never_reads_project_root` 的 xfail 标记后通过；`test_scheduled_chat_bridge.py`、`test_group_studio_*.py` 全部通过。
- [ ] 14.5 Chat 与 scheduled 的资源改用 `run_access`
  - `handlers/send.py:149`：`resolve_run_resource_access(runtime_context)` 改为 `candidate_resource_access(session_id, agent_id=agent_id, permission_mode=data.approval_mode)`。
  - `server/routes/chat/run_context.py`：`build_chat_run_context` 与 `build_scheduled_run_context` 中 `project_resources` 的缺省来源改为 `run_resource_access(runtime_context.run_id)`；删除 `resolve_run_resource_access` 的导入。
  - `scheduler/chat_bridge.py` 第 170 行（启动前的 `validate_required_skill_manifest`）：`project_resources=resolve_run_resource_access(runtime_context)` 改为 `candidate_resource_access(session_id, agent_id=req.agent_id, permission_mode=<该 Job 的 approval_mode>)`。
  - `src/project_resources.py`：删除第二轮新增的 `resolve_run_resource_access`。
  - 13.1 的两个用例：monkeypatch 目标改为 `run_context.run_resource_access`。
  - 依赖 Project binding 语义的现有用例（`test_chat_forced_skills.py`、`test_chat_skill_injection_scope.py`、`test_workspace_project.py::test_chat_run_context_applies_project_resource_allowlist`）改为用 Session grant 构造同一断言；断言 Project binding 本身的用例删除。
  - 完成判定：去掉 `test_session_without_grants_keeps_agent_resources` 和 `test_legacy_binding_never_gates_resources` 的标记后通过。
- [ ] 14.6 Chat 入口收口
  - 證據：`test_fresh_desktop_first_message_reaches_model_boundary`、`test_fresh_hosted_first_message_reaches_model_boundary`、`test_missing_working_directory_fails_closed`；`handlers/send.py` 的 `session_error_http`。
  - 新建 `src/server/routes/session_errors.py::session_error_http`（design §16.3）：
    - `routes/sessions.py::_http_error` 改为调用它，`default_status=404`；
    - `handlers/send.py` 两处 `except SessionError` 改为调用它，`default_status=409`；
    - 测试：新建 `test/test_session_error_http.py`，按 design §16.3 的表逐行参数化断言状态码和 `detail` 结构，并断言未列出的 code 返回传入的 `default_status`。
  - `handlers/send.py` 的 HTTP 层：`data.project_id` 非空时返回 410 `legacy_scope_retired`。
  - `sessions/intake.py::ensure_session_for_message` 删除 `project_id` 形参，新建 Session 的两列写 `""`；调用方 `send.py`、`completion.py:194` 同步删除该实参。
  - 现有测试中向 `/api/chat/send`、`/api/chat/send/start` 传 `project_id` 的用例删除该字段；`test_session_intake.py` 中传 `project_id` 给 `ensure_session_for_message` 的用例删除该实参；改动的用例名写进证据。
  - `ChatMessage.project_id` 的说明改为“已退役，非空返回 410”；`ChatSession` 的 `workspace_id`、`project_id` 默认值由 `"default"` 改为 `""`（`models.py:216–217`）。
  - `background_runs.py::start_chat_background_run` 的 `workspace_id`、`project_id` 默认值由 `"default"` 改为 `""`（20.2 删除这两个形参）。
  - 完成判定：去掉 `test_fresh_desktop_first_message_reaches_model_boundary`、`test_fresh_hosted_first_message_reaches_model_boundary`、`test_missing_working_directory_fails_closed` 的标记后通过。
- [ ] 14.7 工具执行复核改读 Run 快照
  - `runtime/authorization_resources.py::RuntimeCapabilityReader.snapshot()` 按 design §16.2 “工具执行复核”六步重写，删除对 `resolve_runtime_context`、`resolve_project_resource_access`、`scope_guardrails_to_project` 的导入。
  - `server/routes/chat/authorization.py:35–36`：`RunAuthorization` 的 `workspaceId`、`projectId` 传 `""`（20.2 删除这两个字段）。
  - `test_chat_authorization.py` 中依赖 Project 的用例改为 Session grant 构造；新增一个用例：Run 冻结后全局停用其中一个 MCP server，复核结果不再包含该 server。
  - 完成判定：去掉 `test_capability_reader_follows_run_snapshot` 的标记后通过；`test_chat_authorization.py` 全部通过。

## 15. Run 快照 fail-closed

> 本节顺序不可调换：先让所有 Run 入口都带上快照（15.1–15.4），最后才把 `build_main_agent_runtime_spec` 的快照参数改为必填（15.5），否则群聊 Worker 会在中间状态无法运行。

- [ ] 15.1 Chat 与 scheduled：先冻结快照，再写 Run 行
  - `server/routes/chat/background_runs.py::start_chat_background_run`：
    - 生成 `run_id` 后、`_persist_run` 之前调用 `begin_run(session_id, run_id, agent_id=agent_id, permission_mode=approval_mode, start_model=lambda snapshot: snapshot)`（`approval_mode` 为该函数的同名参数）；
    - `begin_run` 成功后再调用 `bind_run_snapshot(run_id)`；
    - 删除 `_freeze_run_snapshot` 及其对 `resource_not_found`、Session 缺失的吞错分支；
    - `scheduled_launch.py:122` 的调用随之生效，无需单独改动。
  - 完成判定：
    - 去掉 `test_snapshot_failure_creates_no_run` 的标记后通过；
    - 5.4–5.6、6.7、8.14 的现有测试在不预先手工冻结快照的情况下通过；
    - `rg -n "_freeze_run_snapshot" backend/src` 无结果。
- [ ] 15.2 群聊 Worker 每次发言冻结快照（design §16.2 “群聊 Worker”三步）
  - `group_studio/worker_runtime.py::_run_worker_turn` 中 `capture_wave_grant(...)` 到 `ctx.begin_run(run_id)` 一段，改为以下固定顺序：
    1. `wave_grant = capture_wave_grant(...)` 保留，只用于模型冻结；
    2. `run_id = f"grun_{uuid.uuid4().hex[:16]}"` 移到 `build_worker_runtime_spec` 之前；
    3. 调用 `run_snapshot = begin_run(str(snapshot.get("chatSessionId") or ""), run_id, agent_id=str(member.get("agentId") or ""), permission_mode="bypass", start_model=lambda s: s)`。捕获 `SessionError` 时，先 `pop_unrunnable_head(group_id)`，再 `emit_envelope(group_id=group_id, event_type="GROUP_ERROR", payload={"code": exc.code, "message": exc.message})`，然后 `return`，与现有“成员已静音”分支的处理方式相同；
    4. `spec, ctx, _tools = build_worker_runtime_spec(snapshot, member, wave_grant, run_snapshot=run_snapshot)`；
    5. `ctx.begin_run(run_id)` 保持不变。
  - `build_worker_runtime_spec`：第 195 行的 `build_main_agent_runtime_spec(...)` 传入 `run_snapshot=run_snapshot, apply_snapshot_filesystem=False`；删除 `resolve_runtime_context`、`resolve_run_resource_access`、`_granted_ids`、`_narrow_ids` 的资源用法；wave grant 只保留模型冻结用途。
  - 其余直接调用 `build_worker_runtime_spec` 的位置：
    - 删除 `worker_runtime.py` 中没有任何调用方的测试辅助函数 `worker_permission_mode`；
    - `test_group_studio_archive_memory.py:118` 改为先用 `begin_run` 冻结快照再传入 `run_snapshot=`。
  - `test_group_studio_session.py::test_wave_grant_changes_between_waves_and_stays_inside_a_wave`（第 126–127 行直接调用）改为断言：两次 Worker Run 各有一行快照，且资源分别反映两次 grant。
  - `test_group_studio_session.py` 新增 `test_worker_snapshot_failure_emits_group_error`：成员 Agent 不在 catalog 时发言，产生 `GROUP_ERROR`（code 为 `resource_not_found`），模型边界未被调用，该成员已移出发言队首。
  - 完成判定：上述用例与 `test_group_studio_*.py` 全部通过；`worker_runtime.py` 不再导入 `src.runtime_context` 和 `src.project_resources`。
- [ ] 15.3 Team 成员必须带父快照
  - 證據：`test_member_requires_parent_snapshot`；`backend/src/server/routes/chat/team_runtime/member_runtime.py:431`
  - `server/routes/chat/team_runtime/member_runtime.py`：在第 430 行调用 `build_main_agent_runtime_spec` 之前，`_parent_run_snapshot(ctx)` 返回 `None` 时抛 `snapshot_required`，不得以 `None` 继续。
  - 测试：`test_team_runtime.py` 新增 `test_member_requires_parent_snapshot`。
  - 完成判定：`test_team_runtime.py` 全部通过。
- [ ] 15.4 Subagent 必须带父快照
  - `subagents/runtime_context.py::build_subagent_runtime_context`：
    - 在函数第一行、第一个 `try` 之前加 `if parent_snapshot is None: raise SessionError("snapshot_required", "Subagent 缺少父 Run 快照")`。必须放在 `try` 之外，因为该 `try` 的 `except Exception` 会把错误吞成 `errors`；
    - 删除 `discover_external_skills(...)` 分支（`external_skills` 固定为 `[]`）和 `elif platform_context is not None:` 中调用 `resolve_project_resource_access` 的分支。
  - `subagents/runner.py::_build_child_system_prompt`：`runtime_context` 改为必填，删除 `or build_subagent_runtime_context(definition)`。唯一生产调用点 `runner.py:212` 已传入，`test_subagents_m1.py:1230` 也已传入。
  - `subagents/manager.py:161` 的调用保持不变。
  - 测试调整：
    - `test_subagents_m1.py` 中没有父快照的用例，改为先用 `begin_run` 冻结父 Run 快照；
    - `test_file_skill_integration.py:346` 改为传 `parent_snapshot=<begin_run 冻结的快照>`。
  - 完成判定：去掉 `test_subagent_requires_parent_snapshot` 的标记后通过；`test_subagents_m1.py` 全部通过。
- [ ] 15.5 `build_main_agent_runtime_spec` 必须带快照
  - `src/runtime/agent_spec.py`：`run_snapshot` 改为无默认值的关键字参数，值为 `None` 时抛 `SessionError("snapshot_required", "Run 没有冻结快照")`。
  - 三个调用方 `react_runtime.py:290`、`team_runtime/member_runtime.py:430`、`group_studio/worker_runtime.py:195` 已在 15.1–15.3 传入快照，本项只做核对。
  - 测试中直接调用它而不传快照的位置，全部改为传入快照：`test_group_studio_pr1.py:185/208`、`test_chat_runtime_regressions.py:2635/2675`、`test_chat_plan_mode_integration.py:17`、`test_scheduled_chat_bridge.py:581/603`（`test_factory_run_snapshot.py` 已传快照，不改）。
    - 快照统一用新辅助函数 `test/snapshot_support.py::freeze_test_snapshot(session_id: str | None = None, *, agent_id: str = "default") -> dict` 生成：`session_id` 为空时先 `create_session`，再调用 `begin_run(..., start_model=lambda s: s)` 并返回快照；
    - 原用例断言 `workspace_dir` 或 `filesystem_scope` 时，同时传 `apply_snapshot_filesystem=False`，保持原断言不变。
  - `agents/factory.py::get_or_create_agent` 的 `run_snapshot` 保持可选，供不属于 Session Run 的内部 Agent 使用（design §16.2 最后一段）。
  - 完成判定：去掉 `test_main_spec_requires_snapshot` 的标记后通过；`rg -n "run_snapshot=None" backend/src` 无结果。

## 16. Scheduled Job 去 Project

- [ ] 16.1 `build_scheduled_run_context` 改用 Session 上下文
  - `server/routes/chat/run_context.py` 第 405 行起：删除 `get_project` 调用和两处 `project_archived` 检查。
  - Session 上下文改用 `build_session_context(owner_session_id, agent_id=<Job Agent>, run_id=run_id)`，资源改用 `run_resource_access(run_id)`。
  - owner Session 不存在或已归档时，分别抛 `session_not_found`、`session_archived`，由现有 firing 失败路径记录并通知。
- [ ] 16.2 `scheduler/chat_bridge.py::_start_scheduled_chat_run` 改用 Session 上下文
  - 删除 `resolve_runtime_context` 调用，改为 `build_session_context(session_id, agent_id=req.agent_id)`。
  - 传给后续的 `workspace_id`、`project_id`（第 199–200 行）改为 `""`。
  - `ScheduledRunRequest.workspace_id`、`project_id` 保留字段，但只作只读来源证据，不参与任何判断。
  - `server/routes/chat/scheduled_launch.py`：第 73 行 `ChatMessage(project_id=project_id, ...)` 删除 `project_id` 实参；传给 `start_chat_background_run` 的 `workspace_id`、`project_id` 改为 `""`。
- [ ] 16.3 `scheduler/service.py` 与 Job 工具去 Project
  - `scheduler/service.py`：
    - 删除 `_require_project`、`_live_project_resources`，以及 `_assert_resources_available` 中的 Project 分支；
    - `build_required_skill_manifest`、`validate_required_skill_manifest`、`frozen_skills_require_bash` 的调用处删除 `project_root=` 实参；
    - 新建 Job 的 `workspace_id`、`project_id` 写 `""`。
  - `tools/scheduled_job_tools.py`：
    - `create_job(workspace_id="", project_id="", ...)`；
    - `resolve_job_skill_refs` 删除 `project_root=_project_root(...)` 和 `workspace_id=` 实参；
    - 删除 `_project_root` 函数。
- [ ] 16.4 Job 验收
  - 完成判定：
    - 去掉 `test_fresh_job_confirm_enable_fire_once` 的标记后通过；
    - `test_scheduled_chat_bridge.py`、`test_scheduled_job_resource_pause.py`、`test_scheduled_unattended.py`、`test_scheduled_jobs.py`、`test_scheduled_preloaded_skill_runtime.py` 全部通过，其中依赖 Project 的用例改为 owner Session grant 构造；
    - `rg -n "get_project|_require_project|project_archived" backend/src/scheduler backend/src/server/routes/chat/run_context.py` 无结果。

## 17. 群聊工作室去 Project

- [ ] 17.1 `group_studio/models.py` 收口
  - 请求模型中的 `projectId` / `workspaceId` 改为可选、默认 `""`，不参与任何逻辑，用于兼容旧 payload。
  - 响应模型删除这两个字段；`GroupStudio.workspace_id` 删除。数据库列保留，只写 `""`。
  - 前端 `useGroupStudioRoom.ts` 读取 `studio.projectId` 的位置由 19.2 删除；17.1 到 19.2 之间前端会传 `undefined` 或空串，后端按本项忽略，不得为此恢复响应字段。
- [ ] 17.2 存储层与服务层按 Session 归属
  - `storage/group_studio_records.py`：
    - `list_active_by_project(project_id)` 改为 `list_active(chat_session_id: str = "")`：为空时列出 tenant 全部，否则按 `s.chat_session_id = ?` 过滤；
    - `find_blank_draft_id(project_id)` 改为 `find_blank_draft_id(chat_session_id)`，WHERE 条件同步修改。
  - `group_studio/service.py`：
    - `list_studios(session_id: str = "")`；
    - `delete_studio(group_id)`、`pause_studio(group_id)` 删除 `project_id` 形参，归属只用 `current_request_session()` 与房间的 `chat_session_id` 比较；
    - 删除 `_assert_project` 和 `CROSS_PROJECT_SESSION`；
    - 第 251 行的 `resolve_runtime_context` 改为 `build_session_context(chat_session_id, agent_id=request.agent_id)`；
    - 第 290–291 行响应中的 `workspaceId` / `projectId` 删除；
    - `_maybe_seat_worker(agent_id)` 删除 `workspace_id` 形参。
- [ ] 17.3 路由层改用 Session 参数
  - `server/routes/group_studio.py`：
    - 列表接口查询参数由 `projectId` 改为可选的 `sessionId`：有值时按它过滤；为空时取 `current_request_session()`；两者都为空时列出当前 tenant 全部房间；
    - 删除接口不再要求 `projectId`；
    - 暂停和模型切换接口忽略请求体中的 `projectId`；
    - 所有 `SessionError` 改为 `session_error_http(..., default_status=409)`。
- [ ] 17.4 `ensure_chat_session` 去旧字段并验收
  - `ensure_chat_session` 删除 `workspace_id`、`project_id` 形参，插入时两列写 `""`。
  - 完成判定：
    - 去掉 `test_fresh_group_studio_create_and_one_worker_run` 的标记后通过；
    - `test_group_studio_v2.py`、`test_group_studio_session.py`、`test_group_studio_pr1.py`、`test_group_studio_archive_memory.py` 全部通过；
    - `rg -n "projectId|project_id|workspaceId|workspace_id" backend/src/group_studio` 只剩数据库列读写处，并在证据中列出行号。

## 18. 迁移执行入口

- [ ] 18.1 新增 `src/session_migration/runner.py`（design §16.6 九步）
  - 證據：`test_session_migration_runner.py` 七例通過；`backend/src/session_migration/runner.py:121`
  - 13.3 已建 `src/session_migration/legacy_fields.py`，本项不重复创建。
  - 测试：新建 `test/test_session_migration_runner.py`，覆盖以下七种情况：
    - 全新 tenant 返回 `fresh_cutover`；
    - 有 legacy 数据时返回 `cutover`，且 Session 工作目录和 grant 数量与 inventory 一致；
    - 有冲突时返回 `blocked`，且 `cutover_state()["cutover"] == "armed"`；
    - 两个 Workspace 的 `profiles/<同一 id>.md` 正文相同时合并进 `global_agents_root()`，正文不同时返回 `blocked` 且全局目录未写入；
    - 五项检查任一不满足时返回 `not_ready`，且 `cutover_state()["cutover"] == "armed"`；
    - dry-run 不写任何文件；
    - 重复执行结果相同。
- [ ] 18.2 接入启动门、hosted 新 tenant 和未迁移 Session 准入
  - 先改测试夹具与受影响用例（design §16.5）：
    - `test/conftest.py::local_tenant`：在 `with use_tenant(ctx):` 块内、`yield ctx` 之前调用 `set_migration_flag(True, schema_version_value=1)`（该函数自行 `ensure_db()`）。hosted 夹具不改，hosted tenant 由下文 `ensure_tenant` 中的启动门切换。
    - `test_global_session_foundation.py::test_default_migration_state_does_not_start` 改名为 `test_status_api_does_not_start_migration`：开头调用 `set_migration_flag(False, schema_version_value=0)`；`with TestClient(app) as client:` 改为 `client = TestClient(app)`（不触发 lifespan 中的启动门）；其余断言不变。
    - 其他需要 legacy 阶段的用例同样先调用 `set_migration_flag(False, schema_version_value=0)`，并直接调用被测函数，不经过 lifespan。
  - `src/sessions/run_binding.py::begin_run`：接入第 2 步 `require_migrated_session(session_id)`。
  - `server/main.py` lifespan：对每个 tenant，在 `migrate_tenant_skills()` 之后、`bootstrap_tenant_scheduler()` 之前调用 `ensure_tenant_cutover()`。
  - `tenancy/lifecycle.py::ensure_tenant`：在 `use_tenant(ctx)` 块内、`migrate_tenant_skills()` 之后调用 `ensure_tenant_cutover()`。
  - `GET /api/migrations/global-session-v1` 的响应增加 `cutoverState`。
  - 完成判定：去掉 `test_fresh_tenant_startup_gate_cuts_over` 和 `test_unmigrated_session_rejected_before_cutover` 的标记后通过；`test_tenant_isolation.py`、`test_tenant_background.py` 以及所有 `test_tenant_lifecycle*.py` 通过。
- [ ] 18.3 新增 CLI `src/session_migration/__main__.py`（design §16.6）
  - 测试：直接调用 `main(argv)`，覆盖 `status`、`dry-run`、`run`、`rollback` 四个子命令，以及退出码 0（成功）、1（异常）、2（`blocked` / `not_ready`）。
  - 完成后才能执行 12.7。

## 19. 前端与 Electron

- [ ] 19.1 新建对话先建 Session
  - 新增 `frontend/src/features/sessions/api.ts::createSession({ agentId, title, workingDirectory })`，调用 `POST /api/sessions`。
  - `pages/ChatPage.tsx`：发送逻辑中的 `const sessionId = activeSessionId || createId()` 改为：
    - `activeSessionId` 为空时，先 `createSession`，再用返回的 `id` 调用 `setActiveSessionId`；
    - 之后再上传附件、发送消息；
    - 创建失败时不上传、不发送，并显示错误。
  - `pages/SandboxPage.tsx:119` 的 `createId('sandbox')` 同样改为先建 Session。
  - 新建对话时允许选择工作目录（仅桌面端）：在输入区增加“工作目录”按钮，调用与 `SessionScopePanel.pickDesktop` 相同的 `window.electronAPI.selectProjectDirectory()`，所选路径作为 `createSession` 的 `workingDirectory` 传入。hosted 端仍在 Session 创建后通过 `SessionScopePanel` 设置。
  - 测试：vitest 覆盖三种情况：没有 `activeSessionId` 时先调 `POST /api/sessions` 再调 `/api/chat/send/start`；附件在 Session 创建之后上传；创建失败时不发送。
- [ ] 19.2 停止发送旧字段
  - 按 `rg -n "projectId|workspaceId|project_id|workspace_id" frontend/src` 的结果逐处删除，至少包括：
    - `hooks/useEventStream.ts:231`、`hooks/eventStreamTypes.ts:149`；
    - `hooks/useMemory.ts` 第 78–110、144、155、209、241、254 行；
    - `hooks/useAgentBuilder.ts:56–57`、`hooks/useAgents.ts:47`、`stores/agentsStore.ts:64`；
    - `features/runtime-stream/TeamBlockViews.tsx:478–483`（删除 `'default'` 默认值和查询参数）；
    - `features/group-studio/api.ts` 的 `projectQuery` 及所有 `projectId` 形参和请求体字段，调用方同步修改；
    - `pages/SubagentsPage.tsx:134` 的 `projectId` prop，以及 `SubagentTaskDetail` 中对应的 prop。
  - 只读类型字段（`types/`、`features/traces/traceTypes.ts`、`hooks/useScheduledJobs.ts` 的响应映射）在 20.2 后端停止返回后再删除。
  - 测试：新增 `frontend/src/features/sessions/legacyScopeRequests.test.ts`，扫描 `src/**/*.{ts,tsx}`（排除 `*.test.*`），出现上述四个标识符即失败。临时豁免清单只含 `types/`、`features/traces/traceTypes.ts`、`hooks/useScheduledJobs.ts`，由 20.5 清空。
- [ ] 19.3 Electron 可信目录改读 Session
  - 后端在 `server/routes/sessions.py` 新增 `GET /api/sessions/directory-roots`：返回 `{"roots": [...]}`，内容为当前 tenant 全部未归档 Session 的工作目录与 allowed roots，经 realpath 后去重、排序。该路由必须写在第 59 行 `@router.get("/sessions/{session_id}")` 之前，否则会被当作 `session_id="directory-roots"` 匹配。
  - `electron/main.ts`：
    - `refreshTrustedProjectRootsFromBackend` 改为调用该接口，并改名为 `refreshTrustedSessionRootsFromBackend`；
    - 删除对 `/api/workspaces` 的调用；
    - `trustedProjectRoots` 改名为 `trustedSessionRoots`，`rememberProjectRoot` 改名为 `rememberSessionRoot`。
  - 测试：后端路由测试覆盖归档 Session 不返回、跨 tenant 不返回。
- [ ] 19.4 前端与 Electron 验收
  - 證據：`npm test` 109 通過、`npm run lint` 0 error、`npm run build` 通過、`npm run electron:compile` 退出碼 0。原生預覽模組因 Electron headers 403 回退，不影響 TypeScript 編譯。
  - 完成判定：
    - `cd frontend && npm run test && npm run lint && npm run build` 通过；
    - `cd frontend && npm run electron:compile` 通过；
    - `rg -n "/api/workspaces" frontend/src electron` 无结果。

## 20. 结构清理与最终验收

- [ ] 20.1 删除测试中的全局 Project 预置
  - 删除以下全局预置：
    - `test/conftest.py::local_tenant` 中对 `_seed_historical_project` 的调用，以及该函数本身；
    - `test/hosted_support.py::seed_legacy_project`，原调用方改用 `legacy_project` 或不再构造 Project；
    - 清空 `CONCAT_PENDING_REMOVAL`。
  - 参考：第二轮复核在沙箱关闭预置后，共 80 个失败和 27 个 setup error，分布如下：

    | 测试文件 | 失败数 |
    | --- | --- |
    | `test_group_studio_v2.py` | 18 |
    | `test_file_skill_integration.py` | 11 |
    | `test_chat_authorization.py` | 8 |
    | `test_scheduled_job_resource_pause.py` | 7 |
    | `test_scheduled_chat_bridge.py` | 7 |
    | `test_scheduled_unattended.py` | 4 |
    | `test_group_studio_session.py` | 4 |
    | `test_group_studio_pr1.py` | 3 |
    | `test_group_studio_archive_memory.py` | 3 |
    | `test_workspace_project.py` | 2 |
    | `test_global_session_foundation.py` | 2 |
    | `test_chat_skill_injection_scope.py` | 2 |
    | `test_chat_runtime_regressions.py` | 2 |
    | `test_chat_forced_skills.py` | 2 |
    | `test_timeline_habits.py`、`test_scheduled_jobs.py`、`test_runtime_paths.py`、`test_permission_migration.py`、`test_file_skill_migration.py` | 各 1 |

    主要报错为 `Workspace 不存在: default`、`cross_project_session`、`Project 不存在: default`。第 14–17 节完成后，其中一部分会自然消失。
  - 修复规则：
    - (a) 测迁移、回滚或旧数据隔离的用例，改用 `legacy_project`；
    - (b) 其他用例删除 Workspace / Project 构造，改用 Session 与 Session grant；
    - (c) 若用例失败是因为生产代码仍要求 Project，说明第 14–17 节有遗漏，回到对应任务修复，不得在测试里补建 Project。
- [ ] 20.2 按 design §16.8 移除 `RuntimeContext`
  - 删除模块：`src/runtime_context.py`、`src/project_resources.py`；`src/project_files.py` 迁到 `src/sessions/files.py`，并删除 `project_root` 形参。
  - 删除字段：`ChatSession`、`RunAuthorization`、Trace、初始事件和群聊响应中的 `workspaceId` / `projectId`。
  - `server/routes/chat/run_context.py:698–710`：删除 Project 提示词段（`Workspace ID`、`Project ID`、`Project root`）。
  - `chat/handlers/sessions.py:138`：`projectId` 默认值改为 `""`（不过滤）。
  - `chat/handlers/runs.py`、`chat/handlers/sessions.py`、`notifications.py`、`mcp.py`、`agents.py` 中其余 `projectId` / `workspaceId` 查询参数保留只读兼容，但不得参与过滤或授权。新增契约测试：传入任意值的结果与不传时相同。
  - 完成判定：`LEGACY_IMPORT_ALLOWLIST` 为空集。
- [ ] 20.3 删除 Skill catalog 与 factory 的兼容签名
  - `skills/service.py::catalog_for` 只保留 `refresh` 参数。
  - 以下函数删除 `workspace_id`、`project_root` 形参：`skills/registry.py` 中的 `discover_external_skills`、`get_runtime_skill_catalog`、`get_installed_skill`、`resolve_project_skill`，以及 `scheduler/skill_manifest.py` 中的各函数。
  - `skills/catalog.py::current_skill_context` 删除 `project_root`、`project_id` 形参，以及对 `list_projects` 的读取；`SkillContext` 删除 project 字段。
  - `agents/factory.py::get_or_create_agent` 删除 `project_id` 形参。
  - 完成判定：`rg -n "catalog_for\(.*(workspace_id|project_id|project_root)" backend/src` 无结果。
- [ ] 20.4 删除 Memory 调用的旧实参
  - `memory/retrieval.py` 第 108、128–129、187–188 行，以及 `team_runtime/service.py:74–75`，删除 `workspace_id`、`project_id` 实参，改用 Memory 模块默认值。
  - 完成判定：`test_timeline_habits.py` 和 Memory 相关测试通过。
- [ ] 20.5 护栏收尾
  - `reference_allowlist.json` 删除所有已不再引用旧字段的路径，只允许保留：`src/session_migration/**`、`src/storage/**`、`src/sessions/legacy_gate.py`、`test/legacy_fixtures.py`、`test/test_legacy_scope_isolation.py`，以及迁移测试。
  - `session_migration/matrix.py::verify_matrix`：固定计数 111 / 24 改为“不得超过 `usage_matrix.json` 记录数”的棘轮，并同步删减 `usage_matrix.json`。
  - 前端 `legacyScopeRequests.test.ts` 的豁免清单清空。
- [ ] 20.6 重新验收前面被打回的任务
  - 对 4.1、4.3、5.3–5.6、6.1–6.3、6.7、8.3、9.1、9.3、10.2、11.5、12.1、12.3 逐项补写“证据：<测试名>；<file:line>”后勾选。
  - `test_fresh_tenant_acceptance.py` 与 `test_legacy_scope_isolation.py` 中不得残留任何 xfail 标记。
  - 完成后继续 12.6–12.9。
