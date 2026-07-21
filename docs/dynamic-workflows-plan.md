# Dynamic Workflow 改造设计

> 状态：2026-07-08 决策版草案。本文替代早期“Chat 内动态编排模式”规划。
>
> 结论：Dynamic Workflow 必须作为独立工作空间推进，核心不是流程图可视化，而是“模型生成可审查的执行脚本，Runtime 按脚本大规模编排 AgentScope Agent，并把阶段、变量、Agent 任务和产物稳定落库展示”。

## 背景与参考

本轮设计参考两类资料：

- Claude Code 官方 Workflows 文档：强调 workflow 是 Claude 生成的可执行脚本，脚本负责并行、循环、pipeline、聚合和交叉验证；中间状态保存在脚本变量中，而不是持续塞回主会话上下文。
- Dynamic Workflow 相关文章：强调把控制平面从对话迁移到代码 / spec，形成可审查、可复跑、可恢复的 agentic workflow，而不是只把子任务画成图。

结合本项目现状，AgentScope 继续作为 Agent 执行单元；LangGraph / LangChain 只作为 workflow 编排和状态推进能力候选，不接管产品事实状态。

## 当前 MVP 偏差

当前 `codex/dynamic-workflow` 分支上的 MVP 已经打通独立入口、SQLite 表、Workflow SSE 和动画舞台，但产品心智仍偏向：

```text
用户目标 -> 默认四个节点 -> 动画流转
理解目标 -> 执行任务 -> 校验结果 -> 生成总结
```

这更像“漂亮的任务进度条”，不是真正的 Dynamic Workflow。

真正需要表达的是：

```text
用户目标
  -> 模型生成 workflow script/spec
  -> 用户审查阶段、预算、权限和原始脚本
  -> Runtime 编译并执行脚本
  -> 动态产生阶段、批次、Agent tasks、变量、产物和事件
  -> 用户可暂停、恢复、重试、复跑、保存模板
```

因此后续改造不应继续打磨固定四节点动画，而要先修正数据模型、运行时模型和前端信息架构。

## 产品定义

Dynamic Workflow 是引力 YINLI 的第二工作空间：

- **工作台模式**：Chat / Agents / Tools / Memory / Trace 等现有功能，适合开放式对话和即时任务。
- **任务流模式**：可审查脚本驱动的长任务编排，适合批量审计、迁移、调研、验证、循环修复和多 Agent 大规模协作。

一句话定位：

> Dynamic Workflow 是一个可审查、可复跑、可恢复的 Agent 编排脚本运行台。

## 设计原则

1. **Script-first，不是 Graph-first**
   - 图和动画只是运行时状态展示。
   - 核心产物是 `workflow script/spec`。
   - 用户必须能看到阶段、并发策略、循环条件、验证策略、预算和权限摘要。

2. **SQLite 是事实来源**
   - LangGraph / LangChain 可以作为执行引擎。
   - 产品状态必须落本项目 SQLite。
   - 刷新、重启、恢复、审计都从 SQLite 恢复。

3. **AgentScope 是执行单元**
   - Workflow 节点不重新实现工具、MCP、Skills、Memory、Permission。
   - 节点通过 bridge 调用现有 AgentScope Agent / Team / Skill。
   - 权限、HITL、Trace 和 ToolScope 继续继承现有体系。

4. **Workflow SSE 与 Chat SSE 隔离**
   - Workflow 使用 `/api/workflows/{run_id}/events`。
   - 前端不依赖 Chat RunState。
   - Workflow 事件稳定契约见 `docs/workflow-event-contract.md`。

5. **运行轨迹动态生成**
   - 创建时不提前固定全部节点。
   - Script Review 阶段展示的是脚本结构。
   - 执行时根据脚本动态产生 stage / task / variable / artifact / event。

## 目标用户体验

### 1. 创建

用户输入复杂目标，例如：

```text
审计当前项目的记忆系统，找出高风险架构问题，要求并行扫描后端、前端、文档和测试，并生成可执行修复计划。
```

系统不直接播放四个节点，而是生成 Workflow Script 草稿。

### 2. Script Review

创建后进入 `review` 状态，页面展示：

- Workflow 名称
- 适用场景说明
- 阶段列表
- 每阶段 Agent 数量或 fan-out 策略
- 并发上限
- 循环 / 重试 / 失败策略
- 预计 token / 时间 / 风险
- 权限需求
- 输出 schema
- 原始 JSON / JS-like script

用户操作：

- 运行一次
- 编辑指令后重新生成
- 查看原始脚本
- 拒绝
- 保存为模板

### 3. Runtime Cockpit

运行中主区域展示的是动态运行台，而不是静态画布：

```text
Plan
  -> Fan-out: backend / frontend / docs / tests
  -> Barrier: 等待全部 Agent 结果
  -> Verify: 去重、交叉验证、风险排序
  -> Loop: 对高风险项二次确认
  -> Report: 生成最终报告
```

页面应突出：

- 当前阶段
- 并发 Agent 池
- 已完成 / 运行中 / 失败数量
- token、耗时、重试次数
- barrier / loop / aggregate 状态
- 每个 Agent task 的 prompt、工具调用、结果摘要
- 产物列表

### 4. Inspector

右侧详情不再只是“节点详情”，而是 tabs：

- `Script`：编排脚本、阶段配置、预算、权限摘要。
- `Variables`：运行时变量、批次输出、聚合结果、循环状态。
- `Agents`：动态派发的 Agent tasks、状态、耗时、token、输出摘要。
- `Events`：Workflow 事件流。
- `Artifacts`：报告、文件、结构化结果。

## 数据模型改造

当前已有表：

- `workflow_runs`
- `workflow_nodes`
- `workflow_edges`
- `workflow_events`
- `workflow_artifacts`

这些表可以保留，但语义需要调整：

- `workflow_nodes` 不再代表创建时固定流程图，而代表运行时实际生成的 stage / task / barrier / aggregate 轨迹。
- `workflow_edges` 只保存运行时实际出现的依赖关系或 script 编译后的静态结构，不作为唯一事实。

需要新增或扩展的核心表：

### `workflow_scripts`

保存模型生成的可审查脚本。

建议字段：

```text
id
run_id
schema_version
language              json | js_like
status                draft | approved | rejected | superseded
title
goal
summary
script_text
script_json
estimated_agents
estimated_tokens
estimated_minutes
risk_level
permission_summary
created_at
approved_at
```

### `workflow_variables`

保存运行时结构化中间状态。

建议字段：

```text
id
run_id
scope                 global | stage | task
scope_id
name
value_json
value_preview
created_at
updated_at
```

### `workflow_agent_tasks`

保存由 runtime 动态派发给 AgentScope 的任务。

建议字段：

```text
id
run_id
stage_id
agent_id
agent_name
team_id
task_title
prompt
status                pending | running | waiting | completed | failed | skipped
input_json
output_json
output_preview
token_input
token_output
started_at
completed_at
error
```

### `workflow_templates`

保存用户确认可复用的 workflow script。

建议字段：

```text
id
name
description
script_json
tags
created_at
updated_at
```

## Workflow Script 格式

第一版推荐使用 JSON 作为 canonical 格式，后续可导入 / 导出 YAML 或 JS-like 表达。

示例：

```json
{
  "schemaVersion": "workflow.script.v1",
  "title": "记忆系统架构审计",
  "goal": "并行审计后端、前端、文档和测试，找出记忆系统风险并生成修复计划",
  "concurrency": {
    "maxAgents": 4,
    "failurePolicy": "pause_on_stage_failure"
  },
  "stages": [
    {
      "id": "plan",
      "type": "planner",
      "instruction": "拆解审计范围，生成扫描任务"
    },
    {
      "id": "fanout_scan",
      "type": "fanout",
      "items": [
        {"key": "backend", "agentId": "code-analyst", "instruction": "审计后端记忆链路"},
        {"key": "frontend", "agentId": "frontend-reviewer", "instruction": "审计 Memory 页面与展示链路"},
        {"key": "docs", "agentId": "doc-auditor", "instruction": "审计文档与实现是否一致"},
        {"key": "tests", "agentId": "test-reviewer", "instruction": "审计测试覆盖缺口"}
      ],
      "collectAs": "scanReports"
    },
    {
      "id": "verify",
      "type": "verifier",
      "input": "$scanReports",
      "instruction": "去重、交叉验证、按风险排序"
    },
    {
      "id": "report",
      "type": "summary",
      "input": "$verifiedRisks",
      "outputSchema": {
        "risks": "array",
        "fixPlan": "array",
        "openQuestions": "array"
      }
    }
  ]
}
```

## 后端模块规划

建议目录：

```text
backend/src/server/routes/workflow/
  __init__.py
  router.py
  models.py
  planner.py
  script_schema.py
  script_review.py
  runtime.py
  graph_compiler.py
  agent_bridge.py
  event_bus.py
  persistence.py
```

职责：

- `planner.py`：根据用户目标生成 workflow script 草稿。
- `script_schema.py`：Pydantic 校验脚本结构。
- `script_review.py`：估算预算、权限、风险，生成 review 摘要。
- `runtime.py`：执行 workflow script，维护 run lifecycle。
- `graph_compiler.py`：把 script 编译为 LangGraph 或轻量自研执行计划。
- `agent_bridge.py`：调用现有 AgentScope Agent / Team / Skill。
- `event_bus.py`：写库并广播 Workflow SSE。
- `persistence.py`：封装 workflow 专属 SQLite 读写，避免继续膨胀 `database.py`。

## 前端改造规划

建议目录：

```text
frontend/src/pages/WorkflowPage.tsx
frontend/src/features/workflow/
  WorkflowRunList.tsx
  WorkflowCreatePanel.tsx
  ScriptReviewPanel.tsx
  RuntimeCockpit.tsx
  StageRail.tsx
  AgentTaskPool.tsx
  WorkflowInspector.tsx
  WorkflowEventTimeline.tsx
  WorkflowArtifactPanel.tsx
frontend/src/hooks/useWorkflowRuns.ts
frontend/src/hooks/useWorkflowEventStream.ts
frontend/src/types/workflow.ts
```

页面状态：

- `empty`：暂无 run。
- `drafting`：正在生成 script。
- `review`：等待用户审查并批准。
- `running`：Runtime Cockpit。
- `waiting`：等待权限 / 用户输入。
- `failed`：失败停驻，可重试 / 跳过 / 终止。
- `completed`：报告与产物视图。

视觉重点：

- 不做传统 graph canvas。
- 使用 stage rail + agent task pool + inspector。
- 动画表达阶段推进、fan-out、barrier、loop、aggregate。
- 动画服务状态理解，不做纯装饰。

## API 草案

```text
POST /api/workflows
  创建 run，并生成 script 草稿，返回 review 快照

GET /api/workflows
  列表

GET /api/workflows/{run_id}
  完整快照：run、script、stages、tasks、variables、events、artifacts

POST /api/workflows/{run_id}/script/regenerate
  根据用户补充要求重新生成脚本

POST /api/workflows/{run_id}/approve
  审批脚本并启动或等待用户手动 start

POST /api/workflows/{run_id}/start
  执行已审批脚本

POST /api/workflows/{run_id}/pause
POST /api/workflows/{run_id}/resume
POST /api/workflows/{run_id}/cancel
POST /api/workflows/{run_id}/tasks/{task_id}/retry

GET /api/workflows/{run_id}/events
  独立 SSE
```

## 事件契约改造

`docs/workflow-event-contract.md` 需要从当前 MVP 的六类事件扩展为：

```text
WORKFLOW_CREATED
SCRIPT_GENERATION_STARTED
SCRIPT_GENERATION_COMPLETED
SCRIPT_REVIEW_REQUIRED
SCRIPT_APPROVED
WORKFLOW_STARTED
STAGE_STARTED
AGENT_TASK_STARTED
AGENT_TASK_COMPLETED
AGENT_TASK_FAILED
VARIABLE_UPDATED
BARRIER_WAITING
STAGE_COMPLETED
WORKFLOW_WAITING
WORKFLOW_FAILED
WORKFLOW_COMPLETED
ARTIFACT_CREATED
```

前端仍只依赖稳定事件，不依赖 LangGraph 内部事件。

## 分阶段实施

### Phase 0：修正当前 MVP 方向

- [ ] 停止继续打磨固定四节点动画。
- [ ] 保留顶部工作空间切换和独立 Workflow 页面。
- [ ] 将当前 `WorkflowPage` 改为 `Script Review + Runtime Cockpit` 壳。
- [ ] 默认创建 run 后进入 review，不自动运行。

### Phase 1：Script Review MVP

- [ ] 定义 `workflow.script.v1` Pydantic schema。
- [ ] 后端先用规则模板生成 script 草稿，不依赖模型。
- [ ] 前端展示阶段、并发、预算、风险、权限和原始 JSON。
- [ ] 用户批准后进入运行。

### Phase 2：Runtime Cockpit MVP

- [ ] Runtime 按 script 动态生成 stage/task 事件。
- [ ] 支持 fan-out、barrier、aggregate 三种基本控制流。
- [ ] 前端展示 stage rail、agent task pool、variables、events。
- [ ] SQLite 支持恢复刷新后的运行状态。

### Phase 3：AgentScope Bridge

- [ ] `workflow_agent_tasks` 调用现有 AgentScope Agent。
- [ ] 继承 Agent guardrails、ToolScope、MCP、Skills、Memory 注入和 HITL。
- [ ] 记录每个 task 的输入、输出、token、错误和产物。

### Phase 4：LangGraph / LangChain 接入

- [ ] 评估是否用 LangGraph 执行 script 编译后的状态机。
- [ ] 若接入，LangGraph 只能做执行引擎，不能作为事实来源。
- [ ] SQLite checkpoint / workflow_events 必须仍可独立恢复产品状态。

### Phase 5：模板、复跑和真实长任务

- [ ] 保存 workflow template。
- [ ] 从历史 run 复跑。
- [ ] 支持编辑 script 后另存。
- [ ] 支持批量审计、批量迁移、release readiness、测试修复循环等内置模板。

## 风险与约束

- 不允许恢复旧独立 Plan Runtime（`runtime.kind=plan`）或旧独立 Subagent Runtime（`runtime.kind=subagent`）；Workflow 如需规划或委托能力，应复用当前 ReAct 内的 Plan middleware / `DelegateTask` 语义。
- 不把 Workflow 塞回 Chat Runtime。
- 不让前端依赖 LangGraph 内部事件。
- 不让模型凭空选择不存在的 Agent / Tool / Skill。
- 不让 workflow script 无限制循环或无限 fan-out。
- 所有危险工具调用继续走现有权限和 HITL。

## 对当前代码的处理建议

当前已新增的代码可以作为 Phase 0 的基础，但需要重构语义：

- `backend/src/server/routes/workflows.py`：保留 API 入口，后续拆到 `routes/workflow/` 包。
- `workflow_runs / workflow_nodes / workflow_edges / workflow_events / workflow_artifacts`：保留，但新增 scripts / variables / agent_tasks。
- `WorkflowPage`：保留工作空间入口，重写中间区为 Script Review / Runtime Cockpit。
- `ExecutionStage`：从固定节点路径改为 stage rail + agent task pool。
- `docs/workflow-event-contract.md`：按本文事件契约扩展。

## 当前结论

Dynamic Workflow 的亮点不是“可视化流程”，而是：

> 把多 Agent 的复杂任务编排变成可审查、可执行、可恢复、可复跑的脚本运行台。

前端动画仍然重要，但它应该表达阶段、并发、变量、验证、聚合和循环，而不是固定节点播放。
