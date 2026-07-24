# Chat Event Stream V3 稳定契约

> 状态：2026-07-23 已在线。
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
| `text` | 最终回答 | 字符串追加或 `{replace}` |
| `reasoning` | 执行过程 | 字符串追加或 `{replace}` |
| `thinking` | 模型思考 | 字符串追加或 `{replace}` |
| `tool` | 工具调用与结果摘要 | 结构化合并；input/output delta 追加 |
| `skill` | Skill 生命周期 | 浅合并结构化字段 |
| `approval` | HITL / AskUser | 浅合并；resume 后 close |
| `plan` | Plan Mode 和任务进度 | 浅合并结构化字段 |
| `artifact` | 产物摘要与下载引用 | 浅合并结构化字段 |
| `error` | 用户可见错误 | 浅合并结构化字段 |
| `subagent` | 子 Agent 生命周期 | 浅合并；内部活动使用子 Block |
| `team` | TeamRun 摘要与成员活动 | 浅合并；内部活动使用子 Block |

新增展示能力只能新增一个后端 per-kind projector 和一个前端 Block module。不得增加 RuntimeFrame op、顶层 SSE 事件、公共 reducer 分支或 payload 反解析。

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
- Domain Event 是业务事实；Trace Event 是诊断事实；RuntimeFrame 是 UI projection，三者不得混用。
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
