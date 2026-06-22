# Plan Runtime v2 实现逻辑

## 目标

当前计划模式主要依赖 system prompt 约束模型输出，属于轻量计划提示层。v2 需要把计划升级为运行时能力：每次 Agent 或 Team 执行都生成可维护的计划，后端用结构化状态约束执行，前端用 Markdown 和状态数据展示进度。

核心目标：

- 让计划成为 Agent 执行过程的一等对象，而不是普通回复文本。
- 支持模型按计划一步步执行，并在每一步后更新计划。
- 支持用户审批、阻塞、重规划和最终总结。
- 前端能稳定展示计划进度，而不是从 Markdown 中猜状态。

## 总体设计

采用“三层计划”：

1. **Plan State**
   后端结构化保存真实状态，用于控制执行。

2. **Plan Markdown**
   Markdown 是模型可读、用户可看、可下载的计划文档。

3. **Plan Runtime Guard**
   后端在工具调用、HITL、重规划时检查当前计划状态，避免计划只是展示。

## 数据结构

新增表建议：

```sql
plans (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  team_id TEXT,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  markdown TEXT NOT NULL,
  current_step_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
)

plan_steps (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  step_index INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  status TEXT NOT NULL,
  assigned_agent_id TEXT,
  evidence TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL
)

plan_events (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  step_id TEXT,
  run_event_id TEXT,
  event_type TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
)
```

状态枚举：

- `draft`: 已生成计划，尚未执行。
- `waiting_approval`: 等待用户确认计划。
- `running`: 执行中。
- `replanning`: 正在修订计划。
- `blocked`: 遇到阻塞。
- `completed`: 已完成。
- `failed`: 执行失败。

步骤状态：

- `pending`
- `active`
- `completed`
- `blocked`
- `skipped`

## Markdown 格式

Markdown 不是唯一状态源，但必须由结构化状态生成，避免模型手写导致状态漂移。

```md
# Plan: <title>

## Goal
<goal>

## Constraints
- <constraint>

## Steps
- [ ] 1. <title> — pending
- [x] 2. <title> — completed
- [ ] 3. <title> — active

## Execution Log

### Step 1: <title>
Status: completed

Evidence:
<tool/event evidence>

Result:
<result>
```

后端提供 `render_plan_markdown(plan_id)`，所有前端展示和模型注入都读取这个渲染结果。

## 执行流程

### 单 Agent

1. 用户发送消息。
2. 后端判断是否需要计划：
   - `planConfig.enabled=false`: 不启用。
   - 纯聊天、简单问答：可跳过。
   - 涉及工具、文件、MCP、Skills、多步骤任务：启用。
3. 调用模型生成结构化计划 JSON。
4. 后端规范化为 `plans + plan_steps`。
5. 渲染 `plan.md`，作为上下文注入 Agent。
6. 如果 `requireUserApproval=true`，通过 HITL 暂停，等待用户确认或修改。
7. 执行时每次进入一个 active step。
8. 工具调用结果绑定到当前 step。
9. step 完成后更新状态和 Markdown。
10. 全部完成后生成最终回答，并把计划状态设为 `completed`。

### Team

Team 计划分两级：

- Leader Plan：整体任务拆解、worker 选择、依赖关系。
- Worker Plan：worker 自己的局部执行计划。

Leader 先生成整体计划：

```md
Step 1: 调研 Worker 收集资料
Step 2: 开发 Worker 修改代码，依赖 Step 1
Step 3: 审查 Worker 做风险检查，依赖 Step 2
Step 4: Leader 汇总
```

Worker 执行时：

- 并行 worker 可同时进入 active。
- 串行依赖 worker 必须等待依赖 step completed。
- 每个 worker 的工具调用绑定自己的 worker step。
- Leader 总结时必须读取 worker step result，而不是只靠上下文猜。

## Plan Runtime Guard

后端需要在 AgentScope 事件流外层做 guard：

- 如果计划要求 `requirePlanBeforeTools=true`，复杂任务没有 plan 不允许进入工具循环。
- 如果当前没有 active step，工具调用事件需要挂到 `unassigned`，同时产生 warning。
- 如果工具调用是写操作且当前 step 未被批准，需要走 HITL。
- 如果模型提出重规划，需要新增 `plan_events`，并更新 step 列表。
- 如果 step blocked，不能自动跳过，除非模型说明原因并生成 replanning。

第一阶段可以只做“记录和展示”，第二阶段再做强拦截。

## 前端展示

Chat 页面把 Plan Runtime 放到“已处理”折叠区里，位置建议：

1. Prompt Cache
2. Plan Runtime
3. 深度思考
4. 工具调用
5. Skills 调用
6. 记忆候选

Plan 展示两种视图：

- **进度视图**：步骤、状态、当前执行 agent、耗时。
- **Markdown 视图**：完整 `plan.md`，可复制和下载。

运行中默认展开当前 active step，任务完成后默认折叠。

## 与现有 planConfig 的关系

保留当前配置，但语义升级：

- `enabled`: 是否启用 Plan Runtime。
- `mode=lightweight`: 只生成简短 plan，可不落强 guard。
- `mode=structured`: 默认模式，落库、展示、绑定工具结果。
- `mode=strict`: 工具调用前必须有 active step；写操作必须审批。
- `maxSteps`: 结构化计划最大步骤数。
- `requirePlanBeforeTools`: 是否要求工具前有计划。
- `requireUserApproval`: 初始计划是否需要用户确认。
- `allowReplanning`: 是否允许执行中修改步骤。

## 分阶段落地

### 阶段一：计划持久化与展示

- 新增 `plans / plan_steps / plan_events` 表。
- 后端生成结构化计划并保存。
- 每轮对话保存 `plan.md`。
- Chat 前端展示计划进度和 Markdown。

### 阶段二：工具结果绑定

- 工具调用开始、结束事件绑定当前 step。
- step result 自动汇总工具结果。
- 历史会话恢复时能看到完整计划轨迹。

### 阶段三：HITL 审批

- `requireUserApproval` 接入 AgentScope HITL resume。
- 用户可批准、修改、拒绝计划。
- 修改后重新生成结构化 plan state。

### 阶段四：严格执行

- strict 模式下强制 active step。
- 写操作必须绑定可执行 step。
- blocked step 必须重规划或用户介入。

## 风险

- 如果只靠 Markdown，会出现状态漂移，所以必须有结构化状态。
- 多 worker 并行时要避免多个 worker 写同一个 plan step。
- 计划不能过度打扰普通聊天，需要有“轻量跳过”策略。
- 历史数据开发期可以清空，正式上线后需要迁移兼容。

## 推荐结论

Plan v2 应该做成“结构化状态 + Markdown 投影 + 运行时 guard”。Markdown 是上下文和展示，不是唯一真相。这样既保留了用户可读性，也能让系统真正按照计划执行。
