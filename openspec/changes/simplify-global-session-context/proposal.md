# Proposal

## Why

当前系统同时把 Workspace、Project、Project root、AgentScope Workspace 和 Project 内 `.agent-workstation` 目录作为上下文边界，导致同一资源在全局目录、Workspace 策略、Project binding、Agent 配置和 Runtime 之间重复判定。产品实际以对话持续完成任务，继续维护 Workspace → Project → Session 三层归属只会扩大数据迁移、权限和“可见但不可用”的不一致，应收敛为用户全局根与 Session 根两层。

## What Changes

- **BREAKING**：删除用户可见 Workspace / Project 概念与选择器；桌面端和 hosted 用户域均不再要求创建默认 Workspace / Project，也不再用 `workspaceId` / `projectId` 作为新请求的运行归属。
- 引入唯一 `SessionContext`：每个 Chat session 是消息、Context、上传、Artifact、Run、Team、Subagent、群聊、Job 和授权工作目录的业务聚合根；Run 只能属于一个 Session。
- 将 `<tenant-root>`（桌面端为 `~/.agent-workstation`）定义为用户全局根，统一保存 Agent、Skill、MCP、HTTP Tool、模型、用户/Agent 长期记忆、系统设置和索引；Project / Workspace / HOME 兼容目录不再产生能力定义。
- 将内部运行文件统一迁移到 `<tenant-root>/sessions/<session-id>/`，Project 外部目录只作为 Session 明确授权的 `workingDirectory` / mounted roots，不写入系统目录、能力定义或隐藏状态。
- **BREAKING**：删除 Project resource binding。Session 创建或首次运行时保存显式资源选择和权限快照；全局启停决定资源是否存在，Session 授权决定本次对话能否使用，Agent 配置继续限制能力上限。
- 将 scheduled Job 绑定到一个持久 owner Session；Job 定义、确认快照和 firing 结果归该 Session，每次 firing 是该 Session 下的独立 Run，`fresh` 只控制跨 firing Context 继承。
- **BREAKING**：scheduled firing 改为 `preloaded-only` Skill Runtime：确认时显式选择的 `skill_ids` 由服务端冻结版本，每次 firing 在首个模型调用前注入对应 `SKILL.md`，模型可见工具列表不再包含 `Skill` viewer；`references/` 与 `scripts/` 仍通过已有文件工具按需访问。
- 统一 Job Skill 工具契约：`ScheduleJob` 只接受显式 Skill 引用，`required_skills` 由确认服务生成；`ConfirmJob` 必须返回冻结版本和首轮预加载状态；普通 Chat 的“按需调用 Skill”描述不得进入 scheduled Runtime。
- 群聊房间继续使用独立领域 API / SSE，但所有房间、成员运行和文件都归其关联 Chat Session，不再读取 Project root 或 Project binding。
- 将 timeline、Trace、通知、Skill 使用记录、Plan、HITL、Team、Subagent 和 Artifact 查询统一按 `session_id` 归属；长期习惯、Agent 专业区和全局能力目录仍位于用户全局根。
- 提供可中断恢复的迁移：把现有 Session 的 Project root 固化为该 Session 的授权工作目录，把 Project/Workspace 资源配置转换为 Session 快照，把 Job/群聊/运行文件迁到 Session 根；保留旧表和旧目录为只读回滚证据。迁移由每次启动时的启动门自动执行，也可用 CLI 手动执行；Runtime 不读取旧数据，tenant 切换完成前，未迁移的历史 Session 拒绝新 Run。
- 移除 `default Workspace`、`default Project`、`default_project_root()` 和无上下文时的隐式回退。没有 Session 的交互式运行必须先创建 Session；系统维护操作使用显式 global context。删除 default bootstrap 必须排在 Chat、群聊和 Job 的 Runtime 切换验收之后，避免全新 tenant 在过渡期无法使用。

## Capabilities

### New Capabilities

- `global-catalog`: 定义用户全局根中的 Agent、Skill、MCP、HTTP Tool、模型、长期记忆和设置的唯一权威目录及租户隔离规则。
- `session-workspace`: 定义 Session 聚合根、Session 文件布局、外部工作目录授权、资源快照、Run/Artifact 归属、删除归档和恢复规则。

### Modified Capabilities

- `skills`: 移除 Workspace 策略和 Project binding，改为全局安装/启停与 Session 显式授权，保持普通 Chat 按需加载和 Job 冻结规则。
- `scheduled-job-skill-context`: Job 改为归属 owner Session，firing 使用 Session 授权快照和 Session 根，不再校验 Project binding 或写 Project Job 目录。
- `group-studio`: 群聊状态、文件与 Worker Runtime 只继承关联 Session，不再依赖 Workspace / Project 上下文。

## Impact

- 后端：`runtime_context.py`、`project_resources.py`、`project_files.py`、`storage/*`、Chat handlers/runtime、Agent factory、Skill/MCP/HTTP catalog、scheduler、group studio、memory、notifications、Trace、Team/Subagent、Sandbox 和 hosted tenancy；工具组装、Skill viewer 描述、Job 工具 schema 和 scheduled firing 的 cache key 需要按 Runtime 模式隔离。
- 前端：移除 Workspace / Project store、hooks、TitleBar/设置选择器和创建弹窗；Chat 历史、Jobs、Trace、Memory、Skills、Agents、MCP、Tools、群聊和文件预览改用 active Session 或 global catalog。
- API：逐步移除请求和响应中的 `workspaceId` / `projectId`；新增 Session 创建时的 `workingDirectory`、allowed roots、resource snapshot 与迁移状态。过渡期旧参数只用于定位旧记录，不能改变新 Session 归属。
- 数据：涉及 `workspaces`、`projects`、`project_resource_bindings` 及所有带 Workspace/Project 列的表；必须采用扩展 schema、回填、校验、切读、停止旧写、最后清理的分阶段迁移。
- 文件：停止向外部 Project 写 `.agent-workstation/sessions`、`jobs`、`group-studios` 和 `.agents` 定义；迁移后内部状态只在 `<tenant-root>`，外部目录只保存用户主动创建或导出的文件。
- 测试与验收：测试夹具不得为所有用例预置 default Workspace / Project；每项 Runtime 切换都要以生产调用点和全新 tenant（无任何 Workspace / Project 记录）的端到端用例验收，单元测试通过不代表切换完成。
- Hosted：账号 tenant 目录继续作为安全隔离边界；control plane、登录、额度和管理员能力不因删除用户可见 Workspace 而合并租户。
