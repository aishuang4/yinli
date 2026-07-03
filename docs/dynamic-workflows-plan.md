# Dynamic Workflows 技术规划

> 状态：概念研究与后续候选功能。当前不进入实现，仅作为后续评估和设计依据。

## 概念定义

Dynamic Workflows 指由模型根据用户目标动态生成执行流程，而不是由用户提前固定编排完整流程。系统会先判断任务复杂度，再动态拆解子任务、选择 Agent/Team/Tool/Skill、生成依赖图，并在执行中根据结果决定是否跳过、重试、补充信息或重新规划。

它不是替代现有 Agent 和 Team，而是更高一层的动态编排能力：

- **Agent**：执行单一角色任务。
- **Team**：预设成员之间协作。
- **Workflow**：预设流程节点和依赖。
- **Dynamic Workflow**：运行时由模型生成临时流程图，再交给执行器调度。

## 适用场景

- 用户目标复杂，但不清楚应该选哪个 Agent 或 Team。
- 任务需要先检索、再分析、再审查、再总结。
- 任务中部分步骤可并行，部分步骤依赖前置结果。
- 工具、Skills 或 MCP 是否需要使用，取决于模型对任务的动态判断。
- 需要把一次成功的动态协作沉淀为 Team 模板或固定 Workflow 模板。

## 与现有系统的融合方式

现有能力可以复用：

- `Agent Builder`：扩展为 Dynamic Workflow Planner，负责生成动态执行图。
- `Team Runtime`：复用 worker 并行/串行依赖、Leader 汇总和 worker 运行状态。
- `run_events`：记录 workflow 节点状态、依赖等待、重试、跳过、重规划、工具调用和 HITL。
- `HITL`：用于确认模型选择的 Agent/Tool/Skill，或在信息不足时收集用户输入。
- `Trace`：展示动态图运行轨迹。
- `Sandbox`：保存动态 workflow 测试案例，回放同一目标的执行稳定性。
- `Memory Hook`：仅作为未来记忆模块重设计后的可选扩展。旧记忆模块已删除，当前 Dynamic Workflow 设计不得依赖旧记忆链路。

## 第一版建议边界

第一版不做拖拽画布，不做复杂 BPMN，不做长期运行任务。建议只做 Chat 内的“动态编排模式”：

1. 用户在 Chat 选择“动态编排”。
2. 系统先调用 planner 生成结构化执行图。
3. 前端展示简洁确认条，默认不打断用户，必要时才要求确认。
4. 执行器按依赖运行节点，支持并行、串行、跳过、失败停止。
5. 结果进入现有 Chat 消息、Team Activity 和 Trace。
6. 执行完成后允许保存为 Team 模板或固定 Workflow 模板。

## 核心数据模型草案

```json
{
  "id": "dynamic_workflow_run_id",
  "goal": "用户目标",
  "mode": "dynamic_workflow",
  "nodes": [
    {
      "id": "research",
      "type": "agent",
      "title": "资料检索",
      "agent_id": "optional_existing_agent_id",
      "role_prompt": "临时角色说明",
      "tools": ["knowledge_search"],
      "skills": [],
      "depends_on": [],
      "failure_policy": "stop"
    }
  ],
  "edges": [
    {
      "from": "research",
      "to": "analysis"
    }
  ],
  "output_policy": {
    "leader_summary": true,
    "save_as_template": false
  }
}
```

## Runtime 关键能力

- **复杂度判断**：简单任务直接走单 Agent，复杂任务进入动态编排。
- **资源选择**：从已配置 Agent、MCP、Skills、HTTP 工具中选择资源，不凭空声称可用工具。
- **动态节点执行**：每个节点可对应 Agent、Team、Tool、Skill、Human Input 或 Condition。
- **依赖调度**：无依赖节点并行，有依赖节点等待前置输出。
- **结果校验**：节点输出不满足要求时允许重试或触发重规划。
- **可观测性**：所有节点状态写入 `run_events`。
- **安全边界**：工具权限仍继承 Agent/Team 权限策略，危险动作继续走 HITL。

## 主要风险

- 模型生成的执行图可能过度复杂，导致成本高、耗时长。
- 动态选择工具/Skills 时可能出现幻觉，需要严格基于已注册资源。
- 重规划如果没有次数限制，容易进入循环。
- 前端展示如果太详细，会重新变成“噪音面板”。
- 动态临时 Agent 是否落库需要谨慎，默认不落库，只保存运行记录。

## 后续决策点

- 是否把 Dynamic Workflows 作为 Chat 的第三种运行模式。
- 是否先实现固定 Workflow，再实现 Dynamic Workflow。
- 是否允许用户一键保存动态执行图为模板。
- 是否需要独立 Workflow 页面，还是先嵌入 Chat 和 Trace。
- 是否引入图执行库，还是基于现有 Team Runtime 自研轻量调度器。

## 建议结论

短期不实现。等 Agent、Team、Trace、Sandbox、Electron 打包稳定后，再把 Dynamic Workflows 作为下一代编排能力评估。它应该复用现有 runtime 和可观测体系，而不是另起一套孤立流程引擎。
