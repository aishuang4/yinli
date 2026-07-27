# 通用事件流架构

> 状态：当前在线架构，2026-07-27；后续只按 `Block.kind` 扩展领域能力。
> 范围：Chat 实时展示、刷新恢复和历史回放使用的通用事件、投影、Block Tree 与前端消费链路。
> 领域边界：Plan、Team、Artifact、Subagent 维护自己的状态机，只通过 per-kind projector 接入本架构。
> 删除边界：不恢复旧 Runtime Event mapping、RunState projector、DisplayEvent reducer、旧 Snapshot 或兼容读取 fallback。

## 1. 最终决策

保留两个稳定产品边界：

- `RUNTIME_EVENT`：SSE 唯一应用事件名。
- `DisplaySnapshot`：刷新、断线恢复和历史回放使用的完整 Block Tree 状态。

删除以下目标设计：

- 删除 `mask registry`。
- 删除 `set / append / remove / clear` 通用字段更新协议。
- 删除应用层 heartbeat/reset 事件；heartbeat 使用 SSE comment，reset 直接发送新 Snapshot。
- 删除前端对 Run、Tool、Plan、Skill、Artifact、Subagent 等领域事件的公共分支。
- 不预先定义 Run/Project/Detail/Trace 四种 channel 类型。
- `RunState v2` 不再作为与 `DisplaySnapshot` 并列的 UI projection。

目标链路固定为：

```text
后端
Raw Event
  -> Normalizer
  -> Domain Event / Trace Event
  -> per-kind Projector
  -> open / delta / close Block Frame

前端
RuntimeFrame
  -> generic Block Tree reducer
  -> blockRegistry[kind].reduce
  -> blockRegistry[kind].renderer
  -> UI
```

核心原则：

1. 前端通用层只理解 Block identity、父子关系、生命周期和 `seq`。
2. Event 不决定 React 组件，`Block.kind` 决定 reducer、显示名称和 renderer。
3. `delta.patch` 的合并语义属于对应 kind，不属于公共协议。
4. Message 本身也是 Block；Tool、Plan、Approval、Artifact、Subagent 都是嵌套 Block。
5. 新增 Agent 展示能力不修改 RuntimeFrame、传输层、通用 reducer 或恢复逻辑。
6. 后端仍保留富 Domain Event 与 Trace；Block Frame 只是面向 UI 的轻量 Projection Event。
7. Snapshot、实时 Frame 和历史 replay 必须得到完全相同的 Block Tree。
8. 线上切换时直接删除旧链路，不双写、不保留 fallback；旧事件流数据清空后由新 schema 重新开始。

## 2. 外部实现校准

### 2.1 Kimi Chat 抓包

2026-07-23 的 Kimi Chat 抓包表现为轻量状态操作流：

```text
set chat.lastRequest
set user message
set assistant message(status=generating)
set block.text
append block.text.content × N
set message.status(completed)
done
```

它证明了三点：

- 前端热流不需要重复携带 aggregate、trace、producer 和页面组件信息。
- 一个稳定序号可以同时承担排序、去重和恢复游标。
- UI 应从最终状态节点选择 renderer，而不是让每个业务事件直接选择组件。

本设计进一步收敛：Kimi 的 `mask` 仍要求公共 reducer 理解字段更新规则，本项目改为 `open / delta / close`。细粒度合并由 kind 模块负责，公共协议不再包含状态路径。

### 2.2 其他公开实现

- [OpenAI Agents SDK Streaming](https://openai.github.io/openai-agents-python/streaming/)：区分 raw response event、run item event 和 tracing；最后一个 token 不等于 Run 已收口。
- [LangGraph Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)：运行事实、状态快照与消费者更新可以分层，不要求 UI 消费完整执行事件。
- [AutoGen Message and Communication](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/framework/message-and-communication.html)：消息应是可序列化纯数据，通信与执行对象解耦。
- [CloudEvents Specification](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)：事件上下文与业务 data 分离，但前端投影流不需要复制完整审计包络。

本项目不复制任何外部产品的私有实现，只采用“后端事实独立、前端轻量投影、稳定游标、组件注册”的共同原则。

## 3. 重构前实现的问题

重构前在线链路为：

```text
AgentScope/raw
  -> runtime_events.py
  -> RUNTIME_EVENT(type + scope + payload)
  -> event_hub/projector.py
  -> RunState / DisplaySnapshot
  -> RUN_STATE_PATCH(kind + DisplayEvent)
  -> runStateStreamAdapter
  -> displayEventReducer(event + block)
```

主要问题不是代码量，而是同一展示事实经过多套判别键：

```text
scope + payload.type
  -> RUN_STATE_PATCH.kind
  -> DisplayEvent.event / block
  -> component branch
```

由此产生：

- 一个跨领域 projector import Tool、HITL、Skill、Plan、Subagent 等全部逻辑。
- 前端从工具参数、工具结果和 optional payload 反解析领域状态。
- 新增展示类型需要同时修改 emitter、mapping、RunState、adapter、reducer 和组件。
- Snapshot、实时流和历史 replay 使用不同转换路径，刷新后容易与实时状态不一致。
- `eventId`、`seq`、cursor、revision 职责重叠。

新设计不承诺总代码量显著下降。它把复杂度从“每个消费端反复猜测”收敛到“后端每个 kind 明确投影一次 + 前端 kind 模块明确合并一次”，目标是降低新增领域的边际改动面。

## 4. 三层数据必须分开

### 4.1 Domain Event

Domain Event 是后端已经成立、不可修改的业务事实：

```ts
type DomainEvent<T = unknown> = {
  id: string;
  type: string;
  dataVersion: number;
  aggregateType: string;
  aggregateId: string;
  revision: number;
  occurredAt: string;
  causationId?: string;
  correlationId?: string;
  traceId?: string;
  data: T;
};
```

例如：

```text
run.status_changed
plan.step_updated
artifact.version_created
subagent.task.status_changed
```

约束：

- Domain Event 不携带组件名、CSS、Block Tree 或页面结构。
- Command 成功后才产生 Domain Event；Command 本身不是事实。
- `type + dataVersion` 决定后端 decoder。
- Domain Event 只进入领域持久化、projector 和审计，不原样发送给 Chat UI。

### 4.2 Trace Event

模型 token、provider fallback、完整工具输入输出、权限决策和 span 属于 Trace：

```ts
type TraceEvent<T = unknown> = {
  id: string;
  runId: string;
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  type: string;
  occurredAt: string;
  data: T;
};
```

Trace 写入 `run_events` 或领域 transcript，通过分页 API 按需读取，不进入常驻 `DisplaySnapshot`。Block 如需跳转 Trace，由 projector 在已注册 kind 的 `data.traceRef` 中写入稳定引用；`traceRef` 不是 RuntimeFrame envelope 字段。

### 4.3 Projection Event

Projection Event 只描述 Block Tree 的增量变化：

```text
open    创建一个 Block
delta   把 patch 交给该 Block.kind 的 reducer
close   合并可选终态 patch，并关闭 Block
```

Projection Event 可以从 Domain Event、Trace 摘要和当前 projection state 重建。它不是业务事实源，也不承担完整调试信息。

### 4.4 后端实际链路

后端必须把事实、投影和传输拆开，不能继续使用 `RUNTIME_EVENT -> RunState -> DisplayEvent` 的跨领域总投影器：

```text
AgentScope / provider raw event
  -> normalizer
  -> Domain Event / Trace Event
  -> per-kind projector
  -> BlockLifecycleCoordinator
  -> ProjectionStreamStore
  -> SSE RUNTIME_EVENT
```

职责边界：

- `normalizer` 只把 AgentScope/provider 事件归一化为可持久化的 Domain Event，不构造 React 或页面字段。
- `Domain Event Repository` 保存已经成立的业务事实；`Trace Event Repository` 保存 token、完整工具 observation、权限和模型调用详情。
- `per-kind projector` 只负责把一个领域的事实翻译为候选 `open/delta/close` Frame，不 import 其他领域 projector。
- `BlockLifecycleCoordinator` 统一校验 parent、生命周期、深度和容量，并处理父子 close barrier。
- `ProjectionRunner` 按 Run stream 的全局领域序号串行分发事件，保证跨 aggregate 的父子因果顺序。
- `ProjectionStreamStore` 原子提交 `seq`、replay frame、全局领域 checkpoint，并按策略提交 Snapshot checkpoint。
- `SSE Stream Service` 只读取已提交的 Snapshot/Frame，并负责 `afterSeq`、Snapshot fallback、done 和 heartbeat。

SSE 外层仍然只有 `RUNTIME_EVENT`，但其 data 是 `RuntimeFrame`。当前 `runtime_events.py` 中的 `{type, scope, payload}` 结构只属于待删除旧链路；新 normalizer 必须从 AgentScope/provider 原始事实直接产生新 Domain Event，不得把旧 envelope 当作兼容输入或转换中间层。

建议的持久化边界：

```text
chat_domain_streams(stream_key, run_id, next_ordinal, created_at, updated_at)
chat_domain_events(id, stream_key, stream_ordinal, run_id, aggregate_type, aggregate_id,
                   type, data_version, revision, data_json, occurred_at)
projection_streams(stream_key, run_id, scope, last_domain_ordinal, last_seq,
                   snapshot_seq, snapshot_json, snapshot_version, stream_done,
                   runner_fence, lease_owner, lease_expires_at, updated_at)
projection_frames(stream_key, seq, frame_json, created_at, UNIQUE(stream_key, seq))
```

`stream_key` 首期固定为一个 Chat Run 的投影流身份，resume 必须复用原 `stream_key`，不能重置 `stream_ordinal` 或 `seq`。领域事务通过 `chat_domain_streams.next_ordinal` 原子分配 Run 内严格递增的 `stream_ordinal`，并同时提交 aggregate 更新与 Domain Event append。事件表必须满足：

```text
UNIQUE(stream_key, stream_ordinal)
UNIQUE(aggregate_type, aggregate_id, revision)
```

同一 stream 只允许一个 `ProjectionRunner` 按 `stream_ordinal` 串行消费，再按事件类型分发到 per-kind projector。runner 先取得进程内 stream lock，再原子更新 lease 并递增 `runner_fence`；每次投影提交都必须携带取得 lease 时的 fence token，数据库只接受 token 等于当前 `runner_fence` 的写入。旧 runner、过期 lease 或重启前任务的提交必须被拒绝。热路径只使用 `projection_streams.last_domain_ordinal` 作为全局消费 checkpoint；不得让各 projector 独立推进消费水位。per-kind 版本只用于 schema 迁移或全量重建，不参与事件排序。

aggregate 更新与 Domain Event append 在一个领域事务中提交；ProjectionRunner 随后在独立投影事务中原子提交 projection frame、seq 和全局领域 checkpoint，并按 Snapshot checkpoint 策略选择是否同时更新 Snapshot。两段事务通过 `stream_key + stream_ordinal` 幂等衔接，避免业务事务被 UI 投影失败回滚，也避免跨 aggregate 父子事件乱序。

`run_events` 继续作为 Trace/Audit 存储；目标迁移完成后，旧 `chat_run_events.raw_event` 不再承担 Chat UI replay。aggregate revision 保证单 aggregate 顺序，`stream_ordinal` 保证一个 Run 内跨 aggregate 的领域因果顺序，Projection Stream 的 `seq` 只负责 UI Frame 顺序，三者不能互相替代。

## 5. RuntimeFrame V3

### 5.1 固定 Event Envelope

SSE 外层永远固定：

```text
event: RUNTIME_EVENT
data: RuntimeFrame
```

内部联合类型固定为：

```ts
type RuntimeFrame =
  | BlockOpenFrame
  | BlockDeltaFrame
  | BlockCloseFrame
  | SnapshotFrame
  | DoneFrame;

type BlockOpenFrame = {
  seq: number;
  op: "open";
  id: string;
  parent?: string;
  kind: string;
  dataVersion: number;
  patch?: unknown;
};

type BlockDeltaFrame = {
  seq: number;
  op: "delta";
  id: string;
  patch: unknown;
};

type BlockCloseFrame = {
  seq: number;
  op: "close";
  id: string;
  outcome: "completed" | "failed" | "cancelled";
  patch?: unknown;
};

type SnapshotFrame = {
  seq: number;
  snapshot: DisplaySnapshot;
};

type DoneFrame = {
  seq: number;
  done: true;
};
```

字段职责：

| 字段 | 职责 |
| --- | --- |
| `seq` | 当前订阅流内的唯一有序序号，同时用于去重和恢复 |
| `op` | 仅表示 Block 生命周期操作，不表达领域事件名 |
| `id` | Block 稳定身份 |
| `parent` | 父 Block ID；省略时为根 Block |
| `kind` | 仅 open 时出现，选择 kind module |
| `dataVersion` | 仅 open 时出现，固定本 Block data schema 版本 |
| `patch` | kind 自己定义的初始数据、增量或终态数据 |
| `outcome` | close 的公共终态，使 Generic Block 不解析 data 也能显示结果 |

RuntimeFrame 永远不包含：

```text
mask
channel
scope
runId/projectId
aggregateType/aggregateId
eventId/cursor/revision
producer
traceId/spanId
componentName
```

stream 归属由订阅 URL 和 Snapshot 决定；领域身份、Trace 引用和显示内容属于已注册 Block data。

### 5.2 为什么没有 mask

公共层不解释 patch：

```ts
const registration = blockRegistry[block.kind];
block.data = registration.reduce(block.data, frame.patch);
```

示例：

```text
text.delta       patch="正在分析..."          -> 字符串拼接
plan.delta       patch={stepId, status}       -> 更新对应步骤
tool.delta       patch={stdout: "..."}        -> 追加 stdout
browser.delta    patch={url, title, loading}  -> 合并浏览状态
```

这些 patch schema 是 kind contract，不是传输协议。新增 kind 不增加新的 op、mask、control frame 或通用 reducer 分支。

### 5.3 示例

```text
open  seq=1 id=msg-1 kind=message
open  seq=2 id=text-1 parent=msg-1 kind=text patch="你好"
delta seq=3 id=text-1 patch="，我正在处理"
close seq=4 id=text-1 outcome=completed
close seq=5 id=msg-1 outcome=completed
done  seq=6
```

Subagent 后续接入时只是嵌套 Block：

```text
open  id=sub-1  parent=msg-1 kind=subagent
open  id=act-1  parent=sub-1 kind=activity
delta id=act-1  patch={summary: "正在读取文件"}
close id=act-1  outcome=completed
close id=sub-1  outcome=completed patch={resultPreview: "..."}
```

完整 Subagent transcript、模型 token 和工具 observation 不进入此树，只投影用户当前需要看到的摘要 Block。

## 6. Block 生命周期

每个 Block 只能经历：

```text
不存在 -> open -> delta × N -> close
```

硬约束：

1. 同一 ID 只能成功 open 一次。
2. 非根 Block open 时，parent 必须已经存在且仍为 open。
3. delta 只能作用于已 open、未 close 的 Block。
4. close 只能成功一次；close 后拒绝所有 delta 和重复 close。
5. close patch 如存在，先由 kind reducer 原子合并，再写入公共 `phase/outcome`。
6. 父 Block 正常 `completed` 只有在全部 descendant 已 close 后才能 close。
7. 父 Block `failed/cancelled` 时，必须先对所有未关闭 descendant 级联发送 `close(cancelled)`，再关闭父 Block；级联 close 必须幂等。
8. sibling 顺序由 open frame 的 seq 决定，后续不可重排。
9. Block Tree 是 append-only projection；协议不提供 remove、move、clear。
10. 真正需要纠正投影时发送完整 Snapshot，不为低频修复扩展公共 op。
11. Block 嵌套深度设置防御性上限，首版为 16；超过上限视为协议错误。

等待用户输入、权限审批、外部执行和 Subagent waiting 都不关闭 Block。对应 kind 通过 delta 更新自己的 data，恢复后继续同一 Block；只有真正 completed/failed/cancelled 才 close。

detached 后台任务不属于当前 Run Tree。未来开放时必须转移到独立 ownership，不能让已经 done 的 Run Stream 继续追加 child。

## 7. DisplaySnapshot V4

DisplaySnapshot 是规范化 Block Tree：

```ts
type DisplaySnapshot = {
  version: 4;
  lastSeq: number;
  streamDone: boolean;
  order: string[];
  blocksById: Record<string, DisplayBlock>;
};

type DisplayBlock<T = unknown> = {
  id: string;
  parent?: string;
  kind: string;
  dataVersion: number;
  phase: "open" | "closed";
  outcome?: "completed" | "failed" | "cancelled";
  data: T;
};
```

规则：

- `order` 是 Block open 的唯一全局顺序索引。
- 根 Block 为 `parent` 为空的 Block。
- `childrenByParent`、root 列表和树深度均由 `order + blocksById` 派生，不进入持久 Snapshot，避免双向索引漂移。
- Message 是 `kind=message` 的根 Block；正文、reasoning、tool、approval、files 等是其 child。
- `lastSeq` 是 Snapshot 已经包含的最后 frame 水位。
- `streamDone=true` 表示 Snapshot 已包含 done；订阅已完成 Run 时可以只发送 Snapshot 后关闭连接。

Snapshot frame 必须满足：

```text
frame.seq === frame.snapshot.lastSeq
```

Snapshot hydrate 原子替换 Block Tree、lastSeq 和旧 buffer，不能拆成一批伪 open/delta frame。Snapshot、完整 replay 和实时 apply 的最终 `DisplaySnapshot` 必须深度等价。

持久化 Snapshot 是周期 checkpoint，不要求每个 Frame 都重写整棵树。`projection_streams.snapshot_seq` 必须等于持久化 `snapshot_json.lastSeq`；`last_seq` 可以大于 `snapshot_seq`，两者之间的状态由 `projection_frames(snapshot_seq + 1 ... last_seq)` 表达。API 返回给前端的 Snapshot 必须先在服务端应用这段 tail replay，或先发送持久化 Snapshot 再连续发送 tail Frame，最终语义完全一致。

### 7.1 RunState 边界

目标前端只以 `RUNTIME_EVENT` 和 `DisplaySnapshot V4` 为事件/恢复边界：

- `chat_runs` 和后端 Run service 继续持久化 lifecycle、ownership、取消和恢复信息。
- `/api/chat/runs/{runId}/state` 保留 URL，但目标响应直接返回 `DisplaySnapshot V4`，不再返回一套并列 UI aggregate。
- `RUN_STATE`、`RUN_STATE_PATCH`、固定 `tools/plans/skills/approvals` 展示字段在切换阶段删除。
- UI 不允许同时读取 RunState 展示字段和 Block Tree。

## 8. Kind Module Registry

每个 kind 以一个模块注册数据契约、reducer 和 renderer：

```ts
type BlockModule<TData, TPatch> = {
  kind: string;
  displayName: string;
  currentDataVersion: number;
  decodeInitial(dataVersion: number, value: unknown): TData;
  decodePatch(dataVersion: number, value: unknown): TPatch;
  reduce(data: TData, patch: TPatch): TData;
  renderer: React.ComponentType<{ block: DisplayBlock<TData> }>;
};

const blockRegistry: Record<string, BlockModule<unknown, unknown>> = {
  message: messageBlockModule,
  text: textBlockModule,
  reasoning: reasoningBlockModule,
  tool: toolBlockModule,
  approval: approvalBlockModule,
  plan: planBlockModule,
  artifact: artifactBlockModule,
};
```

Frame 的解码语义固定为：`open.patch` 只调用 `decodeInitial`；`delta.patch` 调用
`decodePatch` 后交给 `reduce`；`close.patch` 也先经过 `decodePatch + reduce`，最后写入公共
`phase/outcome`。`open` 没有 patch 时使用 kind 声明的空初始值。一个 Block 生命周期内
`dataVersion` 不变，delta/close 使用 open 时确定的版本。

新增展示能力只需要：

```text
后端 per-kind projector
+ 前端 BlockModule（schema/decode/reduce/renderer）
+ tests
```

不修改：

```text
RuntimeFrame
SSE transport
seq/reconnect
generic Block Tree reducer
DisplaySnapshot shape
其他 kind module
```

### 8.1 Reducer 约束

- reducer 必须是纯函数和确定性函数。
- reducer 不读取系统时间、随机数、DOM、网络或全局可变状态。
- reducer 不产生副作用，不发请求，不调用命令 API。
- patch 必须先由 `decodePatch` 校验，不允许直接把 unknown spread 到 data。
- `dataVersion` 在一个 Block 生命周期内不可改变。
- 历史 dataVersion 由对应 kind module 解码或迁移，不增加 RuntimeFrame protocol version。
- 高频 text delta 可以在渲染前按 animation frame 批量 reduce，但不能改变 seq apply 顺序。

### 8.2 Unknown Kind

未知 kind 或未知 dataVersion 必须降级为 Generic Block：

- 只显示 `kind`、`phase` 和公共 `outcome`。
- 不展示、摘要、Markdown 渲染或字符串化未知 data/patch。
- open 时可以保存经过后端投影安全策略处理的 opaque initial data，但不得交给 UI。
- 无可用 reducer 时忽略该 kind 的 delta data，并记录协议诊断；公共 close 仍可更新 phase/outcome。
- 未知 kind 不得令整个 Chat 页面崩溃。

正常桌面发行版中前后端同版本，unknown kind 主要用于历史版本、部分升级和协议损坏时的安全降级，不是常规扩展机制。

## 9. 单 Stream 与恢复

### 9.1 首期只有 Chat Run Block Stream

首期实时入口只有：

```text
/api/chat/runs/{runId}/state/stream?afterSeq=N
```

一个 Chat 页面、一个 Run、一个 Block Stream。Subagent、Plan、Tool、Approval 等不新开 stream，只作为 Block Tree 的 kind/child。

Project 任务中心、Subagent 控制台和 Detail 页面首期继续使用普通查询 API；Trace 使用分页查询。未来某个页面确实需要实时更新时，可以在新的 scoped URL 上复用完全相同的 RuntimeFrame 和 Block Tree，但不得在 frame 中增加 `channel`，也不得让前端公共 reducer理解 stream 类型。

因此“单 stream”表示每个 UI projection scope 只消费一条统一 Block Stream，不表示把全平台所有 Run、Trace 和 Project 数据塞进一个全局连接。

### 9.2 首包

首次订阅先读取最近一次持久化 Snapshot checkpoint，并连续发送其后的 tail Frame：

```text
snapshot(seq=S, lastSeq=S)
frame S+1
frame S+2
...
frame N
```

服务端也可以先应用 `S+1...N`，直接返回物化到 N 的 Snapshot；两种路径最终状态必须深度等价。`/state` 普通查询必须返回物化到当前 `last_seq` 的完整 Snapshot，不得把 tail replay 责任暴露给非流式调用方。

如果持久化或物化后的 Snapshot 已经 `streamDone=true`，发送 Snapshot 后直接正常关闭连接，不需要重复发送同 seq 的 done。

### 9.3 重连

客户端携带最后成功应用的 seq：

```text
GET/STREAM afterSeq=N
```

- replay log 仍包含 N 之后的 frame：发送 `N+1...`。
- seq 已过期、Snapshot schema 变化、ownership 变化或服务端无法证明连续性：直接发送最新 Snapshot，原子替换本地状态。
- 不发送 reset event；Snapshot 本身就是唯一 reset/hydrate 机制。
- 收到 `seq <= lastSeq` 的增量 frame 时幂等忽略。
- 收到 `seq > lastSeq + 1` 时暂存并重连补齐；暂存受最大帧数、最大字节数和最大等待时间限制，任一超限或补齐失败则丢弃 buffer 并重新拉取 Snapshot。
- done 分配最后一个严格递增 seq；处理后 stream adapter 标记 done 并推进 lastSeq。
- 网络连接关闭但没有 done 或 `snapshot.streamDone=true` 时，只视为断线，不推断 Run failed。
- resume 继续使用原 Run 的 `stream_key`、`lastSeq` 和 `last_domain_ordinal`；不得创建新 projection stream 或从零开始编号。

### 9.4 Snapshot checkpoint 与 retention

实时事务始终 append `projection_frames` 并推进 `last_seq`；只有达到 checkpoint 策略时才序列化整棵 Snapshot。首版策略固定为：

- 距上次 Snapshot 达到 128 个 Frame；
- 距上次 Snapshot 达到 2 秒且期间有新 Frame；
- stream done；
- replay retention 压缩或 Snapshot schema 迁移前。

这些阈值是后端可配置性能参数，不属于 RuntimeFrame 协议。时间触发的 checkpoint 必须取得同一 stream 的 runner lock/fence，基于已经提交到 `last_seq` 的工作状态写入；它不分配新 seq，也不推进 `last_domain_ordinal`。Snapshot JSON 必须与其 `snapshot_seq` 在同一事务提交。retention 只能删除 `seq <= snapshot_seq` 的旧 Frame，绝不能删除恢复当前 Snapshot 仍需要的 tail Frame。

### 9.5 Heartbeat

heartbeat 使用标准 SSE comment，不进入 RuntimeFrame：

```text
: heartbeat
```

它不分配 seq、不写 replay log、不进入 reducer、不影响 Snapshot。
前端连接存活判断必须依据底层 SSE 字节到达（包括 comment）或 `fetch` reader，不能只依赖原生 `EventSource.onmessage`，因为 comment 不会触发业务消息回调。

## 10. 前端模块边界

目标目录职责：

```text
frontend/src/features/runtime-stream/
  protocol.ts          RuntimeFrame 类型和 frame decoder
  streamAdapter.ts     SSE、seq、snapshot、done、重连
  blockTreeReducer.ts  open/delta/close 公共生命周期
  blockRegistry.ts     kind module 注册
  selectors.ts         roots/children/tree 派生
  GenericBlock.tsx     unknown kind 安全降级

frontend/src/features/runtime-blocks/
  message/
  text/
  reasoning/
  tool/
  approval/
  plan/
  artifact/
```

依赖方向：

```text
page
  -> runtime-stream
  -> blockRegistry
  -> individual kind module
```

kind module 不 import 页面，不 import其他领域 module。Block renderer 发起批准、取消、重试等操作时调用显式 command API；command 结果仍通过后端 projection 返回 Block Frame，renderer 不直接修改 source of truth。

前端通用层只做三件事：解码固定 Frame、维护规范化 Block Tree、按 `kind` 分发模块。它不读取 Domain Event、Trace、工具原始参数、工具完整结果或旧 RunState 字段。

`streamAdapter` 必须统一处理：首包 Snapshot、连续 replay、重复 seq、seq gap、Snapshot 替换、done、断线重连和底层 heartbeat。`blockTreeReducer` 只处理公共生命周期，不按字段路径解析 patch；未知 kind 进入 Generic Block，不得让 Chat 页面崩溃。

前端 renderer 的批准、取消、重试、展开详情等操作必须调用显式 command API。renderer 不直接改写 Block，也不把 command response 当作新的 UI 状态源；命令成功或失败仍由后端产生 Domain Event，再经 projector 返回 Block Frame。

### 10.1 前端状态模型

前端只保存一份规范化展示状态，不再并列维护 `tools[]`、`plans[]`、`approvals[]` 和 DisplayEvent timeline：

```ts
type RuntimeStreamState = {
  snapshot: DisplaySnapshot;
  gapBuffer: Map<number, RuntimeFrame>;
  connection: "connecting" | "live" | "reconnecting" | "done" | "protocol_error";
};
```

`roots`、`childrenByParent`、可见折叠节点和各 kind 列表只能由 selector 派生，不能再次持久化为第二套 source of truth。页面卸载、切换 Run 或 Snapshot hydrate 时必须同时清空旧 gap buffer 和派生缓存。
`connection` 只描述传输连接，不代表 Run outcome；断线不得被映射成业务 failed。

### 10.2 Frame 应用顺序

```text
Snapshot
  -> decoder 校验 version/lastSeq
  -> 原子替换 snapshot、lastSeq 和 gapBuffer

Frame.seq <= lastSeq
  -> 幂等忽略

Frame.seq == lastSeq + 1
  -> blockTreeReducer apply
  -> 推进 lastSeq
  -> 连续 drain gapBuffer

Frame.seq > lastSeq + 1
  -> 有界暂存
  -> 以 lastSeq 重连补齐
  -> 超限或超时回 Snapshot
```

`open` 由通用 reducer 建立 identity/parent/kind，再调用 kind module 的 `decodeInitial`；`delta` 和 `close.patch` 先按 Block 已保存的 `dataVersion` 解码，再调用 kind reducer。任何 decode/reduce 失败只产生协议诊断，不允许部分修改现有 Block。

## 11. 后端模块边界

目标职责：

```text
raw adapters
  -> normalizer
  -> domain service / domain event repository
  -> ProjectionRunner（按 stream_ordinal 串行消费）
  -> per-kind projector
  -> BlockLifecycleCoordinator
  -> projection stream store
  -> SSE Stream Service
```

- `raw adapter`：归一化 AgentScope/provider 原始事件，不构造 React 组件形状。
- `Domain service`：维护 aggregate、状态转换和 revision；Command 成功后才 append Domain Event。
- `Domain event repository`：持久化业务事实，不作为前端协议。
- `Trace repository`：保存完整模型/工具/HITL 细节，Block 只通过 `data.traceRef` 建立按需跳转。
- `ProjectionRunner`：每个 stream 同时只允许一个有效 runner，严格按 `last_domain_ordinal + 1` 消费并路由事件；不同 kind 不得独立抢读同一领域流。
- `per-kind projector`：单领域、单文件，把领域事实或安全 Trace 摘要翻译为候选 Block Frame。
- `BlockLifecycleCoordinator`：校验 parent、open/delta/close、深度、容量和父子 close barrier；失败/取消时对子树执行幂等级联 close。
- `projection stream store`：原子分配 seq、保存 replay log、全局 `last_domain_ordinal`，并按策略保存 Snapshot checkpoint。
- `SSE Stream Service`：只发送已提交的 Snapshot/Frame 和 SSE heartbeat comment，负责 `afterSeq`、过期 Snapshot fallback 和 done。

后端不得让 projector 直接写 SSE，也不得让 handler 直接修改 DisplaySnapshot。所有投影必须经过同一个 coordinator 和 writer，保证实时 apply、完整 replay 和 Snapshot hydrate 结果一致。

目标目录职责：

```text
backend/src/server/routes/chat/projection/
  domain_events.py              Domain Event schema/normalizer
  projection_runner.py          全局领域顺序、锁/fencing、重启续跑
  frame_schema.py               RuntimeFrame / DisplaySnapshot V4
  block_lifecycle.py            公共 Block 生命周期校验
  projection_writer.py          seq、tail replay、checkpoint 原子提交
  projection_registry.py        kind projector 注册表
  stream_service.py             afterSeq、Snapshot fallback、SSE
  projectors/
    message.py
    text.py
    reasoning.py
    tool.py
    approval.py
    plan.py
    artifact.py
    subagent.py
```

`handlers` 只负责鉴权、Run ownership、请求参数和调用 `stream_service`；Agent runtime 只产生
Domain Event，不直接拼 SSE 字符串。

### 11.1 Projector 隔离

- 每个 kind 的 projector 独立文件或独立领域目录。
- projector 不 import 其他领域 projector 或 renderer helper。
- 通用 helper 只能处理 ID、时间、大小限制和 frame builder，不包含领域分支。
- 脱敏策略由每个 kind 声明式提供字段白名单或裁剪 schema；公共 helper 只执行配置，禁止出现 `if kind == "tool"` 一类领域分支。
- 禁止重新出现一个 import Tool、HITL、Plan、Skill、Subagent 的巨型 projector。
- 增加静态依赖检查，阻止跨领域 projector import。

### 11.2 一致性

- aggregate 更新、Run 内 `stream_ordinal` 分配与 Domain Event append 在同一个 SQLite transaction 中提交。
- 每个 stream 只有一个持有当前 `runner_fence` 的有效 ProjectionRunner，严格读取 `last_domain_ordinal + 1`；投影事务必须比较 fence token，per-kind projector 只被 runner 调用，不持有独立消费游标。
- 同一 Domain Event 产生的 Block Frame seq 分配、replay log append、`last_domain_ordinal`/`last_seq` 更新必须在同一个 transaction 中完成；命中 checkpoint 策略时，Snapshot 与 `snapshot_seq` 也加入该事务。
- SSE 只读取已经提交的 projection log，不直接依赖内存 callback。
- 进程在 commit 后、发送前退出时，客户端仍可按 seq replay。
- projector 失败不回滚已经成立的业务事实，但 stream 不得越过缺失 frame 发送更大 seq。

### 11.3 投影提交事务

一条 Domain Event 的投影提交必须遵循以下顺序并在同一事务中完成：

```text
读取 projection_streams.last_domain_ordinal
  -> 只读取 stream_ordinal = last_domain_ordinal + 1 的 Domain Event
  -> 幂等检查 stream_key/stream_ordinal 和 Event ID/revision
  -> 运行 per-kind projector
  -> BlockLifecycleCoordinator 在内存工作状态中校验并 apply
  -> 分配 seq、写入 projection_frames
  -> 更新 projection_streams.last_domain_ordinal/last_seq
  -> 若命中 checkpoint 策略，更新 snapshot_json/snapshot_seq
  -> commit
```

一个 Domain Event 即使不产生 Frame，也必须在同一事务推进 `last_domain_ordinal`，否则会被无限重复消费。进程在 commit 后、SSE 发送前退出时，客户端仍可通过 `afterSeq` replay。投影失败时保留 Domain Event，但不得推进 `last_domain_ordinal`，也不得提交后续更大的 seq 越过缺口；进程重启后从最近 Snapshot + tail Frame 重建工作状态，再继续消费。

### 11.4 首批领域映射

Domain Event 和 Block Frame 不是一一对应；一个 projector 可以根据当前 Snapshot 产出零个、一个或多个候选 Frame：

| Domain Event | Projector 输出 |
| --- | --- |
| `message.started` | `open(kind=message)` |
| `agent.output.started` | `open(kind=text, parent=message)` |
| `agent.output.delta` | `delta(text)` |
| `agent.output.completed` | `close(text, completed)` |
| `tool.call.started` | `open(kind=tool, parent=message)` |
| `tool.call.delta` / `tool.result.delta` | `delta(tool)` |
| `tool.result.completed` | `close(tool, completed/failed)` |
| `interaction.waiting` | `open/delta(kind=approval)`，保持父 message open |
| `interaction.resumed` | `close(approval)`，继续原 message |
| `run.completed` | 校验所有 descendant 已 close，关闭 message，提交 done；不满足时进入 §11.5 投影修复 |
| `run.failed/cancelled` | 级联关闭未结束 descendant、关闭 message、提交 done |

Projector 不得从工具 JSON 字符串反解析 UI 状态；需要展示的领域事实必须在 normalizer/Domain Event 阶段结构化。完整工具输入输出仍进入 Trace，Block 只保留经过 per-kind 白名单裁剪的摘要。

### 11.5 终态收敛与异常修复

`run.completed` 产生前必须先经过 Domain completion barrier，而不是把收敛责任留给 projector：

```text
请求成功收尾
  -> 停止创建新的 child activity
  -> flush normalizer 和待写 Domain Event
  -> 等待所有 active activity/tool/text/subtask 产生终态事件
  -> barrier 成功：append run.completed
  -> barrier 超时：append run.failed(reason=completion_barrier_timeout)
```

completion barrier 使用有界超时，且检查的是领域活动状态，不读取 DisplaySnapshot。`run.completed`、`run.failed` 和 `run.cancelled` 仍保持终态不可逆。

若已经提交的 `run.completed` 到达 ProjectionRunner 时仍存在 open descendant，说明 checkpoint、投影实现或 Domain Event 日志违反不变量。处理顺序固定为：

1. 回滚当前投影事务并记录 `projection.invariant_violation` Trace。
2. 从该 stream 的完整 Domain Event 序列重建 Block Tree，不能只重试当前内存状态。
3. 重建后已满足 barrier：正常提交 `run.completed` 投影。
4. 重建后仍存在残留 descendant：在同一修复事务中按叶子到根顺序将残留 descendant `close(failed)`，再将 message/root `close(completed)` 并提交 done。

应急修复不能追加 `run.failed` 或改写 `chat_runs.status`，因为 `run.completed` 已是成立且不可逆的业务事实。异常 child 通过公共 `outcome=failed` 和 Trace 诊断暴露；根 Block 保持 completed，从而既不伪造领域终态，也不让前端永久 open。

## 12. 安全和容量限制

- Block patch 必须是 JSON 可序列化纯数据。
- projector 负责敏感信息过滤和展示裁剪，禁止把 token、凭据、完整 headers 或完整工具 observation 写入 Block。
- unknown kind 不展示 data。
- text/tool/log delta 设置单帧和单 Block 累计大小上限；超限后转 Artifact/Trace 引用。
- Snapshot 设置总大小上限；大对象只保存摘要与 `detailRef`。
- Block 数量、嵌套深度和单 Run replay retention 必须有上限。
- seq gap 暂存必须同时设置最大帧数、最大字节数和最大等待时间，超限立即回 Snapshot。
- renderer 只渲染 decode 后的数据，不使用 `dangerouslySetInnerHTML` 处理未知 payload。
- 用户命令通过现有权限/API 链路执行，Block patch 不能直接触发副作用。

## 13. 在线实现

当前生产链路已经完成以下切换：

- 后端使用结构化 `SourceEvent -> DomainEvent -> ProjectionRunner -> RuntimeFrame`，不再序列化原始 SSE 后反解析。
- `message/text/reasoning/thinking/tool/skill/approval/plan/artifact/error/subagent/team/team-member/team-task/team-activity/team-mail` 均有独立 projector 和前端 kind module。
- `/api/chat/runs/{runId}/stream` 只发送 `RUNTIME_EVENT: RuntimeFrame`；`/state` 只返回 DisplaySnapshot V4。
- Chat 与 Sandbox 只渲染 V4 Block Tree；HITL 命令结果必须经 Frame 回流。
- 已删除 `event_hub`、RunState schema、DisplayEvent reducer、旧 stream adapter 和固定领域展示组件。
- SQLite 启动时检测旧 Chat 投影表或非 V4 Snapshot 后执行一次性运行域 reset；不读取、不转换旧数据。
- 专项回归覆盖全局 ordinal、lease/fencing、非法生命周期、Snapshot + tail replay、retention fallback、completion barrier、resume 和 terminal done。

## 14. 测试矩阵

### 14.1 Protocol 与 Block 生命周期

- open/delta/close 合法序列。
- delta before open、重复 open、重复 close、close 后 delta 全部拒绝。
- parent 不存在、parent 已关闭、parent 先于 open descendant close 全部拒绝。
- sibling 严格按 open seq 排序。
- 嵌套深度和 Block 数量上限。
- close patch 与 phase/outcome 原子提交。
- 父节点 `failed/cancelled` 的 descendant 级联 close 顺序、幂等和最终 Snapshot 正确。

### 14.2 Kind Module

- text 字符串 delta 顺序拼接。
- plan step patch、tool stdout patch 和结构化 patch 的确定性合并。
- reducer 对相同初始状态和 frame 序列产生相同结果。
- open patch 只走 decodeInitial，delta/close patch 只走 decodePatch + reduce。
- patch decode 失败不污染现有 Block。
- 当前各 kind dataVersion 严格解码；不为切换前旧数据提供 decoder 或 migration。
- unknown kind/version 只显示 kind/phase/outcome，不泄露 data。
- 注册新 kind 不修改 RuntimeFrame、streamAdapter 或 blockTreeReducer。

### 14.3 Snapshot 与恢复

- 完整 replay、Snapshot hydrate、Snapshot + 后续 frame 深度等价。
- Snapshot frame seq 与 `snapshot.lastSeq` 不一致时拒绝 hydrate。
- Snapshot 水位正好落在 text delta 后重连，不重复追加字符。
- 周期 Snapshot + tail replay 与逐帧实时 apply 深度等价。
- 只删除 `seq <= snapshot_seq` 的 retention 压缩后仍可完整恢复。
- 重复 seq 幂等忽略。
- 跳号补齐，补齐失败后 Snapshot 替换。
- gap buffer 超过帧数、字节数或等待时间后自动回 Snapshot。
- done 严格晚于业务 Block close，重复 done 幂等。
- 已完成 Run 的 `streamDone=true` Snapshot 后正常关闭。
- SSE heartbeat comment 不进入 reducer 和 replay log。
- fetch reader 收到 heartbeat comment 时更新传输存活时间，但不生成 RuntimeFrame。
- 网络断开但无 done 时不误判失败。

### 14.4 后端一致性

- aggregate/event transaction 在 commit 前后崩溃恢复。
- 并发 aggregate append 获得同一 stream 内唯一、连续且按提交顺序消费的 `stream_ordinal`。
- message/tool/plan 跨 aggregate 事件按全局 ordinal 投影，不发生 child before parent。
- ProjectionRunner 锁/fencing 阻止两个 runner 同时消费同一 stream。
- `last_domain_ordinal` 与 frame transaction 在 commit 前后崩溃恢复；零 Frame 事件也只消费一次。
- seq/replay log/last_domain_ordinal 同 transaction；命中策略时 Snapshot/snapshot_seq 也同 transaction。
- projector 失败时不越过缺口发送。
- Domain Event 已提交、投影事务失败、进程重启后的 checkpoint 重放。
- commit 后发送前退出仍可 replay，且不会重复 seq 或重复 Block delta。
- completion barrier 成功后才产生 run.completed，超时产生 run.failed。
- run.completed 遇到残留 open descendant 时先全量重建，仍不一致则应急 close 并保证 done。
- resume 复用原 stream_key、last_domain_ordinal 和 seq 空间。
- per-kind 脱敏白名单生效，公共 helper 不包含领域分支。
- 不同 kind projector 不存在跨领域 import。

### 14.5 前端和 E2E

- Message + text streaming + done。
- reasoning、tool 成功/失败、Plan、HITL waiting/resume、files 和 error。
- 多层 Block 折叠、未知 kind、长内容截断和 detailRef。
- 刷新、断线、重启、取消和历史回放。
- 高频 delta 批处理不改变字符顺序或 seq 水位。
- 前端不读取 raw Domain Event、Trace、工具 args/result 或旧 RunState UI 字段。

### 14.6 删除验收

- 没有同一用户可见功能的新旧 projector 双写。
- 不存在 mask registry、动态路径赋值或 payload 猜测。
- 不存在 `RUN_STATE_PATCH.kind -> DisplayEvent.event/block` 主 UI 链路。
- 不从 DelegateTask/tool result/JSON 字符串反解析领域展示状态。
- 已迁移 Chat 不依赖旧 event constants、adapter、reducer 和 fixture。

## 15. 最终验收标准

1. SSE 应用事件名只有 `RUNTIME_EVENT`。
2. RuntimeFrame 只有 open/delta/close、Snapshot 和 done 五种稳定形状。
3. 通用前端只理解 seq、Block identity、父子关系和生命周期。
4. 新增 kind 不修改 RuntimeFrame、stream transport、恢复逻辑或通用 reducer。
5. Message、Tool、Plan、Approval、Artifact、Subagent 和 Team 均使用同一 Block Tree。
6. Snapshot、实时 Frame 和完整 replay 产生相同状态。
7. Domain Event、Trace Event 和 Projection Event 职责独立。
8. `/state` 只返回 DisplaySnapshot V4，前端不再消费并列 RunState UI projection。
9. mask、旧 DisplayEvent、旧 RUN_STATE_PATCH UI 链路和工具结果反解析全部删除。
10. 旧逻辑和旧事件流数据直接删除，不实现 migration、双读、双写或兼容 fallback。
11. 同一 Run 的跨 aggregate Domain Event 按唯一 `stream_ordinal` 串行投影，不发生 child before parent。
12. Snapshot 使用周期 checkpoint + tail replay；高频 delta 不逐帧全量重写 Snapshot。
13. completion barrier、全量重建和应急 close 保证所有终态 stream 最终 done，且 projector 不改写业务终态。

## 16. 非目标

- 不在本次引入 Kafka、NATS、Redis Stream 或分布式 worker。
- 不用 Block Frame 取代后端 Domain Event、Trace 或领域 transcript。
- 不把完整模型输出、完整工具 observation 或完整 Subagent transcript 放入 DisplaySnapshot。
- 不用事件流基础设施决定 Subagent、Team、Plan 或 Artifact 的领域状态机。
- 不保证不同订阅 URL 之间的全局 seq 或跨 stream 因果顺序；前端不得比较不同 stream 的 seq。
- 不为未来假设提前增加 remove/move/channel/mask 等公共协议能力。
