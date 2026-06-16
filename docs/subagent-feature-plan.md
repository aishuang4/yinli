# Subagent 功能规划

> 更新时间：2026-06-16
>
> 本文档用于规划 Agent Workstation 的 Subagent 功能。当前代码中已经预留 `callableAgents` 和 `maxDepth` 字段，但运行时尚未把子 Agent 暴露为主 Agent 可调用能力。本文档定义功能边界、实现路径、数据结构、前后端改造点和测试验收标准。

## 1. 背景

当前系统已经支持三类 Agent 协作能力：

- 单 Agent：用户直接和一个 Agent 对话。
- Team：Leader + Worker 的显式团队协作，前端会展示每个 worker 的运行状态。
- Agent Builder：根据用户需求生成 Agent / Team 草案。

但还缺少“一个 Agent 在自己的 ReAct 循环里按需调用另一个 Agent”的能力。这个能力不应该替代 Team，而是补齐单 Agent 的内部委派能力。

典型场景：

- 一个“产品经理 Agent”遇到技术细节时，临时调用“架构 Agent”给建议。
- 一个“代码审查 Agent”遇到不确定事实时，调用“知识检索 Agent”补充上下文。
- 一个“写作 Agent”调用“数据分析 Agent”生成一段结构化分析，再由自己组织最终答案。

## 2. 功能定位

Subagent 是“主 Agent 可调用的子 Agent 工具”。

主 Agent 仍然是用户选择的唯一对话入口。子 Agent 不直接接管会话，而是作为主 Agent 的工具能力之一，由主 Agent 在需要时调用，并把子 Agent 的输出作为中间结果继续推理。

### 和 Team 的区别

| 能力 | Subagent | Team |
| --- | --- | --- |
| 用户入口 | 一个主 Agent | 一个 Team |
| 调用方式 | 主 Agent 自主决定是否调用 | Leader 编排 worker |
| 前端展示 | 类似工具调用，显示调用了哪个子 Agent | 明确展示每个 worker 状态 |
| 适合场景 | 临时委派、专家咨询、轻量内部协作 | 多角色任务拆解、并行/依赖执行、人机协作 |
| 生命周期 | 一次工具调用内完成 | 一次 Team run 中多个 activity |
| 结果汇总 | 主 Agent 自己吸收子 Agent 输出 | Leader 汇总 worker 输出 |

### 和 Skills 的区别

| 能力 | Subagent | Skills |
| --- | --- | --- |
| 本质 | 调用另一个 Agent 运行 | 给当前 Agent 注入操作手册和资源 |
| 是否独立模型调用 | 是 | 否 |
| 是否有独立记忆 | 是，使用子 Agent 自己的记忆配置 | 否，随当前 Agent |
| 是否有独立工具权限 | 是，继承/限制后使用子 Agent 配置 | 否，使用当前 Agent 配置 |

### 和 MCP / HTTP 工具的区别

Subagent 是一个智能体工具，不是确定性工具。它会运行自己的模型、prompt、记忆、工具和安全策略，因此需要额外的深度限制、循环检测、Trace 展示和成本提示。

## 3. 目标

第一阶段目标：

- Agent 编辑页可以选择可调用的子 Agent。
- 主 Agent 运行时可以把这些子 Agent 暴露为工具。
- 子 Agent 被调用时执行自己的 prompt、模型、记忆、工具和权限配置。
- 子 Agent 调用过程写入 Trace。
- Chat 页面像工具调用一样展示“调用了哪个子 Agent、输入是什么、输出是什么”。
- 防止循环调用和无限嵌套。

第二阶段目标：

- 子 Agent 调用支持 HITL。
- 子 Agent 调用支持流式前端反馈。
- 子 Agent 调用支持调用预算、超时、token 统计、缓存命中统计。
- Agent Builder 可以自动推荐 subagent 关系。

## 4. 现有基础

当前已经存在：

- 后端 Agent 数据模型：
  - `callableAgents: list[str]`
  - `maxDepth: int`
- 前端 Agent 类型：
  - `callableAgents`
  - `maxDepth`
- Agent 编辑器已有“最大调用深度”字段。
- SQLite `agents.callable_agents` 和 `agents.max_depth` 已持久化。
- Chat 已有工具调用展示、Trace、HITL、记忆、模型 fallback、工具权限等基础设施。

缺失：

- Agent 编辑器缺少“选择子 Agent”的 UI。
- Runtime 没有把 `callableAgents` 转成工具注入 Toolkit。
- 没有子 Agent 调用 Trace 事件。
- 没有子 Agent 调用历史展示。
- 没有循环检测和调用上下文深度控制。

## 5. 核心设计

### 5.1 数据模型

短期不新增表，复用现有字段：

```json
{
  "callableAgents": ["agent-id-1", "agent-id-2"],
  "maxDepth": 2
}
```

后续如需精细策略，再引入扩展配置：

```json
{
  "subagentPolicy": {
    "mode": "auto",
    "maxCallsPerRun": 5,
    "inheritWorkspace": true,
    "inheritModel": false,
    "requireApproval": false,
    "outputLimit": 8000
  }
}
```

第一阶段建议暂不扩表，避免引入迁移负担。

### 5.2 Runtime 工具形态

为每个可调用子 Agent 创建一个工具：

```text
CallAgent_<agentName>
```

工具输入：

```json
{
  "task": "要交给子 Agent 的任务",
  "context": "必要上下文，可选",
  "expected_output": "期望输出格式，可选"
}
```

工具输出：

```json
{
  "agentId": "...",
  "agentName": "...",
  "status": "completed",
  "output": "...",
  "toolCalls": [],
  "skillCalls": [],
  "memorySummary": [],
  "traceId": "..."
}
```

### 5.3 子 Agent 执行策略

子 Agent 被调用时：

1. 根据子 Agent 配置创建运行时 Agent。
2. 注入子 Agent 自己的 system prompt。
3. 如果子 Agent 开启记忆，则注入子 Agent 自己的记忆。
4. 使用子 Agent 自己选择的 MCP、Skills、HTTP 知识库工具。
5. 使用子 Agent 自己的 guardrails，但 workspace context 可以继承主 Agent 当前工作区。
6. 运行一次子 Agent ReAct loop，拿到最终输出。
7. 把输出返回给主 Agent。

默认不让子 Agent 再调用主 Agent，防止环路。

### 5.4 深度和循环控制

每次主 Agent 调用子 Agent 时传递一个 runtime context：

```json
{
  "rootAgentId": "A",
  "currentDepth": 1,
  "maxDepth": 2,
  "callStack": ["A", "B"]
}
```

规则：

- `currentDepth >= maxDepth` 时，不再注入任何 subagent 工具。
- 如果目标子 Agent 已在 `callStack` 中，拒绝调用。
- 如果子 Agent 不在主 Agent 的 `callableAgents` 中，拒绝调用。
- 每次 run 限制最大子 Agent 调用次数，第一阶段可硬编码为 8。

### 5.5 权限继承

第一阶段采用保守策略：

- 子 Agent 使用自己的 tool permission profile。
- 如果主 Agent 是 `read_only`，子 Agent 也强制降级为 `read_only`。
- 如果主 Agent 禁用网络，子 Agent 也禁用网络。
- 如果主 Agent 使用 Docker workspace，子 Agent 默认使用同样 workspace scope。
- 子 Agent 不自动继承主 Agent 的 MCP / Skills，只使用自己配置的能力。

这样可以避免主 Agent 通过子 Agent 绕过安全边界。

### 5.6 记忆策略

子 Agent 调用时：

- 注入子 Agent 自己的主记忆和长期记忆。
- 子 Agent 调用结束后，可以触发自己的记忆候选提取。
- 主 Agent 不直接写子 Agent 记忆，只提供调用上下文。
- Trace 中记录子 Agent 是否注入记忆，但不把完整主记忆暴露到前端 payload。

## 6. 后端改造点

### 6.1 Agent 路由

文件：

- `backend/src/server/routes/agents.py`

需要：

- 保持现有 `callableAgents` 和 `maxDepth`。
- 创建 / 更新 Agent 时校验：
  - 不能选择自己作为子 Agent。
  - 不能重复。
  - 最好过滤不存在的 Agent ID。
- 导入 / 导出 Agent 时继续包含 `callableAgents`。

### 6.2 Agent Factory

文件：

- `backend/src/agents/factory.py`

需要新增：

- `SubagentRuntimeSpec`
- `CallAgentTool`
- `_create_subagent_tools(...)`
- `get_or_create_agent(..., subagents=..., subagent_context=...)`

建议结构：

```python
@dataclass(frozen=True)
class SubagentRuntimeSpec:
    id: str
    name: str
    description: str
    system_prompt: str
    model_config: dict
    guardrails: AgentGuardrails
    mcp_tools: list[str]
    knowledge_tools: list[str]
    skills: list[str]
    memory_enabled: bool
```

`CallAgentTool` 负责：

- 参数校验
- 深度检查
- 循环检查
- 创建子 Agent
- 执行子 Agent
- 返回结构化结果
- 调用 trace callback

### 6.3 Chat 路由

文件：

- `backend/src/server/routes/chat.py`

需要：

- 从当前 Agent 配置读取 `callableAgents`。
- 构建 `SubagentRuntimeSpec`。
- 注入到 `get_or_create_agent`。
- 记录以下事件：
  - `SUBAGENT_CALL_START`
  - `SUBAGENT_CALL_END`
  - `SUBAGENT_CALL_FAILED`
- 将 subagent 调用映射成前端可展示的 tool call 或新增 `subagentCalls`。

第一阶段推荐先复用 tool call 展示：

- 工具名：`CallAgent_<子Agent名称>`
- 工具结果：子 Agent 输出摘要
- Trace：额外展示完整 payload

这样前端改动最小。

### 6.4 Trace

文件：

- `backend/src/server/routes/traces.py`
- `frontend/src/pages/TracePage.tsx`

新增事件 payload：

```json
{
  "parentAgentId": "...",
  "parentAgentName": "...",
  "subagentId": "...",
  "subagentName": "...",
  "task": "...",
  "depth": 1,
  "maxDepth": 2,
  "status": "completed",
  "outputPreview": "...",
  "durationMs": 1234
}
```

Trace 页面应展开：

- `SUBAGENT_CALL_START`
- `SUBAGENT_CALL_END`
- `SUBAGENT_CALL_FAILED`

### 6.5 记忆 Hook

文件：

- `backend/src/server/routes/memory.py`

需要确认：

- 子 Agent 运行结束后是否触发 `maybe_summarize_agent_run`。
- metadata 增加：

```json
{
  "source": "subagent_call",
  "parentAgentId": "...",
  "parentAgentName": "...",
  "rootSessionId": "..."
}
```

## 7. 前端改造点

### 7.1 Agent 编辑器

文件：

- `frontend/src/features/agents/AgentEditor.tsx`

在“协作”区域补充：

- 子 Agent 选择器
- 已选择子 Agent 列表
- 最大调用深度
- 简短说明

交互建议：

- 选择器排除当前 Agent。
- 以 Agent 卡片形式展示已选子 Agent：
  - 名称
  - 描述
  - 模型
  - 标签
  - 移除按钮
- 容器无边框，子 Agent item 有边框，保持当前 UI 规则。

### 7.2 Chat 页面

文件：

- `frontend/src/pages/ChatPage.tsx`
- `frontend/src/hooks/useEventStream.ts`

第一阶段：

- subagent 调用复用工具调用卡片。
- 展示名规范为：

```text
子 Agent：架构 Agent
```

后续增强：

- 新增 Subagent Activity 卡片。
- 点击卡片右侧打开子 Agent 详情侧栏。
- 展示子 Agent 的输入、输出、工具调用、Skills 使用和记忆候选。

### 7.3 Agent Builder

文件：

- `backend/src/server/routes/agent_builder.py`
- `frontend/src/pages/AgentTeamPage.tsx`

第二阶段再做：

- Builder 生成单 Agent 时，可以推荐 callable subagents。
- 如果缺少合适 Agent，提醒用户先创建对应 Agent。

## 8. API 设计

第一阶段无需新增公开 API，只扩展已有 Agent CRUD。

可能需要新增内部 helper：

```python
def resolve_subagent_runtime_specs(agent_ids: list[str]) -> list[SubagentRuntimeSpec]:
    ...
```

如果后续要支持前端单独调试子 Agent，可新增：

```http
POST /api/agents/{agent_id}/subagents/test
```

请求：

```json
{
  "subagentId": "...",
  "task": "...",
  "context": "..."
}
```

## 9. 运行事件设计

新增 `run_events.event_type`：

- `SUBAGENT_CALL_START`
- `SUBAGENT_CALL_END`
- `SUBAGENT_CALL_FAILED`

状态：

- `running`
- `completed`
- `failed`
- `blocked`

blocked 用于：

- 超过最大深度
- 检测到循环调用
- 子 Agent 未授权
- 子 Agent 不存在

## 10. 安全规则

必须实现：

- 禁止 Agent 调用自己。
- 禁止 call stack 循环。
- 遵守最大深度。
- 主 Agent read-only 时，子 Agent 强制 read-only。
- 主 Agent network disabled 时，子 Agent 强制 network disabled。
- 子 Agent 调用记录必须进入 Trace。
- 子 Agent 工具输出需要长度限制。

建议实现：

- 每轮最大 subagent 调用次数限制。
- 每次 subagent 调用独立超时。
- 输出长度限制，避免主 Agent 上下文爆炸。
- 失败时返回结构化错误，不中断整个主 Agent，除非主 Agent 自己决定失败。

## 11. 分阶段实施计划

### 阶段 1：最小可用闭环

目标：主 Agent 能选择并调用子 Agent。

任务：

- Agent 编辑器增加子 Agent 多选。
- 后端校验 `callableAgents`。
- Factory 增加 `CallAgentTool`。
- Chat runtime 注入 subagent tools。
- Trace 记录 `SUBAGENT_CALL_*`。
- Chat 复用工具调用卡片展示子 Agent 调用。
- 补 smoke 测试。

验收：

- 创建 Agent A、Agent B。
- A 配置 B 为子 Agent。
- Chat 选择 A，提示“请让子 Agent 帮我分析 xxx”。
- A 可以调用 B，并把 B 的结果纳入最终回答。
- Trace 中能看到 B 的调用开始、结束、输出摘要。

### 阶段 2：体验增强

目标：Subagent 调用在前端有独立体验。

任务：

- Chat 新增 Subagent 调用卡片。
- 点击子 Agent 卡片打开右侧详情。
- 展示子 Agent 的工具调用、Skills、模型、耗时、token/cache。
- 子 Agent HITL 审批接入输入框 dock。
- 子 Agent 记忆候选显示来源。

验收：

- 用户能区分普通工具调用和子 Agent 调用。
- 子 Agent 内部工具调用可追踪。
- HITL 不需要翻历史消息找审批按钮。

### 阶段 3：Builder 和策略化

目标：智能创建 Agent 时自动规划 subagent 关系。

任务：

- Agent Builder 识别“主 Agent + 专家子 Agent”场景。
- 生成 `callableAgents` 建议。
- 当缺少合适子 Agent 时提示用户创建。
- 增加 per-subagent policy：
  - 是否自动调用
  - 是否需要审批
  - 输出长度
  - 调用预算

验收：

- 用户输入“创建一个产品经理 Agent，需要能调用架构和数据分析专家”。
- Builder 生成主 Agent 和两个子 Agent。
- 主 Agent 自动配置 callableAgents。

## 12. 测试计划

### 后端

建议新增 pytest / TestClient：

- Agent 创建时不能选择自己为子 Agent。
- Agent 更新时过滤重复子 Agent。
- 超过 maxDepth 时子 Agent 工具返回 blocked。
- 循环调用 A -> B -> A 被拒绝。
- read_only 主 Agent 会强制子 Agent read_only。
- `SUBAGENT_CALL_*` 写入 `run_events`。

### 前端

建议测试：

- Agent 编辑器能选择 / 移除子 Agent。
- 当前 Agent 不出现在自己的子 Agent 候选列表。
- 保存后刷新仍保留选择。
- Chat 工具卡能展示子 Agent 调用。
- Trace 能展开 subagent payload。

### 人工测试案例

准备两个 Agent：

- 主 Agent：产品经理 Agent
- 子 Agent：架构 Agent

主 Agent 配置：

- `callableAgents = [架构 Agent]`
- `maxDepth = 2`

输入：

```text
请设计一个订单系统的 MVP。遇到技术架构问题时，请调用你的架构子 Agent 给出建议，然后你再汇总成最终方案。
```

预期：

- 主 Agent 调用架构 Agent。
- Chat 中展示子 Agent 调用。
- 主 Agent 最终回答包含架构 Agent 的建议。
- Trace 中有 `SUBAGENT_CALL_START` 和 `SUBAGENT_CALL_END`。

## 13. 设计取舍

### 为什么不直接用 Team？

Team 是显式协作，适合用户能看到每个成员执行过程的任务。Subagent 是隐式委派，适合一个主 Agent 自主调用专家能力。两者需要共存。

### 为什么第一阶段复用工具调用卡片？

当前 Chat 已经有稳定的工具调用反馈、历史回显和 Trace。复用工具调用卡片可以快速打通闭环，避免第一阶段在 UI 上做过多分叉。

### 为什么子 Agent 默认使用自己的配置？

子 Agent 是独立 Agent，应该保留自己的 prompt、记忆、工具和权限。只在安全边界上继承主 Agent 的更严格限制，避免权限绕过。

## 14. 需要避免的问题

- 不要让 subagent 变成第二套 Team。
- 不要默认展示大量子 Agent 内部日志，第一阶段保持类似工具调用。
- 不要允许循环调用。
- 不要让子 Agent 绕过主 Agent 的安全边界。
- 不要把子 Agent 的完整记忆暴露到前端 Trace payload。
- 不要让 subagent 输出无限进入主 Agent 上下文，需要裁剪。

## 15. 推荐下一步

建议下一轮开发先做阶段 1：

1. Agent 编辑器补子 Agent 选择器。
2. 后端 Agent 更新校验 `callableAgents`。
3. Factory 实现 `CallAgentTool`。
4. Chat 注入 subagent tools。
5. Trace 记录 `SUBAGENT_CALL_*`。
6. 跑 smoke 测试和前端 build。

