# Chat ReAct

> 状态：当前在线架构，2026-09-20。本文只讲 Chat 单 Agent 的 ReAct 循环：一次发送如何变成 Think → Act → Observe，以及暂停与收口。
> 不讲 Plan Mode、Skill、Team、Subagent、记忆、定时任务、群聊。那些是挂在 ReAct 上的其它能力，各有专题。
> SSE 字段以 `docs/chat-sse-event-contract.md` 为准。总览入口见 `docs/architecture-overview.md`。

## 1. ReAct 在这里是什么

ReAct 是 Chat 当前唯一的 architecture：`runtime.kind=react`。

它不是应用层手写的 `while`。循环在 AgentScope `Agent.reply_stream()` 里：模型推理（Think），决定是否调用工具（Act），把工具结果写回上下文（Observe），直到给出最终答案、向用户提问、等待权限，或达到迭代上限。

应用层做三件事：把本轮请求组装成一个 Agent；消费 SDK 事件并投影给前端；在循环外处理暂停、恢复和收口。

旧独立 Plan Runtime、旧独立 Subagent Runtime 已删除。不要把「只有 react」理解成系统没有规划或委托能力——那些是别的专题，本文不展开。

## 2. 一次发送怎么进到循环

```text
POST /api/chat/send 或 /send/start
        │
        ▼
按 project_id 解析 RuntimeContext     Workspace ← Project ← Session ← Run
        │
        ▼
后台 Run 启动                         执行与 SSE 订阅解耦
        │
        ▼
组装 ChatRunContext                   Readiness → 路由 → prompt / 工具 / Agent 规格
        │
        ▼
启动校验                              空消息 / 无 API Key / 模型无图片能力
        │
        ▼
execute_chat_runtime                  当前只分发到 react
        │
        ▼
get_or_create_agent                   复用或新建 AgentScope Agent
        │
        ▼
agent.reply_stream(UserMsg)           ReAct 循环本体
        │
        ▼
leader_stream 消费事件
        │
        ├─ 暂停：AskUser / 权限确认 → 同一 Run resume
        └─ 结束：落 assistant 消息，Run completed 或 failed
```

两条 HTTP 入口共用 `start_chat_run()`：

- `POST /api/chat/send`：直接订阅后台 Run 的 SSE。
- `POST /api/chat/send/start`：只返回 Run 元数据，前端再订 `/api/chat/runs/{run_id}/stream`。

handler 在进后台前只做机械校验：Session 必须属于该 Project；模型绑定来自请求或 Session；带图时模型必须支持图片；上传路径必须落在 Project session 目录。

后台 Run 产出进程内 `SourceEvent`，再投影为 `RUNTIME_EVENT: RuntimeFrame`。前端主界面只消费 Frame 与 DisplaySnapshot V4，不直接读 handler 事件名。

## 3. 进循环之前

权威文件：`run_context.py`、`readiness_gate.py`、`intent_router.py`、`startup_checks.py`。

### 3.1 Readiness

只回答「当前输入能不能启动 Runtime」。

- 空消息：`ready=false`，不创建主 Agent。
- 其它情况默认 `ready=true`。规则若觉得像执行型任务但缺目标，仍放行，把缺口交给循环内的 `AskUser`。
- 只有规则认为「模糊执行型」时才可能再调一次 ReadinessAgent 补 reason。规则已经够清楚时不再调模型。
- 模型就算返回 `ready=false`，sanitize 也会改回 `true`。业务缺口不在闸门拦截。

Readiness 不选择 architecture、不选择工具范围。结果写入 `READINESS_GATE` Trace。

### 3.2 路由

Chat 有两层决策，职责不能混：

| 层 | 字段 | 谁决定 | 本文范围 |
| --- | --- | --- | --- |
| architecture | `runtime.kind` | 前置路由 | 只有 `react` |
| executor | `executor.kind` | 请求里的 `mode` | 本文只讲 `mode=agent` → `single_agent` |

当前 `INTENT_CATALOG` 只有 `react`，不必为路由再调一次模型。`clarification` 不是 `runtime.kind`，也不是新的运行时分支。

### 3.3 本轮上下文

`ChatRunContext` 是执行前的只读快照。和循环直接相关的部分：

| 字段 | 作用 |
| --- | --- |
| `runtime_context` | Workspace / Project / Session / Run 归属 |
| `runtime_model_config` | 主模型、fallback、`max_react_iters`（默认 40，硬顶 512） |
| `system_prompt` | 跨轮稳定指令。不含时钟、不含会话历史、不含本轮附件 |
| `session_history` | 已落库的 user / assistant 正文，只供进程内没有 Agent 上下文时重建 |
| `guardrails` | 权限、工作目录、超时；filesystem 再与 Project root 取交集 |
| `runtime_tool_scope` | 是否打开 Bash / HTTP / WebFetch / MCP / 知识库 |
| `session_workspace_dir` | 本轮 cwd，默认 Project 下的 session 目录 |

system prompt 里和 ReAct 行为有关的段：

- Agent 正文（catalog 文件为准）
- Project / workspace 说明
- Runtime：时间在对话里的 `<system-reminder>`，prompt 本身不写时钟
- Clarification：缺信息必须调 `AskUser`，禁止纯文本追问
- Final Answer：能直接回答就写正文；用过工具或 AskUser 之后，收尾也直接写正文

本轮用户消息 = 用户原文 + 附件说明（`<attached_files>`）。附件进 `state.context`，不进 system prompt。

启动校验拦截空消息、未配置 API Key、模型无图片能力。失败走 `emit_failed_startup_run`，不进入循环。

## 4. Agent 从哪来

权威文件：`runtime_dispatcher.py`、`react_runtime.py`、`runtime_builder.py`、`agents/factory.py`、`agents/session_context.py`。

`execute_chat_runtime()` 只按 `runtime.kind` 分发，当前只进 `execute_react_runtime()`。本文只覆盖随后的单 Agent 路径。

`get_or_create_agent()` 是唯一构造入口：

- 同一持久 Agent + 同一 Session，多轮默认复用实例。在线对话记忆是 AgentScope `state.context`，不要把历史扁平拼进 system prompt。
- 缓存键包含模型、thinking、工具范围、权限等稳定配置，**不含 prompt 正文**。复用时刷新 `_system_prompt`。
- 配置变化会换实例，旧实例的 `context` / `summary` 搬到新实例。
- 缓存 LRU 48、闲置 6 小时。正在执行或等待用户（AskUser / 审批）的实例不淘汰。
- 用户没回答等待、直接发了新消息：工厂先补 `ToolResult` 关掉未完成的工具调用，再复用。
- 进程重启或实例被淘汰：用 `chat_messages` 重建精简历史，再从 `session_context_state` 恢复压缩摘要。

AgentScope 侧与循环相关的配置：

- `ReActConfig.max_iters`：安全底线 512。长任务不会因为 40 或 128 这种小上限被打断。死循环由 `ToolLoopGuardMiddleware` 按工具指纹判断：同一参数反复失败、只读结果不变、连续相同调用，或 2 到 4 步来回打转。Chat、Team、群聊达到阈值都会停掉这一轮，再走不调用工具的总结。
- `ContextConfig`：占用到窗口 75% 触发压缩，预留 20%，单条工具结果上限 4000 字
- 时间用 HintBlock 注入，不改写 system prompt
- 工作目录是 Project session 目录，避免工具扫到用户主目录

执行期间 `mark_agent_busy`，结束后 `mark_agent_idle`，并落 `session_context_state`。

## 5. 循环本体

权威文件：`leader_stream.py`、`leader_execution.py`、`agents/factory.py`、`tools/ask_user.py`、`answer_generation.py`。

应用层不实现 Think / Act 调度。`stream_leader_agent_reply()` 消费 `reply_stream()`，把 SDK 事件变成 Trace、前端投影和暂停点。

### 5.1 一圈

```text
REPLY_START
    MODEL_CALL_START
        模型输出：思考 / 过程文本 / 工具参数
    MODEL_CALL_END
    （可选）TOOL_CALL_*
        权限检查
        通过 → 执行工具 → TOOL_RESULT_*
        需要人 → REQUIRE_USER_CONFIRM 或 REQUIRE_EXTERNAL_EXECUTION，循环暂停
    若仍未结束 → 下一圈 MODEL_CALL
REPLY_END.finished_reason
```

`finished_reason` 必须原样进入 Runtime payload。`completed` 才能走成功收口。`interrupted`、`error`、`exceed_max_iters` 不得当成成功。

达到轮次预算时 SDK 发 `EXCEED_MAX_ITERS`。若还能写出总结，这一轮按总结收口；写不出来才返回「已达到最大 ReAct 迭代次数」。重复工具调用不会等到这个预算才停。

用户点停止时，应用层取消是 Run 终态事实源。已经 cancelled 的 Run，不要把 SDK 的 `interrupted` 再写成 failed。

### 5.2 默认可调用的工具

普通单 Agent 默认工具集（`include_default_tools=True`）：

| 工具 | 在循环里的角色 |
| --- | --- |
| `ScopedRead` / `Grep` / `Glob` | 读工作区。cwd 为 session 目录，上限为 Project ∩ Agent filesystem |
| `ScopedWrite` / `ScopedEdit` | 写工作区。只读权限不注入 |
| `GuardedBash` | 执行命令。黑名单 + 超时；是否注入还看 ToolScope |
| `HttpRequest` / `WebFetch` | 网络。由本轮 ToolScope 打开。WebFetch 不是搜索引擎 |
| `AskUser` | 外部工具。暂停循环，等用户结构化回答 |

写工具会走权限策略。需要确认时 SDK 发 `REQUIRE_USER_CONFIRM`。`AskUser` 发 `REQUIRE_EXTERNAL_EXECUTION`。同一 `reply_id` 里可能有多个 REQUIRE 事件，必须排空整批再交给前端，否则 resume 会带回 Agent 并未等待的 `tool_call_id`。

ToolScope 在路由之后解析，不属于 IntentRoute。Bash 默认保留。HTTP / Web 只在消息像接口或网页任务时打开。

### 5.3 过程文本和最终答案

模型在一圈里可能先写一两句过程说明，再调工具；也可能全程不调工具、直接回答。

一次 `MODEL_CALL_START` 到下一次模型调用之前，是同一次生成。这次生成的 `TEXT_BLOCK_DELTA` 从第一个 token 起投影为带 `generation` 的 `text` 块。同一次生成里如果出现工具调用，这段正文留在过程里，循环继续。

`REPLY_END.finished_reason=completed`、这次生成没有工具调用、且正文非空时，消息 `data.answerGeneration` 指向该 `text` 块。中断、失败、等待 AskUser，以及这次生成带了工具调用时，不写这个字段。

收尾正文为空、但已有工具、推理、思考或达到轮次上限时，`final_answer_recovery.py` 再要一轮 `tool_choice=none` 的纯文本，仍按同一次生成的规则收口。

思考增量走 `THINKING_BLOCK_DELTA`，累加到 `assistant_thinking`。思考不是终态信号。

### 5.4 AskUser

缺信息时模型应调 `AskUser`，不要把问题写在最终答案里。

- 最多 3 题；题型 `choice` / `multi_choice` / `text`
- 循环暂停，Run 进入 `waiting_user`
- 前端从 Snapshot 里 `kind=approval` 且工具名为 `AskUser` 派生表单，只出现在底部输入区
- 用户提交走 `POST /api/chat/resume/start`，**同一 Run 继续**，不是新发送

若模型把追问写成纯文本，leader_stream 在 `REPLY_END` 后做 clarification 兜底。这仍是 ReAct 暂停，不是新 architecture。

### 5.5 中途补充

用户在循环还没结束时往当前 Run 补一句话，由 `RunSteerMiddleware` 在下一圈 reasoning 前写入 `state.context`。不要插在正在等待工具结果的位置，以免打断 `assistant(tool_calls) → tool` 的相邻顺序。

## 6. 上下文如何撑过多圈

循环会把工具结果越积越多。治理分三层，都挂在同一个 Agent 上：

1. **窗口按模型给**。用 `models.json` 的 `contextSize`，没有则按供应商默认。不要退回 AgentScope 的 32768。
2. **一级无模型整理**。新一圈开始前，把早于最近 2 轮、过长的工具输出换成一行存根。不拆 tool_call / tool_result 配对。
3. **二级压缩**。占用超过窗口 75% 时由 AgentScope 摘要。应用层只在摘要或上下文真的变短时记一条 `CONTEXT_COMPRESSED`。

`CONTEXT_USAGE.estimatedTokens` 必须与 AgentScope `count_tokens` 同源，不要再做第二套字符折算。

时间、上下文长度以 HintBlock 追加进 messages，formatter 送模时拆成 user 消息。system prompt 保持稳定，供应商前缀 cache 才能命中。

## 7. 暂停、恢复、收口

权威文件：`handlers/resume.py`、`hitl.py`、`completion.py`、`leader_execution.py`。

### 7.1 暂停

| 原因 | 信号 | Run 状态 | 之后 |
| --- | --- | --- | --- |
| 权限确认 | `REQUIRE_USER_CONFIRM` | 等待用户 | `/api/chat/resume/start`，同一 Run |
| AskUser | `REQUIRE_EXTERNAL_EXECUTION` | `waiting_user` | 同上 |
| 用户停止 | 应用层 cancel | `cancelled` | 不能再 resume 成成功 |

暂停时落当前 assistant 草稿（已有文本、思考、工具调用），Agent 实例留在 pending 表，不被闲置淘汰。新的用户发送才是新 Run。

### 7.2 收口

`execute_and_finalize_leader_reply()` 保证：只要不是暂停，就只走 `completion.py`。

成功条件：`REPLY_END.finished_reason=completed`，且有最终答案文本。然后落 assistant 消息、本轮新文件产物、`RUN_COMPLETED`。

失败条件：没有最终答案、超过 max iters、`finished_reason` 不是 completed、HITL 被拒、创建或执行抛错。

## 8. 事件怎么分

ReAct 循环会碰到三类出口，不要写串：

| 通道 | 给谁 | 循环里对应什么 |
| --- | --- | --- |
| RuntimeFrame / DisplaySnapshot | Chat 主界面 | 文本、思考、工具、AskUser / 权限、产物 |
| `trace_observations` | Trace 页 | 一次模型调用一条 generation，一次工具一条 tool，AskUser / 权限一条 approval |
| `run_events` | 审计 | 压缩、失败原因等，不当时钟线展示 |

前端通用层只理解 Block 身份、父子和 `seq`。工具、文本、审批各自是 `Block.kind`。命令 API 的 HTTP 响应不是 UI 状态，展示变化必须等 Frame。

## 9. 文件地图

只列单 Agent ReAct 主路径：

| 文件 | 职责 |
| --- | --- |
| `handlers/send.py` | HTTP 入口，启动后台 Run |
| `handlers/resume.py` | AskUser / 权限恢复，同一 Run |
| `background_runs.py` | Run 生命周期，把 SourceEvent 送进投影 |
| `run_context.py` | 组装本轮快照 |
| `readiness_gate.py` | 能不能启动 |
| `intent_router.py` | `runtime.kind=react` |
| `startup_checks.py` | Key / 空消息 / 图片能力 |
| `runtime_dispatcher.py` | 按 architecture 分发 |
| `react_runtime.py` | 单 Agent 执行入口 |
| `runtime_builder.py` | 模型配置、缓存键、Agent 规格 |
| `tool_scope_resolver.py` | 本轮是否打开网络类工具 |
| `prompts.py` | AskUser / Final Answer 契约 |
| `context_middleware.py` | 工具输出整理与压缩观测 |
| `steer_middleware.py` | 中途补充 |
| `leader_stream.py` | 消费 `reply_stream` |
| `leader_execution.py` | 流消费 + 收口编排 |
| `final_answer_recovery.py` | 收尾正文为空时再要一轮纯文本 |
| `completion.py` | 成功 / 失败落库 |
| `projection/` | SourceEvent → RuntimeFrame |
| `agents/factory.py` | 构造 Agent 与默认工具 |
| `agents/session_context.py` | 实例间搬运上下文、用历史重建 |
| `tools/ask_user.py` | 暂停并提问 |
| `answer_generation.py` | 按模型生成累计正文，并在无工具收尾时发出终态信号 |

## 10. 红线

1. 不要再增加第二种 Chat architecture。新能力挂工具、middleware 或后处理，不要新的 `runtime.kind`。
2. 不要把 `clarification` 做成独立运行时。缺信息继续用 `AskUser`，文本兜底只是解析。
3. 不要在应用层再写一套 Think / Act 调度。循环留在 `reply_stream()`。
4. 不要把会话历史、时钟、附件写进 system prompt。
5. 不要把 prompt 正文放进 Agent 缓存键。
6. 不要把 `interrupted` / `error` / `exceed_max_iters` 映射成成功。
7. 不要为 token 再做第二套估算。`estimatedTokens` 与 AgentScope `count_tokens` 同源。
8. 不要用命令 API 的 HTTP 响应驱动 UI。AskUser 和权限的展示以 Frame 为准。
