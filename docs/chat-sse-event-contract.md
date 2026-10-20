# Chat Event Stream V3 稳定契约

> 状态：2026-07-27 已在线。
> 本文是 Chat 实时展示、刷新恢复和历史回放的唯一前端事件契约。
> 旧 RunState、DisplayEvent、`RUN_STATE_PATCH`、顶层 `TEAM_EVENT` 和旧 Snapshot 不兼容、不迁移、不读取。

## 1. 稳定边界

Chat 展示只保留两个稳定边界：

- SSE 应用事件名：`RUNTIME_EVENT`。
- 完整展示状态：`DisplaySnapshot V4`。

SSE 连接中的 heartbeat 使用 comment：

```text
: heartbeat
```

heartbeat 不带 `seq`、不进入 replay log，也不进入前端 reducer。

## 2. RuntimeFrame

`RUNTIME_EVENT` 的 `data` 必须是以下五种形状之一。协议拒绝额外字段。

### 2.1 Open

```json
{
  "seq": 1,
  "op": "open",
  "id": "message:run-1",
  "kind": "message",
  "dataVersion": 1,
  "patch": { "role": "assistant" }
}
```

子 Block 使用 `parent` 指向已打开的父 Block：

```json
{
  "seq": 2,
  "op": "open",
  "id": "text:reply-1",
  "parent": "message:run-1",
  "kind": "text",
  "dataVersion": 1,
  "patch": ""
}
```

### 2.2 Delta

```json
{
  "seq": 3,
  "op": "delta",
  "id": "text:reply-1",
  "patch": "正在处理"
}
```

`patch` 的结构和合并语义只由 Block 的 `kind + dataVersion` 决定。公共协议不提供 mask、字段路径或动态赋值。

### 2.3 Close

```json
{
  "seq": 4,
  "op": "close",
  "id": "text:reply-1",
  "outcome": "completed",
  "patch": { "replace": "最终结果" }
}
```

`outcome` 只能是 `completed | failed | cancelled`。关闭后的 Block 不得再次 delta 或 close。

### 2.4 Snapshot

```json
{
  "seq": 4,
  "snapshot": {
    "version": 4,
    "lastSeq": 4,
    "streamDone": false,
    "order": ["message:run-1", "text:reply-1"],
    "blocksById": {
      "message:run-1": {
        "id": "message:run-1",
        "kind": "message",
        "dataVersion": 1,
        "phase": "open",
        "data": { "role": "assistant" }
      },
      "text:reply-1": {
        "id": "text:reply-1",
        "parent": "message:run-1",
        "kind": "text",
        "dataVersion": 1,
        "phase": "closed",
        "outcome": "completed",
        "data": "最终结果"
      }
    }
  }
}
```

Snapshot Frame 的 `seq` 必须等于 `snapshot.lastSeq`。前端收到 Snapshot 后整树替换，不把它与本地旧树合并。

### 2.5 Done

```json
{
  "seq": 6,
  "done": true
}
```

`done` 是唯一的正常传输收口信号，必须晚于所有业务 Block close。`streamDone=true` 的 Snapshot 不得包含 open Block。

## 3. DisplaySnapshot V4

```ts
type DisplaySnapshot = {
  version: 4;
  lastSeq: number;
  streamDone: boolean;
  order: string[];
  blocksById: Record<string, {
    id: string;
    parent?: string;
    kind: string;
    dataVersion: number;
    phase: "open" | "closed";
    outcome?: "completed" | "failed" | "cancelled";
    data: unknown;
  }>;
};
```

约束：

- `order` 与 `blocksById` 的 ID 集合必须完全一致且无重复。
- 父 Block 必须存在，并在 `order` 中早于子 Block。
- 最大嵌套深度为 16，单 Snapshot 最多 2000 个 Block。
- `phase=open` 时不能有 `outcome`；`phase=closed` 时必须有 `outcome`。
- 未注册的 `kind` 只展示公共元数据，不渲染 `data`。

## 4. Seq 与恢复

- `seq` 在单个 Run stream 内从 1 单调递增；不同 Run 之间不可比较。
- 前端只提交连续 Frame；重复或旧 `seq` 幂等忽略。
- 出现 gap 时先有界缓存并请求缺失 tail；无法补齐时请求 Snapshot 整树替换。
- `GET /api/chat/runs/{runId}/stream?projectId=...&afterSeq=N` 返回连续 tail；retention 已删除所需 Frame 时返回 Snapshot。
- 不传 `afterSeq` 时返回当前 Snapshot，再继续实时订阅。
- `GET /api/chat/runs/{runId}/state?projectId=...` 只返回 DisplaySnapshot V4。
- assistant 历史消息的 `displaySnapshot` 只保存 V4；不存在旧版本 decoder。
- `waiting_user` / `waiting_permission` 只暂停 Runtime，不结束同一 Run 的传输订阅。前端必须保持
  当前订阅跨越 HITL，resume 后继续从原 `lastSeq` 实时接收增量 Frame；只有 Run 进入
  `completed | failed | cancelled` 终态后，后端才发送 `done` 并关闭 subscriber。

## 5. 请求入口

- `POST /api/chat/send/start`：创建后台 Run，返回 Run 元数据。
- `GET /api/chat/runs/{runId}/stream`：订阅或恢复 RuntimeFrame。
- `GET /api/chat/runs/{runId}/state`：读取完整 DisplaySnapshot V4。
- `POST /api/chat/resume/start`：恢复原 HITL Run；返回原 `runId`，前端继续订阅同一 seq 空间。
- `POST /api/chat/runs/{runId}/cancel`：提交取消命令；展示终态仍由 RuntimeFrame 回流。

命令 API 的 HTTP 响应不是 UI 状态。Approval、AskUser、取消和重跑的展示变化必须由后端投影 Frame 驱动。

## 6. Block Kind

当前核心 registry：

| kind | 用途 | patch 语义 |
| --- | --- | --- |
| `message` | 单轮根节点 | 浅合并结构化字段 |
| `text` | 模型正文。终态由消息 `data.answerGeneration` 指向的那一块承担 | 字符串追加或 `{replace}` |
| `reasoning` | 执行过程 | 字符串追加或 `{replace}` |
| `thinking` | 模型思考 | 字符串追加或 `{replace}` |
| `tool` | 工具调用与结果摘要 | 结构化合并；input/output delta 追加 |
| `skill` | Skill 生命周期 | 浅合并结构化字段 |
| `context` | 当前会话上下文占用与压缩阈值 | 浅合并结构化字段；Chat 顶栏读取，消息树不渲染 |
| `approval` | HITL / AskUser | 浅合并；resume 后 close |
| `plan` | Plan Mode 和任务进度 | 浅合并结构化字段 |
| `artifact` | 产物摘要与下载引用 | 浅合并结构化字段 |
| `error` | 用户可见错误 | 浅合并结构化字段 |
| `subagent` | 子 Agent 生命周期 | 浅合并；内部活动使用子 Block |
| `team` | TeamRun 根状态与统计 | 浅合并结构化字段 |
| `team-member` | 临时 Member 身份与状态 | 浅合并结构化字段 |
| `team-task` | Team Task 生命周期 | 浅合并结构化字段 |
| `team-attempt` | Member 对 Team Task 的单次执行记录 | 浅合并结构化字段 |
| `team-activity` | Member 输出、思考与恢复过程 | `delta` 追加 content，其他字段浅合并 |
| `team-mail` | 单条 Mailbox 消息与 delivery 状态 | 浅合并结构化字段 |

新增展示能力只能新增一个后端 per-kind projector 和一个前端 Block module。不得增加 RuntimeFrame op、顶层 SSE 事件、公共 reducer 分支或 payload 反解析。

### 权限 V2 的 approval/tool 扩展（data.version=2）

权限决策仍进入既有 Domain Event → ProjectionRunner → approval/tool projector，不新增顶层事件或 Frame op。
审批模式来自 session 的下一轮选择，发送时冻结为 Run 的 `approvalMode`（`manual | model_review | full_access`）；
更改下一轮选择不修改当前 Run 或已存在审批。AskUser 保持下文的业务回答合同，不纳入工具权限批准。

`approval.data` 在工具权限批准时可增加以下版本化字段；未携带的历史数据按原 reader 展示：

```ts
type PermissionApprovalDataV2 = {
  version: 2;
  approvalMode: 'manual' | 'model_review' | 'full_access';
  actorId: string;
  decisions: Array<{
    decisionId: string;
    approvalId: string;
    callId: string;
    actionFingerprint: string;
    toolName: string;
    effect: 'read_only' | 'mutation' | 'outbound' | 'orchestration' | 'unknown' | 'output';
    targets: string[];
    summary: string;
    diffPreview?: string;
    outboundPreview?: string;
    source: 'readonly' | 'manual' | 'model' | 'model_error' | 'boundary' | 'full_access';
    reasonCode: string;
    reason: string;
    status: 'pending' | 'approved' | 'rejected' | 'cancelled' | 'expired' | 'interrupted';
    expiresAt: string;
  }>;
};
```

`decisions` 仅列出这张卡实际覆盖的有限调用。命令只能提交这些 decision 的批准/拒绝，不能提交替换参数，
不能生成或记住整工具规则。按钮文案为“允许这一次”“拒绝”；旧 `rememberApproval` 不扩大批准范围。
审批归属由服务器复核 tenant/workspace/project/session/run/actor；历史或失去执行现场的记录不可继续。

`tool.data.authorization` 可携带 `version:2`、`decisionId`、`actionFingerprint`、`approvalMode`、`actorId`、
`source`、`verdict`、`reasonCode`、`reason`，及可选 `executionId`、`executionStatus`。
执行状态为 `reserved | running | succeeded | failed | unknown`，无执行记录表示未启动；
`allow` 和人工批准都不代表执行成功。拒绝/边界失败必须与实际执行结果分开。
模型拒绝可提供 `reviewable:true`，人工复核必须显式创建新尝试，不能复活已终止调用。

命令响应仍只表示命令接收状态；审批结果、取消和执行终态必须从后端事实经原 stream seq 回流。
普通 Block 数据不携带 secret、Cookie、Authorization、受限原文或模型思维链。

工具普通入参（路径、命令、查询、正文及数值选项）必须在 Chat 和 Trace 可查看，不得全部替换为占位符。
权限 V2 可暂缓发送不完整的参数片段；收到 `TOOL_CALL_END` 后，必须将累计完整 JSON 逐字段隐藏凭据，
通过 `tool.call.updated` Domain Event 更新现有 tool Block 的 `inputPreview`，不新增 SSE 或 Frame op。
Trace 和历史回放使用同一份脱敏参数。`inputPreview` 更新是替换，不能把完整参数重复追加到流式片段后。
Team 的 `member.tool.input` 领域事件采用同样语义；仅更新工具入参，不关闭工具或改变执行状态。

`kind=context` 是 detached Block：消息树不渲染，Chat 顶栏读取当前会话最近一次模型调用的占用。同一 Run 只有一个 `context:{runId}` Block，后续 `MODEL_CALL_START/END` 浅合并更新。`data` 固定包含：

```ts
type ContextBlockData = {
  systemPromptChars?: number;
  summaryChars?: number;
  contextChars?: number;
  contextMessages?: number;
  toolResultBlocks?: number;
  toolCount?: number;
  contextWindow?: number;
  triggerRatio?: number;
  triggerTokens?: number;
  estimatedTokens?: number;
  compressed?: 0 | 1;
  overTrigger?: 0 | 1;
  inputTokens?: number;
  outputTokens?: number;
  estimateMethod?: 'bytes/4';
  estimateErrorPct?: number;
  promptSections?: Record<string, number>;
  toolSchemaChars?: number;
  dataBlocks?: number;
  compressionCount?: number;
  stubbedToolResults?: number;
  droppedImages?: number;
  hygieneSavedChars?: number;
};
```

- `estimatedTokens` 与 AgentScope `ChatModelBase.count_tokens` 同源：system prompt + summary + context 文本 + 工具 schema JSON 的 utf-8 字节 / 4，每个 DataBlock 固定 2000；不额外调用 tokenizer。`inputTokens` 在模型调用结束后为供应商回报，顶栏优先显示它；`estimateErrorPct = (estimated - input) / input * 100`，正值表示估算偏高（压缩会提前触发）。
- `promptSections` 是 system prompt 各段字符数（`agent` / `project` / `workspace` / `runtime` / `skills` / `route` / `skillRun` / `memory` / `clarification` / `finalAnswer`），用于定位膨胀来源。
- `compressed=1` 表示 AgentScope `state.summary` 已有压缩摘要；`compressionCount` 是本会话真正压缩的累计次数（每次同时写入 `CONTEXT_COMPRESSED` trace 事件）。
- `stubbedToolResults` / `droppedImages` / `hygieneSavedChars` 是一级无模型整理（`ContextHygieneMiddleware`）累计：早于最近 2 轮、超过 600 字的工具输出被换成一行存根，旧图片换成文字占位。
- `overTrigger=1` 表示占用已达到 `ContextConfig.trigger_ratio`（当前窗口的 75%）阈值。
- `contextWindow` 来自模型 `context_size`：`models.json` 的 `contextSize` 优先，否则按 adapter 默认（deepseek / openai / moonshot 128k、dashscope 131072、anthropic 200k、gemini 1M、ollama 32k）；降级链取链上最小值。
- 可选 `session` 描述本 Run 冻结的 Session 资源，不新增顶层 SSE 事件，也不进入消息树。只允许这些字段：`sessionId`、`snapshotId`、`agentId`、`directoryGrantVersion`、`resourceGrantVersion`、`workingDirectory`、`skills`、`tools`。`skills` 与 `tools` 是资源 ID 列表。不得携带 Skill 正文、secret、token 或模型 Key。刷新后的 Snapshot 与实时 Frame 回放必须得到同一份 `session`。

```ts
session?: {
  sessionId: string;
  snapshotId: string;
  agentId: string;
  directoryGrantVersion?: number;
  resourceGrantVersion?: number;
  workingDirectory?: string;
  skills: string[];
  tools: string[];
};
```

最终回答的 `text` Block 必须保留上游模型的增量节奏。Provider thinking 只投影到 `thinking`
Block。思考块只表示模型思考，不能当成终态信号，也不能用 thinking 到普通文本的边界推断最终回答已经开始。

一次模型生成对应一个 `text` Block。普通 `TEXT_BLOCK_DELTA` 从第一个 token 起就投影为 `FINAL_ANSWER_DELTA`，
payload 带 `generation`（从 1 递增的正整数）。同一 Run 里人工确认恢复后续用暂停前的编号，不从 1 重新开始。
`generation > 0` 时 Block id 为 `text:{runId}:{generation}`；
缺省、非整数或 `<= 0` 时仍使用 `text:{runId}`，供 Team 综合和计划审核摘要沿用单块正文。
正文不积压、不改判成 `reasoning`，也不存在按字数提前晋升。同一次生成里若出现工具调用，该 Block 保持为过程正文，循环继续。

终态信号只在 `REPLY_END.finished_reason=completed`、这次生成没有工具调用、且正文非空时发出。
投影为消息 Block 的 `data.answerGeneration`，值是对应 `text` Block 的 id。这不是新的 SSE op：
前端只看到既有 `delta` Frame 在消息上补上该字段。`AGENT_FINAL_ANSWER_STARTED.reason=reply_completed`，
payload 为 `{generation, direct: true}`，不携带工具名。已经流过的正文不再整段重发 `FINAL_ANSWER_DELTA`。

中断、失败、等待 AskUser，以及这次生成带了工具调用时，不写 `answerGeneration`。收尾正文为空、
但已有工具、推理、思考或达到轮次上限时，再要一轮 `tool_choice=none` 的纯文本；这一轮仍按上面的生成规则投影。
仍然为空时沿用缺答案或超轮次失败。

计划审核摘要和 Team 综合继续直接流最终正文。它们不带 `generation`，结束时把 `text:{runId}` 写入 `answerGeneration`。
旧快照没有该字段时，展示层把已完成消息里的 `kind=text` 当作当年的最终答案；不回写历史库。

支持增量结果的长耗时工具必须在执行期间持续发送结果 delta。内置 Bash 在本地执行时必须按
stdout/stderr 可读节奏产生 `TOOL_RESULT_TEXT_DELTA`，使对应 `tool` Block 的 `outputPreview`
实时追加；进程退出后再发送 `TOOL_RESULT_END`。取消 Run 必须终止仍在执行的本地子进程，并先
投影所有开放 Block 的 `cancelled` close Frame 和 `done`，随后才关闭 subscriber。前端发起取消后
不得主动中断当前订阅，必须消费该 Run 的 cancel 终态 Frame；只有取消请求失败时才允许本地兜底
中断。

### 6.1 AskUser 交互投影

AskUser 不新增 Block kind。后端以 `phase=open` 的 `kind=approval` Block 表达待回答请求，`data.type=REQUIRE_EXTERNAL_EXECUTION`，`data.toolCalls` 中包含 `rawName/name=AskUser` 的工具调用。AskUser 工具输入中的 `questions` 固定为：

```ts
type AskUserQuestion = {
  id: string;
  type: "choice" | "multi_choice" | "text";
  label: string;
  options?: string[];
  placeholder?: string;
  required: boolean;
};
```

- `required` 是每个问题的必传布尔字段，不允许通过缺省值推断必填。
- 前端只校验 `required === true` 的问题；空的选填答案不进入 `externalResults` 模型文本。
- Chat 从当前 DisplaySnapshot 中按 `order` 派生所有开放 AskUser，底部输入区显示其中一个并提供待回答数量；不建立第二份 interaction store。
- 开放 AskUser 在消息过程区只显示等待状态，实际表单只出现在底部输入区，避免同一命令存在两个提交入口。
- `POST /api/chat/resume/start` 成功不代表 UI 已完成；只有原 approval Block 收到 close Frame 后才移出待回答队列并恢复普通 Composer。
- 刷新或重连后由 Snapshot 恢复待回答队列。未提交草稿不是协议状态，不写入 Block 或 Domain Event。

### 6.2 Plan 审核与执行投影

Plan Mode 是 ReAct 内的持久化领域状态，不是独立 Runtime。每次计划起草、模型修订和确认执行都可以启动新的 Chat Run，但必须复用同一个 `planId`；新 Run 的第一个 Plan 领域事件必须携带当前完整 Plan 数据，使该 Run 的 `kind=plan` Block 能独立恢复，不依赖上一轮 Snapshot 或进程内 Agent。

Plan middleware 和控制工具只在请求显式携带 `plan_requested=true`，或携带 `planId + planAction` 继续既有 Plan 时装载。普通单 Agent Run 不暴露 Plan 控制工具，也不因为 Plan 能力改变 Agent cache key。

`kind=plan` 的 `data` 固定包含：

```ts
type PlanBlockData = {
  planId: string;
  title: string;
  goal: string;
  status:
    | "drafting"
    | "review_waiting"
    | "revising"
    | "approved"
    | "executing"
    | "replanning"
    | "completed"
    | "failed"
    | "cancelled";
  revision: number;
  approvedRevision?: number;
  markdown: string;
  steps: Array<{
    id: string;
    stepIndex: number;
    title: string;
    description: string;
    status: "pending" | "running" | "completed" | "failed" | "skipped";
    evidence?: string;
    result?: string;
  }>;
  feedback?: string;
  executionRunId?: string;
  updatedAt: string;
};
```

稳定领域事件语义如下，仍统一投影为 `open | delta | close`，不增加 SSE 事件名或 RuntimeFrame op：

- `PLAN_CREATED` / `PLAN_CONTENT_REPLACED` / `PLAN_STEPS_REPLACED`：起草并替换当前未提交 revision。
- `PLAN_REVIEW_REQUESTED`：完整 revision 已保存，状态进入 `review_waiting`；前端底部普通 Composer 替换为 Plan 审核 Composer。

### 6.3 Subagent attached task 投影

Subagent 保持 Markdown + YAML frontmatter 定义，并由主 Agent 在 ReAct 内调用
`DelegateTask` 创建。`DelegateTask` 的在线语义是 fork attached task：调用成功后立即返回
`invocationId`，不等待 child 结束，也不把 child 中间过程持续注入父 Agent 上下文。

每个 `kind=subagent` Block 对应一个稳定 invocation，`data` 固定包含：

```ts
type SubagentBlockData = {
  invocationId: string;
  subagentName: string;
  taskPreview: string;
  status:
    | "queued"
    | "running"
    | "waiting"
    | "completed"
    | "failed"
    | "cancelled"
    | "interrupted";
  deliveryStatus: "none" | "pending" | "delivered" | "consumed";
  resultRef?: string;
  resultPreview?: string;
  error?: string;
  durationMs?: number;
  updatedAt?: string;
};
```

- child 的模型 delta、工具输入输出和完整 transcript 不进入父 Agent context；Chat Block
  只接收任务状态、当前轻量摘要和最终消费状态。
- child 终态结果先原子写入任务目录的 `result.md`，再把状态改为终态。父 Agent
  只能通过 Subagent 结果读取工具消费该文件。
- 读取结果后 delivery 进入 `delivered`；父 Agent 基于结果继续至少一次 acting 后进入
  `consumed`。
- 父 Run 的 completion barrier 要求所有 attached child 已进入终态且 delivery 为
  `consumed`。模型忘记 wait/read 时不得正常 close Run。
- 多个 child 可并行；Run 内所有 Subagent Block Frame 仍使用同一全局 `seq`。
- 首期不支持 detached 和嵌套 Subagent，不从 frontmatter 推断前台/后台执行策略。
- `PLAN_REVISION_REQUESTED`：用户提交自然语言修改意见，状态进入 `revising`，由模型生成完整新 revision。
- `PLAN_APPROVED` / `PLAN_EXECUTION_STARTED`：用户确认当前 revision 后进入执行阶段。
- `PLAN_STEP_UPDATED`：执行步骤状态、evidence 或 result 更新，前端实时归并到同一个 Plan Block。
- `PLAN_REPLAN_REQUESTED`：执行中需要重新规划，状态进入 `replanning`，完成新 revision 后再次进入 `review_waiting`。
- 起草 Run 必须以 `PLAN_REVIEW_REQUESTED` 收口，执行 Run 必须以 `PLAN_COMPLETED | PLAN_FAILED` 收口；模型直接结束但未调用对应控制工具时，后端必须把该 Run 判为失败，不能留下无入口的 `drafting | revising | replanning | executing`。
- 初次起草或执行失败时 Plan 进入 `failed` 并关闭 Block；revision/replan 尝试失败时回退到已有 revision 的 `review_waiting`，继续保留确认、再次修改和取消入口，不创建伪 revision。
- `PLAN_COMPLETED` / `PLAN_FAILED` / `PLAN_CANCELLED`：Plan 终态并关闭 Plan Block。

Plan 审核命令只提交 `planId`、`action=revise|approve|cancel` 和可选自然语言 `feedback`。HTTP 响应只返回新 Run 元数据，不能直接修改 UI；审核态、revision 和执行进度必须由新 Run 的 Plan Block 回流。`revise` 不能接收前端直接编辑后的 Markdown 或步骤，完整新 revision 必须由模型通过 Plan 工具生成。

## 7. 后端边界

```text
AgentScope/provider
  -> SourceEvent
  -> ChatProjectionNormalizer
  -> DomainEventRepository
  -> ProjectionRunner
  -> per-kind projector
  -> BlockLifecycleCoordinator
  -> ProjectionStreamStore
  -> RUNTIME_EVENT: RuntimeFrame
```

- SourceEvent 是进程内输入，不是前端协议，也不允许先序列化为 SSE 再反解析。
- Domain Event 是业务事实；Chat Trace Observation 是按 Run 的诊断步骤（见 `docs/trace-run-observations.md`）；RuntimeFrame 是 UI projection，三者不得混用。
- 同一 Run 由唯一 `stream_ordinal` 排序，ProjectionRunner 使用 lease/fencing 串行投影。
- projector 不直接写 SSE，handler 不直接修改 DisplaySnapshot。
- Snapshot checkpoint、tail Frame、消费水位和 seq 由 projection store 原子维护。
- `run.completed` 必须通过 completion barrier；失败或取消时按叶子到根关闭未完成 Block，最后发送 done。
- Block 只保存白名单摘要。完整工具 observation、模型原始响应和完整 Subagent transcript 留在 Trace/领域存储。

## 8. 前端边界

```text
RUNTIME_EVENT
  -> decodeRuntimeFrame
  -> streamAdapter
  -> blockTreeReducer
  -> registry[kind].reduce
  -> registry[kind].renderer
```

- 通用层只理解 `seq`、Block identity、父子关系和生命周期。
- renderer 不修改 Block Tree，不从工具文本或 JSON 字符串恢复领域状态。
- 每个 kind 严格校验 `dataVersion`、initial data 和 patch。
- Chat 与 Sandbox 使用同一 Runtime Block Tree 渲染入口。

## 9. 已删除契约

下列名称只能出现在破坏性 reset、历史设计说明或删除验收中，不能重新进入运行时：

- `RunState` / `RUN_STATE` / `RUN_STATE_PATCH`
- `DisplayEvent` 与固定 event/block 映射
- `event_hub`、`runStateStreamAdapter`、`displayEventReducer`
- mask registry、动态字段路径、`set/append/remove/clear`
- 顶层业务 `TEAM_EVENT`
- 旧 Snapshot reader、迁移 adapter、双读或双写 fallback

## 10. 变更规则

修改 RuntimeFrame、DisplaySnapshot、核心生命周期或现有 kind 语义前，必须先更新本文，再同步：

1. 后端 Pydantic schema 与 projector 测试。
2. 前端 TypeScript decoder、kind module 与 reducer 测试/构建。
3. Snapshot、tail replay、HITL resume 和 terminal done 回归。

新功能如果只需要新增 `kind`，不得修改本协议公共结构。

## 11. Team 投影契约

成员/Leader 的权限等待先写 Team outbox `member.permission.required/resolved`，再投影为现有
`approval` Block，附原 Chat 的 session/run、actor、task 和精确 decision；不新增顶层 SSE。
原 Chat 的恢复命令只唤醒对应 live waiter，其他成员继续执行；未解决审批阻挡综合和完成。
人工等待暂停单任务活动执行预算，恢复后只使用剩余预算；取消清理全部 waiter 和待处理交互。

Team 是 ReAct 的 `executor.kind=team` 执行能力，不新增 SSE 事件名。TeamStore 的 `events.jsonl` 是后端领域事实与 outbox，`TEAM_DOMAIN_EVENT` 只是进程内源信号；浏览器只能收到由 projector 生成的 `RUNTIME_EVENT`。

稳定 Block identity：

```text
team:{teamRunId}
team-member:{teamRunId}:{memberId}
team-task:{teamRunId}:{taskId}
team-attempt:{teamRunId}:{attemptId}
team-activity:{teamRunId}:{activityId}
team-mail:{teamRunId}:{messageId}
tool:team:{teamRunId}:{toolCallId}
```

`team`、`team-member`、`team-task`、`team-attempt`、`team-activity` 和 `team-mail` 都是 `message:{runId}` 的后代。Task 固定直属 Team，Attempt 固定直属对应 Task；活动与工具继续直属 Task，使用 `attemptId` 关联本次执行；没有 task 的 Leader 协调活动直属 Team。parent 一经 open 不得因 owner、状态或返工次数变化而修改。

`attempt.created/started/submitted/accepted/rejected/failed/cancelled` 领域事实投影到 `team-attempt` Block。同一 Task 可以依次拥有多个 Attempt Block；Attempt 进入终态后不可 reopen。Task 的 `currentAttemptId`、`acceptedAttemptId` 和 `attemptCount` 通过 `team-task` Block 更新。Mailbox 的 `dispatchMode=context|continue_task|new_task` 必须保留在 `team-mail.data`，但命令 HTTP response 仍不能直接修改 Block Tree。

TeamStore event 不得携带完整 snapshot。每条事件 payload 必须自包含 projector 所需白名单字段。Team terminal 投影必须先关闭开放的 tool/activity，再关闭 task/member，最后关闭 team；只有随后到达的 Run terminal 才能关闭 message 和发送 done。`team_submit_result` 的真实顺序允许 `task.completed` 先于对应 `member.tool.completed`：projector 必须先更新 task 数据，保留 task 为 open，待最后一个已启动 tool 关闭后再关闭 task。task 进入领域终态后到达的新 activity 或新 tool 不再创建展示 Block，已启动 tool 的终态仍必须消费并投影。同一 `toolCallId` 可能因外部执行拒绝连续产生 `member.tool.denied` 和 `member.tool.failed`，Tool 首个终态关闭 Block 后，后续重复终态必须按幂等事实消费，不得重新 open。

`team-activity.data.content` 最多保留最近 12000 字符，`team-mail.data.content` 最多 4000 字符，Task brief/result 分别最多 2000/4000 字符；完整内容留在 Team 文件状态和 Trace，不进入 DisplaySnapshot。

前端 Team Console 只能从 DisplaySnapshot Block 子树派生。Mailbox 命令的 HTTP response 不得直接修改 Block Tree，必须等待对应 `team-mail` Frame 回流。旧顶层 `TEAM_EVENT`、`teamSnapshot`、`TeamConsoleState`、`teamEventReducer` 和历史 reader 均不兼容、不迁移、不读取。

Team 综合阶段必须把模型的 `TEXT_BLOCK_DELTA` 同步投影到当前消息的 `kind=text` Block，逐段发送 `delta` Frame；综合完成后允许发送一次 `replace` 作为最终一致性校正，但不得把它作为首个回答内容。这样成员执行过程和最终回答都沿同一条可恢复的 seq 流实时展示。

Chat 中的 `kind=team` renderer 必须直接从同一 DisplaySnapshot 的 Team 子树派生实时成员、任务和最新活动摘要。运行中的成员、任务和阶段变化应在 Frame 到达后立即更新，并使用局部状态动画表达进行中/完成/失败；完整过程仍由 Team Console 下钻查看。renderer 不得缓存第二份 Team 状态，也不得等待 Team 终态后批量构造组件。

## 模型配置 V1 补充（2026-09-07）

- Chat 请求使用 `modelConfigId`，旧 `model` 请求字段不再接受。Agent 不携带模型选择。
- `POST /api/chat/send/start` 与 Run REST 元数据增加 `modelConfigId`、`providerId`、`modelName`；持久化只保留无密钥的配置摘要。
- `RUN_STARTED` 的 trace payload 增加 `modelConfigId` / `providerId`，不新增 SSE 类型或 RuntimeFrame op。
- 会话返回 `modelConfigId`；`PATCH /api/chat/sessions/{id}/model?projectId=...` 保存下一轮选择，活动运行期间返回 409。
- 一次 Run 固定完整模型快照。审批恢复、Team、Subagent 和后台后处理沿用快照；配置修改只影响新 Run。
- 所有公开响应、Trace 和 RuntimeFrame 禁止携带 `apiKey`。

### 文件 Skill 身份（2026-10）

既有 `SKILL_CONTEXT` / `SKILL_END` 经原有 Block 投影传递，禁止新增顶层事件。
Skill 条目的 `id` 表示具体文件包稳定身份，`name` 仅供显示；新增可选 `loadName`、
`sourcePath`、`sourceScope`、`contentHash` 表示 viewer 实际读取的限定加载名、来源与原始正文版本。
旧历史缺省这些字段仍可回放。生命周期必须按 `loadName` 唯一匹配本轮目录，不能按同名第一项记账。
加载失败不得产生成功激活。会话激活记录保存上述身份和 hash；下一轮撤销停用、解绑、缺失或过期正文时，
只替换受控 Skill 工具块/HintBlock，用户消息和历史审计保持原样。
