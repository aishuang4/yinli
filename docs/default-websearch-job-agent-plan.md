# 系统默认 WebSearch Job Agent 实施计划

> 状态：已实施。
> 日期：2026-08-26。
> 关联设计：`docs/scheduled-jobs-v2-agent-activation.md`。
> 本文记录系统默认 Job Agent 的实施方案与验收基线；当前事实以 `AGENTS.md` 和 `docs/architecture-overview.md` 为准。

## 1. 背景

当前用户通过 Chat 提出定时任务需求时，系统必须选择一个已经配置完成的
`role=job` Agent 作为到期执行者。若系统中没有 Job Agent，Chat 只能提示用户先到
Agents 页面创建并配置，无法直接完成定时任务登记。

本次计划增加一个开箱即用的系统 Job Agent，使需要读取公开网页或调用 HTTP API
的定时任务具备默认执行者。

该 Agent 不是普通的初始化样例数据，而是与系统 `default` Chat Agent 类似的稳定
系统资源，需要同时满足：

- 全新数据库首次启动时自动创建。
- 历史数据库升级后自动补齐。
- 多次启动保持幂等，不重复创建。
- 在所有 Workspace / Project 中可被定时任务选用。
- 不允许删除或改成 Chat Agent。
- 不绕过 MCP、HTTP Tool、Skill 等外部资源的 Project binding。

## 2. 能力边界

项目当前没有真正的 `WebSearch` 工具：

- `WebFetch` 只能读取用户或任务已经知道的公开 HTTP(S) URL。
- `HttpRequest` 用于调用已知 HTTP API。
- 两者都不提供搜索引擎查询、结果发现或通用 WebSearch 能力。

因此本次按产品要求保留展示名称 `WebSearch` 和描述“能够联网搜索的agent”，但实现
与测试不得把它表述为已经具备开放式互联网搜索能力。若后续需要真正的搜索能力，
应单独引入经过安全审查的 WebSearch 工具，再升级该系统 Agent 配置。

工具名称必须使用代码中的准确标识 `WebFetch`，不能写成 `webFetch`。

## 3. 目标配置

系统 Agent 使用稳定 ID，不用显示名称判断身份：

```text
id: system-websearch-job
name: WebSearch
description: 能够联网搜索的agent
workspaceId: default
role: job
systemPrompt: 请根据用户需要完成定时任务

modelConfig.primary: deepseek-v4-flash
modelConfig.fallback: []
modelConfig.temperature: 0.7
modelConfig.topP: 0.8
modelConfig.maxTokens: 4096
modelConfig.maxReactIters: 40

jobToolkit.required:
  - kind: builtin
    id: WebFetch
  - kind: builtin
    id: HttpRequest

jobToolkit.optional: []
mcpTools: []
knowledgeTools: []
skills: []

guardrails.networkEnabled: true
guardrails.humanApproval: false
guardrails.toolPermissionProfile: trusted_tools
guardrails.toolPermissionMode: bypass

tags:
  - system
  - scheduled-job
  - websearch
  - <内置配置版本标签>
```

`fallback` 计划设为空，避免用户明确指定的 `deepseek-v4-flash` 被运行时默认回退链
隐式改写。若产品希望沿用全局 fallback，应在实现前明确调整该项。

## 4. 设计原则

### 4.1 稳定 ID 是系统身份

名称、描述和 tags 都可能被编辑，不能作为权限判断依据。后端统一定义：

```python
DEFAULT_CHAT_AGENT_ID = "default"
DEFAULT_WEBSEARCH_JOB_AGENT_ID = "system-websearch-job"
SYSTEM_AGENT_IDS = frozenset({
    DEFAULT_CHAT_AGENT_ID,
    DEFAULT_WEBSEARCH_JOB_AGENT_ID,
})
```

可见性、删除保护、角色保护和跨 Workspace 访问均通过
`is_system_agent_id(agent_id)` 判断，逐步替换散落的 `agent_id == "default"` 特例。

### 4.2 只豁免系统 Agent 本身

`system-websearch-job` 可以不依赖普通 Agent Project binding，但它未来绑定的 Skill、
MCP、HTTP Tool 和 Knowledge Tool 仍必须满足以下交集：

```text
Agent 当前绑定
∩ Project resource binding
∩ 资源仍安装并启用
∩ 无人值守权限允许
∩ Job capability contract 已认领
```

不得因为它是系统 Agent 而放开 Project 的全部外部资源。

### 4.3 初始化不覆盖用户配置

首次缺少稳定 ID 时写入完整默认配置。记录已存在时：

- 不重置用户修改过的名称、描述、模型、提示词和工具分层。
- 通过内置配置版本标签执行必要的单次升级。
- 升级只修复系统不变量，不反复覆盖用户配置。
- API 层持续保证角色、网络和无人值守权限不被破坏。

### 4.4 与当前定时任务架构保持一致

本次不新增 Runtime，不新增 Chat SSE 事件，也不绕过现有能力门禁。运行链路保持：

```text
Chat Agent
  -> CheckJobFeasibility(system-websearch-job)
  -> ScheduleJob
  -> pending_confirmation
  -> ConfirmJob
  -> ticker
  -> react + single_agent scheduled Chat Run
```

## 5. 实施步骤

### 5.1 抽取系统 Agent 常量与判断函数

修改 `backend/src/server/routes/agents.py`，或将通用常量下沉到无路由依赖的轻量模块：

- 定义两个系统 Agent 稳定 ID。
- 提供 `is_system_agent_id()`。
- 将只针对 `default` 的系统身份判断改为统一 helper。
- 保留 `default` 只能是 Chat Agent 的特殊约束。
- 增加 `system-websearch-job` 只能是 Job Agent 的特殊约束。

如果 helper 需要被 `project_resources.py`、scheduler 和 Chat Runtime 共用，优先放在
独立底层模块，避免 `scheduler -> routes` 循环依赖继续扩大。

### 5.2 构造系统 WebSearch Job Agent

参照 `_build_default_agent()` 新增构造函数，例如：

```python
def _build_default_websearch_job_agent() -> AgentResponse:
    """构建系统内置的联网定时任务 Agent。"""
```

构造结果必须复用 `JobToolkit`、`Guardrails` 和 Agent Pydantic 模型校验，不直接写
未经验证的松散 JSON。保存前复用 `validate_job_agent_config()`，确保必注入工具、网络
权限和 trusted tools 策略满足 Job Agent 合同。

### 5.3 统一系统 Agent 初始化

将 `_ensure_default_agent()` 扩展或封装为 `_ensure_system_agents()`：

1. 加载 SQLite 中已有 Agents。
2. 确保 `default` Chat Agent 存在并执行已有 prompt 升级。
3. 确保 `system-websearch-job` 存在。
4. 对已存在记录执行幂等版本检查。
5. 新增或升级后调用 `upsert_agent()`。
6. 发生有效配置变化时调用 `invalidate_agent()`。

不需要新增 SQLite 表或字段，继续使用现有 `agents` 表。不能用名称查询或删除同名
用户 Agent。

### 5.4 系统资源保护

修改 Agent 更新和删除接口：

- `DELETE /api/agents/system-websearch-job` 返回 403。
- 更新时强制保留 `role=job`。
- 强制保留 `networkEnabled=true`。
- 强制保留 `humanApproval=false`。
- 强制使用 `trusted_tools / bypass`。
- `jobToolkit.required` 仍必须至少一项有效工具。

推荐允许用户修改名称、描述、模型、系统提示词以及工具分层，但不能破坏上述运行
不变量。若产品要求完全不可编辑，应在实现前改为前后端同时只读，本计划默认采用
“可配置、不可删除”的现有系统 Agent 习惯。

### 5.5 跨 Workspace / Project 可见和可运行

修改以下位置，把“仅 default Agent 跨 Workspace”的逻辑扩展为“系统 Agent”：

- `backend/src/server/routes/agents.py`
  - 所有 Workspace 的 Agent 列表都返回系统 Agent。
- `backend/src/project_resources.py`
  - `require_agent()` 对系统 Agent 本身免普通 Project binding。
- `backend/src/server/routes/chat/run_context.py`
  - scheduled run context 允许系统 Job Agent。
- `backend/src/scheduler/chat_bridge.py`
  - Scheduled Chat Run 启动时正确解析系统 Agent。
- `backend/src/scheduler/service.py`
  - confirm / resume 复检时认可系统 Agent，但继续检查 Project、Skill、MCP 和 HTTP
    资源状态。
- `backend/src/scheduler/capability_catalog.py`
  - 能力库存能读取系统 Job Agent，同时继续按 Project binding 过滤外部资源。

### 5.6 接入 Chat 登记目录

`backend/src/tools/scheduled_job_tools.py` 当前会把已配置 Job Agent 写入主 Agent prompt。
需要确保系统 Agent 在任意 Workspace / Project 下都出现在可用目录中，例如：

```text
Available Job Agents: WebSearch (system-websearch-job)
```

同时检查目录构建不会把其他 Workspace 的普通 Job Agent 暴露给当前会话。若现有代码
没有按 Workspace 过滤，应在本次改造中一并按运行上下文收紧，而不是依赖模型避免
选择越权 Agent。

### 5.7 前端展示与保护

修改：

- `frontend/src/types/agent.ts`
- `frontend/src/hooks/useAgents.ts`
- `frontend/src/features/agents/AgentLandingPage.tsx`
- `frontend/src/features/agents/AgentEditor.tsx`

建议由后端响应提供派生字段 `systemManaged`，前端不要用名称或可编辑 tags 判断系统
身份。界面行为：

- 在“定时任务 Agent”区域展示 WebSearch。
- 显示“系统内置”标识。
- 不显示删除按钮。
- 保留配置入口。
- 编辑器锁定用途和无人值守权限不变量。
- Chat Agent 选择器继续按 `role !== 'job'` 排除该 Agent。

`systemManaged` 可以作为 API 派生字段，不需要写入 SQLite。

### 5.8 文档同步

实现完成后更新：

- `AGENTS.md`：补充系统默认 WebSearch Job Agent 的当前事实和能力边界。
- `docs/architecture-overview.md`：补充系统 Job Agent 初始化与跨 Workspace 行为。
- `docs/project-progress-handoff.md`：记录完成状态和验证结果。
- `docs/project-todo.md`：关闭对应中文待办。
- `docs/scheduled-jobs-v2-agent-activation.md`：补充默认执行者，不改写能力合同原则。

本次不新增或修改 Chat SSE 事件，因此无需修改
`docs/chat-sse-event-contract.md`。

## 6. 测试计划

### 6.1 系统 Agent 初始化

- 全新数据库启动后同时存在 `default` 和 `system-websearch-job`。
- 历史数据库启动后自动补齐 `system-websearch-job`。
- 连续执行初始化不会重复创建。
- 同名用户 Agent 不影响系统 Agent 创建。
- 已编辑的系统 Agent 配置不会在普通重启时被覆盖。
- 配置版本升级只执行一次。

### 6.2 配置与权限

- 模型主项严格为 `deepseek-v4-flash`。
- 系统提示词与需求一致。
- 必注入工具严格包含 `WebFetch` 和 `HttpRequest`。
- `role=job`、网络开启、trusted tools、无人工审批。
- 不能删除。
- 不能改为 Chat Agent。
- 不能降级为需要人工审批或禁止网络的配置。

### 6.3 Workspace / Project 隔离

- 在非 default Workspace 的 Agent 页面可见。
- 无普通 Agent binding 时仍可作为 Scheduled Job 执行者。
- 其他 Workspace 的普通 Job Agent 不会进入当前 Chat 的可用目录。
- 系统 Agent 新绑定的 Skill、MCP 和 HTTP Tool 仍受 Project binding 限制。
- 未绑定外部资源不会被 firing toolkit 注入。

### 6.4 可行性与 firing

- 需要 `web.fetch` 的任务通过必注入层检查。
- 显式需要 `http.call` 的任务通过必注入层检查。
- firing toolkit 始终包含 `WebFetch`、`HttpRequest` 和 `FinalAnswerStart`。
- firing 不包含 `AskUser`、Plan、DelegateTask 和定时任务登记工具。
- 无法把 `HttpRequest` 错认成邮件发送能力。
- 不新增任何真正 WebSearch 能力的虚假测试。

### 6.5 前端

- WebSearch 出现在“定时任务 Agent”区域。
- 显示系统内置标识且没有删除按钮。
- Chat Agent 下拉框中不可见。
- 编辑后刷新仍保持配置。
- 非 default Workspace 中展示一致。

## 7. 验证命令

先执行定向测试：

```bash
cd backend
uv run pytest test/test_default_agent.py
uv run pytest test/test_job_feasibility.py
uv run pytest test/test_scheduled_unattended.py
uv run pytest test/test_scheduled_job_tools.py
```

再执行完整验证：

```bash
cd backend
uv run pytest

cd ../frontend
npm run build
npm run lint
```

关键交互需人工验证：

1. 使用已有历史数据库启动应用，确认系统 Agent 自动出现。
2. 切换不同 Workspace / Project，确认均可选择该 Job Agent 登记任务。
3. 在 Chat 输入需要定时读取公开网页的请求。
4. 确认调用顺序为 `CheckJobFeasibility -> ScheduleJob -> ConfirmJob`。
5. 手动触发或等待到期，确认 firing 使用 WebFetch / HttpRequest 且无需 HITL。
6. 查看 Jobs 页面执行结果、产物和应用内通知。

## 8. 验收标准

满足以下条件才视为完成：

- 新旧数据库都自动存在且只存在一个稳定 ID 的 WebSearch 系统 Job Agent。
- 默认配置与第 3 节完全一致。
- 用户无需先创建 Job Agent 即可登记适用的定时任务。
- Agent 在所有 Workspace / Project 可用，且不破坏外部资源白名单。
- 系统 Agent 不可删除、不可切换为 Chat 角色。
- 到期执行继续使用能力合同和无人值守安全边界。
- 前后端定向测试、后端全量测试、前端 build 和 lint 全部通过。
- 文档不把 WebFetch / HttpRequest 宣称成真正的开放式 WebSearch。

## 9. 回滚方案

若上线后需要回滚：

1. 停止自动补齐该系统 Agent。
2. 保留已经由它创建的 Scheduled Job 和 firing 历史，不删除历史记录。
3. 将仍引用该 Agent 的 active / pending Job 暂停，原因写为 `needs_job_update`。
4. 恢复系统 Agent 特例前，先确认没有运行中的 firing。
5. 是否删除 `system-websearch-job` 持久记录由单独迁移决定，不在代码回滚时静默删除。

回滚不得恢复旧独立 Plan Runtime、Subagent Runtime，也不得放宽 Project resource
binding 或无人值守权限。
