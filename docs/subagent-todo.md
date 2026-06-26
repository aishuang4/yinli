# Subagent 功能实现 TODO

> 状态：历史文档。旧 Subagent Runtime 已于 2026-06-26 下线，本文只保留为设计演进记录。后续重新设计 Subagent 时必须新建 TODO，不要继续按本文勾选项实现。
>
> 更新时间：2026-06-23
>
> 参考设计文档：`docs/subagent-feature-plan.md`
>
> 目标：补齐“主 Agent 可调用子 Agent”的能力。当前项目已经打通第一阶段运行链路，本 TODO 用于记录剩余增强项和后续协作事项。

## 当前状态

已存在：

- 后端 Agent 模型已有 `callableAgents` 和 `maxDepth`。
- SQLite `agents` 表已有 `callable_agents` 和 `max_depth`。
- 前端 Agent 类型已有 `callableAgents` 和 `maxDepth`。
- Agent 编辑器已有“最大调用深度”输入框。
- Chat 已有工具调用展示、Trace、HITL、Memory、模型 fallback、权限配置等基础设施。
- 主 Agent 通过 `CallAgent_<子Agent名称>` 工具显式委派子 Agent。
- 子 Agent 使用自己的 system prompt、记忆、Skills、MCP / HTTP 工具和模型配置。
- 子 Agent 前端已从普通工具调用中拆出，Chat 中有专用调用卡片，点击后可在右侧详情栏查看委派任务、上下文、期望输出、执行结果和原始调用。

缺失：

- 子 Agent 记忆 hook 暂不实现：当前定位为隔离执行单元，只把结果返回主 Agent，不沉淀独立长期记忆。
- 子 Agent 内部工具调用、Skills、token/cache 信息还没有独立详情模型，当前只展示子 Agent 最终输出和原始调用 JSON。
- 子 Agent HITL resume 还未做专用前端反馈。

## 上下文传递策略结论

当前采用 **显式委派上下文**，暂不做“精简 / 摘要 / 最近 N 轮”的复杂策略配置。

设计原则：

- 不把主 Agent 的 system prompt 直接共享给子 Agent。
- 子 Agent 的 system prompt 始终来自子 Agent 自己，并叠加子 Agent 自己的记忆和 Skills。
- 主 Agent 通过 `CallAgent` 工具参数生成一次委派消息，包含当前场景、任务、必要上下文和期望输出。
- 子 Agent 在隔离上下文中完成任务，只把最终结果返回给主 Agent。
- 后续只有在真实使用中发现“子 Agent 缺少背景信息”时，再考虑增加摘要策略；近期不作为主线。

## 阶段 1：最小可用闭环

目标：主 Agent 能选择子 Agent，并在运行时像工具一样调用子 Agent。

### 1. Agent 编辑器补子 Agent 选择 UI

文件：

- `frontend/src/features/agents/AgentEditor.tsx`
- `frontend/src/types/agent.ts`

TODO：

- [x] 在“协作”区域增加“可调用子 Agent”多选。
- [x] 候选列表排除当前正在编辑的 Agent。
- [x] 候选列表展示 Agent 名称、描述、模型、标签。
- [x] 已选择子 Agent 以列表项/卡片展示，并提供移除按钮。
- [x] 保持现有 UI 规则：外层容器无边框，子 Agent item 有边框。
- [x] 保存时把 `callableAgents` 原样提交到后端。
- [x] 刷新页面后能正确回显已选择子 Agent。

验收：

- 编辑 Agent A 时不能选择 A 自己。
- 可以选择 Agent B / C 作为子 Agent。
- 保存后重新打开 Agent A，已选择子 Agent 仍然存在。

### 2. 后端 Agent CRUD 校验 callableAgents

文件：

- `backend/src/server/routes/agents.py`

TODO：

- [x] 创建 Agent 时过滤重复的 `callableAgents`。
- [x] 更新 Agent 时过滤重复的 `callableAgents`。
- [x] 禁止 Agent 选择自己作为子 Agent。
- [x] 对不存在的 Agent ID 给出明确策略：
  - 推荐第一阶段：过滤不存在的 ID，并返回正常结果。
  - 如果选择报错，前端要能显示错误。
- [x] 导入 Agent 时保留 `callableAgents`，但对缺失引用给 warning。
- [x] 删除 Agent 时，从其他 Agent 的 `callableAgents` 中移除该 Agent ID。

验收：

- A 不能把自己保存进 `callableAgents`。
- 重复选择 B 时数据库里只保留一个 B。
- 删除 B 后，A 的 `callableAgents` 自动移除 B。

### 3. 定义 Subagent Runtime Spec

文件：

- `backend/src/agents/factory.py`
- `backend/src/server/routes/chat.py`

TODO：

- [x] 新增 `SubagentRuntimeSpec` dataclass。
- [x] 字段至少包含：
  - `id`
  - `name`
  - `description`
  - `system_prompt`
  - `model_config`
  - `guardrails`
  - `mcp_tools`
  - `knowledge_tools`
  - `skills`
  - `memory_enabled`
- [x] 在 `chat.py` 中从当前 Agent 的 `callableAgents` 构建 spec 列表。
- [x] 子 Agent 不存在时跳过，并写入 Trace warning。

验收：

- Chat 运行 Agent A 时，后端能拿到 A 配置的子 Agent specs。

### 4. 实现 CallAgentTool

文件：

- `backend/src/agents/factory.py`

TODO：

- [x] 新增 `CallAgentTool`。
- [x] 工具名格式：`CallAgent_<子Agent名称>`。
- [x] 工具 description 说明该子 Agent 的职责。
- [x] 工具输入 schema：

```json
{
  "task": "要交给子 Agent 的任务",
  "context": "必要上下文，可选",
  "expected_output": "期望输出格式，可选"
}
```

- [x] 工具输出包含：
  - `agentId`
  - `agentName`
  - `status`
  - `output`
  - `durationMs`
  - `error`
- [x] 输出文本做长度裁剪，第一阶段建议最大 8000 字符。
- [x] 子 Agent 调用失败时返回结构化错误，不让整个主 Agent 直接崩掉。

验收：

- 主 Agent 的工具列表里能看到 `CallAgent_xxx`。
- 主 Agent 能调用该工具并拿到子 Agent 输出。
- 子 Agent 出错时，主 Agent 能收到错误信息并继续组织回答。

### 5. 深度控制和循环检测

文件：

- `backend/src/agents/factory.py`
- `backend/src/server/routes/chat.py`

TODO：

- [x] 设计并传递 subagent runtime context：

```json
{
  "rootAgentId": "A",
  "currentDepth": 1,
  "maxDepth": 2,
  "callStack": ["A"]
}
```

- [x] `currentDepth >= maxDepth` 时，不再注入 subagent tools。
- [x] 目标子 Agent 已在 `callStack` 中时，拒绝调用。
- [x] 每轮最大 subagent 调用次数限制，第一阶段可设为 8。
- [x] 被拒绝时返回 `blocked` 状态，并写入 Trace。

验收：

- A -> B -> A 的循环调用会被拒绝。
- `maxDepth = 1` 时，A 可以调用 B，但 B 不再拥有可调用子 Agent。
- 超过调用次数限制时，工具返回 blocked。

### 6. 权限继承和安全边界

文件：

- `backend/src/agents/factory.py`
- `backend/src/server/routes/chat.py`

TODO：

- [x] 子 Agent 默认使用自己的 guardrails。
- [x] 如果主 Agent 是 `read_only`，子 Agent 强制降级为 `read_only`。
- [x] 如果主 Agent `networkEnabled = false`，子 Agent 也强制禁用网络。
- [x] 如果主 Agent 使用 Docker workspace，子 Agent 默认使用同样 workspace scope。
- [x] 子 Agent 只使用自己配置的 MCP / Skills / HTTP 工具，不继承主 Agent 的工具选择。

验收：

- 主 Agent read-only 时，子 Agent 无法使用 Write/Edit/Bash/HTTP/MCP。
- 主 Agent 禁网时，子 Agent 不能调用网络工具。
- 子 Agent 不会拿到主 Agent 未授权的工具。

### 7. Chat runtime 注入 subagent tools

文件：

- `backend/src/server/routes/chat.py`
- `backend/src/agents/factory.py`

TODO：

- [x] 扩展 `get_or_create_agent(...)` 参数，支持 `subagents` 和 `subagentContext`。
- [x] 主 Agent 创建时注入 subagent tools。
- [x] 子 Agent 创建时根据深度决定是否继续注入自己的 subagent tools。
- [x] Agent cache key 要包含 subagent 配置摘要，避免缓存复用旧工具列表。

验收：

- 修改 A 的 `callableAgents` 后，不重启后端也能在下一轮 Chat 生效。
- 子 Agent 工具不会串到其他 Agent。

### 8. Trace 事件

文件：

- `backend/src/server/routes/chat.py`
- `backend/src/server/routes/traces.py`
- `frontend/src/pages/TracePage.tsx`

TODO：

- [x] 新增 `SUBAGENT_CALL_START`。
- [x] 新增 `SUBAGENT_CALL_END`。
- [x] 新增 `SUBAGENT_CALL_FAILED`。
- [x] blocked 场景可以记录 `SUBAGENT_CALL_FAILED`，status 为 `blocked`。
- [x] payload 至少包含：

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

- [x] Trace 页面展开 `SUBAGENT_CALL_*` payload。

验收：

- 子 Agent 调用开始和结束都能在 Trace 看到。
- 子 Agent 调用失败或被 blocked 时 Trace 有明确原因。

### 9. Chat 前端展示

文件：

- `frontend/src/pages/ChatPage.tsx`
- `frontend/src/hooks/useEventStream.ts`

TODO：

- [x] `CallAgent_xxx` 工具展示名改为 `子 Agent：xxx`。
- [x] 工具输入展示 task/context。
- [x] 工具结果展示子 Agent 输出摘要。
- [x] 历史消息中能回显子 Agent 调用。
- [x] 不要和 Team worker 展示混在一起。
- [x] Chat 中新增 Subagent 调用卡片。
- [x] 点击 Subagent 卡片后，右侧详情栏展示子 Agent 输入、输出、状态、耗时和原始调用。

验收：

- Chat 中能看出调用了哪个子 Agent。
- 子 Agent 输出不会把消息卡片撑爆。
- 历史会话打开后仍能看到子 Agent 调用。

### 10. 子 Agent 记忆 hook（暂不实现）

决策：当前先砍掉该功能，不进入近期开发主线。

原因：

- 当前 Subagent 定位是隔离执行单元，不是长期自成长专家。
- 子 Agent 每次执行只接收主 Agent 显式委派的场景、任务、上下文和期望输出，结束后把结果返回主 Agent 即可。
- 如果对子 Agent 也做长期记忆，容易把一次性委派任务沉淀为长期事实，增加记忆污染和审核成本。

保留边界：

- 子 Agent 运行时仍然可以读取它自己已有的记忆。
- 子 Agent 调用过程继续写 Trace / 工具调用记录。
- 后续如果出现明确需求，例如“专家子 Agent 需要跨会话学习自己的工具使用经验”，再重新评估。

## 阶段 1 整体验收案例

准备：

- Agent A：产品经理 Agent。
- Agent B：架构 Agent。
- A 配置：
  - `callableAgents = [B]`
  - `maxDepth = 2`

输入：

```text
请设计一个订单系统 MVP。遇到技术架构问题时，请调用你的架构子 Agent 给出建议，然后你再汇总成最终方案。
```

预期：

- A 调用 B。
- Chat 工具卡显示 `子 Agent：架构 Agent`。
- A 最终回答包含 B 的建议。
- Trace 中有 `SUBAGENT_CALL_START` 和 `SUBAGENT_CALL_END`。
- B 如果使用工具，工具调用出现在 B 的 subagent trace payload 或后续详情里。
- 如果 B 被配置为不能联网，调用网络工具会被限制。

## 阶段 2：体验增强

目标：让子 Agent 调用在 Chat 中更像一个轻量活动，而不是普通工具。

TODO：

- [x] 新增独立 `subagentCalls` 前端类型和后端持久化字段，不再长期复用 `toolCalls`。
- [x] Chat 中新增 Subagent 调用卡片。
- [x] 点击 Subagent 卡片后，右侧详情栏展示：
  - 子 Agent 输入
  - 子 Agent 输出
  - 耗时
- [ ] 右侧详情栏继续补充：
  - 子 Agent 工具调用
  - 子 Agent Skills
  - token/cache 信息
- [ ] 子 Agent HITL 审批接入输入框 dock。
- [ ] 子 Agent 失败时提供可读错误状态。

验收：

- 用户能区分普通工具调用和子 Agent 调用。
- 子 Agent 内部过程可查看但不会默认挤占主对话。

## 阶段 3：Agent Builder 接入

目标：智能创建 Agent 时能自动建议 subagent 关系。

文件：

- `backend/src/server/routes/agent_builder.py`
- `frontend/src/pages/AgentTeamPage.tsx`

TODO：

- [x] Builder 识别“主 Agent + 专家子 Agent”需求。
- [x] 生成 Agent 草案时填充 `callableAgentRefs` 建议。
- [x] 如果缺少合适工具或 Skill，继续通过 `missingTools` / `missingSkills` 提醒用户创建并手动注入。
- [x] Apply plan 时自动把草案临时 ID 映射为真实 Agent ID，并回写主 Agent 的 `callableAgents`。
- [x] 前端 Builder 草案卡片展示子 Agent 关系。

实现说明：

- `agents` 模式不创建 Team，但允许生成“主 Agent + 专家子 Agent”。
- 模型规划可以通过 `callableAgents` 返回子 Agent 名称；后端会解析为草案 `tempId`。
- 本地兜底在需求包含“子 Agent / 调用 / 委派 / 专家”等意图时，会生成主 Agent、架构专家 Agent、数据分析专家 Agent，并让主 Agent 可调用两个专家。
- 新建完成后，子 Agent 仍是普通 Agent，可继续单独编辑工具、Skills、权限和提示词。
- 已补后端回归测试 `backend/test/test_agent_builder_subagents.py`，覆盖草案生成和 Apply 后真实 Agent ID 回写。

验收：

输入：

```text
创建一个产品经理 Agent，需要能调用架构专家和数据分析专家。
```

预期：

- Builder 生成主 Agent。
- Builder 生成或推荐两个子 Agent。
- 主 Agent 自动配置 callableAgents。

## 测试清单

### 后端 smoke / 单元测试

- [x] Agent 创建时过滤重复 `callableAgents`。
- [ ] Agent 更新时不能选择自己。
- [ ] 删除子 Agent 后，父 Agent 引用被清理。
- [ ] A -> B -> A 循环调用被拒绝。
- [ ] 超过 `maxDepth` 被拒绝。
- [ ] read-only 主 Agent 强制子 Agent read-only。
- [x] `SUBAGENT_CALL_*` 写入 `run_events`。
- [x] Agent Builder 在 agents 模式下生成主 Agent + 专家子 Agent 草案。
- [x] Agent Builder Apply 时把草案 `callableAgentRefs` 映射为真实 `callableAgents`。
- [ ] 子 Agent 调用失败不会导致主 Agent SSE 直接断流。

### 前端测试

- [ ] Agent 编辑器可选择子 Agent。
- [ ] 当前 Agent 不出现在候选列表。
- [ ] 已选子 Agent 可移除。
- [ ] 保存后刷新仍回显。
- [x] Chat 展示子 Agent 调用。
- [x] 历史会话回显子 Agent 调用。
- [x] Trace 展开 subagent payload。

### 必跑命令

```bash
cd /Users/aishuang/agent-workstation/backend
.venv/bin/python -m py_compile $(find src -name '*.py' -print)
```

```bash
cd /Users/aishuang/agent-workstation/frontend
PATH=/Users/aishuang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm run lint
```

```bash
cd /Users/aishuang/agent-workstation/frontend
PATH=/Users/aishuang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm run build
```

## 开发注意事项

- 不要把 Subagent 做成第二套 Team。
- Subagent 第一阶段已经打通闭环，当前前端有专用卡片和右侧详情；底层已使用独立 `subagentCalls` 字段持久化，旧历史可从 `toolCalls` 兼容派生。
- 不要让子 Agent 绕过主 Agent 的安全边界。
- 不要把子 Agent 的完整记忆写入 Trace payload。
- 子 Agent 输出必须裁剪，避免塞爆主 Agent 上下文。
- 主 Agent 只通过委派消息告诉子 Agent 当前场景和任务，不直接共享主 Agent system prompt。
- 每完成一个阶段再提交，不要每个小改都提交。
