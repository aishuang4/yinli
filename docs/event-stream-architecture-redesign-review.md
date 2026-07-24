# 事件流架构重构方案审计

> 审计对象：[event-stream-architecture-redesign.md](./event-stream-architecture-redesign.md)（Block Tree 版）
> 关联文档：[chat-sse-event-contract.md](./chat-sse-event-contract.md)、[subagent-runtime-event-redesign.md](./subagent-runtime-event-redesign.md)
> 审计焦点：SSE 协议对前端是否足够简单、后续给 agent 加功能是否不动协议、是否真正缓解“现有实现太重”。
> 初审结论：Block Tree 方向正确，把前端要理解的概念砍到最少，达成了“加 kind 不动协议”的目标。
> 复核状态：初审提出的 5 个运行时语义问题已补入主设计；版本命名和兼容事件边界已同步到 SSE 契约。源码仍未实施。
> 全链路复核（本轮）：协议层稳定，无需再改。新增后端链路的 3 个必须决策项已按 §9 结论补入主设计，源码仍未实施。

## 1. 相较上一版（mask 版）的改进

上一版审计提出“mask registry 会膨胀成新判别地狱”“projector 跨领域 import 会复活”。本版已正面回应：

| 上一版问题 | 本版处理 | 结论 |
| --- | --- | --- |
| mask 是路径易被误实现为动态赋值 | 删除 mask，`patch` 由 `blockRegistry[kind].reduce` 处理，公共层不解析路径（§5.2） | 已解决 |
| mask 粒度膨胀 | 协议只剩 open/delta/close，粒度差异全进 kind 的 `data`（§5.2） | 已解决 |
| projector 跨领域 import 复活 | §11.1 明令单领域单文件 + 禁跨领域 import + 静态检查 | 已写入约束 |
| Generic Block 脱敏未定义 | §8.2 明确未知 kind 只显示 kind/phase/outcome，不展示 data | 已解决 |
| Trace 关联键缺失 | §4.2 明确 `data.traceRef` 由 projector 写入，非 envelope 字段 | 已解决 |
| 跨 stream 顺序未定义 | §16 明确非目标：前端不得比较不同 stream 的 seq | 已解决 |
| `/state` “可以保留”模糊 | §7.1 明确直接返回 DisplaySnapshot V4 | 已解决 |
| append 边界重连测试缺失 | §14.3 已加“Snapshot 水位正好落在 text delta 后重连” | 已解决 |

上一版六条补强 + 两条硬约束基本全部落地。

## 2. 对“前端好处理”的评估

达成。前端通用层只需理解四件事：`seq`、Block id、`parent`、open/delta/close 生命周期。对比旧链路的 `scope + payload.type -> RUN_STATE_PATCH.kind -> DisplayEvent.event/block -> component branch` 四层判别，这是数量级的简化。

关键收益：**加一个新 agent 展示能力 = 加一个后端 projector + 一个前端 BlockModule + 测试，协议/传输/reducer/恢复逻辑零改动**（§8、§15.4）。这正是诉求“后续给 agent 加新功能，现在太重了”的正解。Subagent 作为 `kind=subagent` 嵌套 Block 而非新 stream（§5.3），验证了这条路径对最复杂的扩展场景也成立。

## 3. 初审发现（历史记录）

复杂度已从协议层转移到后端 projector 和少数运行时语义。以下是本版新暴露或仍需明确的问题。

### 3.1 父子 close barrier 与流中断的死结（§6 规则 6）——最需要决策

“父 Block 默认只有在全部 descendant 已 close 后才能 close”是好约束，但与真实失败路径冲突：

- Run 被 cancel 或 crash 时，某个 child（如 running 的 tool）可能永远不会收到 close。
- 此时父 message / 根 Block 按规则**无法 close**，前端会停在“永远 open”的状态。

文档没定义“父要 failed/cancelled close 时，未收口的 child 怎么办”。需要明确一条级联规则，二选一：

1. **级联 close**：父 close(failed/cancelled) 时，projector 必须先对所有未 close descendant 发 close(cancelled)，再 close 父。（推荐，语义清晰）
2. **前端派生**：父 closed 后，前端把未 close 的 descendant 视为 cancelled 渲染。（协议更省，但把语义塞回前端，违背“前端傻”原则）

建议选 1，并在 §6 和 §14.1 各加一条。否则这是上线后第一个会遇到的挂起 bug。

### 3.2 delta 乱序暂存的内存上界未定义（§9.3）

“收到 `seq > lastSeq + 1` 时暂存并重连补齐”——暂存 buffer 没有大小/超时上界。高频 delta 下若中间一帧永久丢失，buffer 会无界增长。建议明确：暂存超过 N 帧或 T 毫秒未补齐，直接触发 Snapshot 重拉（§9.3 已有“补齐失败则等待新 Snapshot”，但缺触发阈值）。

### 3.3 open 携带 `patch` 时 reduce 的初始态语义（§5.1 / §8）

`BlockOpenFrame` 有可选 `patch`，同时 kind module 有 `decodeInitial` 和 `reduce`。文档没说 open 的 `patch` 走 `decodeInitial` 还是 `decodeInitial(空) + reduce(patch)`。§5.3 示例 `open ... kind=text patch="你好"` 把 patch 当初始内容，但 delta 也用 patch 追加——同一字段两种语义。建议明确：**open.patch 走 `decodeInitial`，delta.patch 走 `decodePatch + reduce`**，并在 §8 BlockModule 注释里写死，避免 kind 作者混淆。

### 3.4 SSE comment heartbeat 与断线检测（§9.4）

heartbeat 改用 SSE comment（`: heartbeat`）很干净，但要确认前端 EventSource / fetch reader 能观测到 comment 以刷新“连接存活”判断。原生 `EventSource` 不会把 comment 暴露给 `onmessage`,若前端靠应用层帧判活会误判超时断线。建议 §9.4 补一句：前端以传输层字节到达（含 comment）判活，不以 RuntimeFrame 判活。

### 3.5 单领域 projector 的公共 helper 边界（§11.1）

“通用 helper 只能处理 ID、时间、脱敏、大小限制和 frame builder”方向对，但“脱敏”本身是最容易长出领域分支的地方（不同 kind 敏感字段不同）。建议明确：脱敏策略按 kind 声明式配置（字段白名单），公共 helper 只执行配置，不写 `if kind == "tool"` 这类分支。否则跨领域 projector 会以“公共脱敏 helper”的形式复活。

## 4. 确认成立、无需改动

- 三层分离（Domain / Trace / Projection Event）+ Block.kind 决定 renderer，是整套设计的正确内核。
- open/delta/close + Snapshot + done 五种固定形状，是比 mask 版更彻底的收敛。
- Snapshot 作为唯一 reset/hydrate 机制，删掉应用层 reset frame，恢复语义更简单（§9.3）。
- §7 Snapshot 只存 `order + blocksById`，childrenByParent 派生，避免双向索引漂移，正确。
- §8.1 reducer 纯函数约束、§12 容量与安全限制、§13 原子切换路线，均可执行。
- `frame.seq === frame.snapshot.lastSeq` 一致性约束正确；§11.2 已用同事务保证 seq/replay/last_domain_ordinal，命中策略时 Snapshot/snapshot_seq 也原子提交——**实现时最易出竞态，Phase 1 优先写并发测试**。

## 5. 初审一句话总结

协议设计已达标：前端只认生命周期，加功能只加 kind。初审剩余风险集中在 close 级联、恢复 buffer、patch 解码、heartbeat 可观测性和按 kind 脱敏边界。

## 6. 复核结果

主设计已完成以下补强：

- `failed/cancelled` 父 Block 先对子树级联 `close(cancelled)`，再关闭父节点；正常 `completed` 仍要求所有 descendant 已关闭。
- seq gap buffer 增加最大帧数、最大字节数和最大等待时间，超限或补齐失败直接回 Snapshot。
- 固定 `open.patch -> decodeInitial`、`delta/close.patch -> decodePatch + reduce` 的解码顺序。
- 明确 heartbeat comment 只能通过底层字节到达或 `fetch reader` 参与连接存活检测。
- 脱敏策略由 per-kind 白名单声明，公共 helper 不得出现领域分支。
- SSE 契约明确区分 Event Stream V3、DisplaySnapshot V4 和迁移前 Runtime Event；兼容事件家族仅作为删除前基线。

进入 Phase 1 前，必须把以上内容转成后端 coordinator、projection writer、stream service 以及前端 stream adapter 的测试，不得只停留在文档层。

## 7. 全链路复核发现（已按 §9 处理）

本轮新增内容集中在 §4.4、§11.2–11.4。协议层已定型不再有意见；以下 3 项是新链路引入、且 Phase 1 前必须决策的问题，按严重度排序。

### 7.1 缺少跨 aggregate 的全局领域顺序——最关键（§4.4 / §11.3）

这是全链路里最容易出隐蔽 bug 的点。

- §4.4 用 `(aggregate_type, aggregate_id, revision)` 保证**单个 aggregate 内**的顺序。
- 但 Block Tree 是**跨 aggregate**的：`message`（run aggregate）、`tool`（tool aggregate）、`plan`（plan aggregate）投影到同一棵树，且有父子依赖（tool 的 `open` 必须在其父 message `open` 之后）。
- projector 又是**有状态**的:§11.4 明说“projector 根据当前 Snapshot 产出 Frame”。有状态 + 跨 aggregate,就要求领域事件有一个**全局单调处理序**,否则 tool.open 可能先于 message.open 被投影,parent 不存在直接被 coordinator 拒绝。

`projection_checkpoints` 目前是 per-projector（`last_domain_event_id, last_domain_revision`），它只能保证单 projector 幂等，**不能定义多个 projector 之间谁先谁后**。

建议：`chat_domain_events` 增加一个 run 内单调递增的 `stream_ordinal`（或直接用自增主键），投影按 `stream_ordinal` 全局串行消费，checkpoint 记录“已消费到的全局 ordinal”，而不是 per-projector 各记各的。这样父子顺序由领域写入序天然保证，coordinator 不会因乱序误拒。**不加这条，父子 open 乱序是上线必现问题。**

### 7.2 每事件全量重写 Snapshot 的写放大（§4.4 表 / §11.3）

`projection_streams.snapshot_json` 是整棵树的 JSON blob。§11.3 的提交流程在**每条 Domain Event** 都“更新 Snapshot”。若每次都重写整块 blob：

- 一次 Run 有 N 条 delta，Snapshot 从小长到大，累计写入是 O(N²)。
- 高频 text delta（文档自己列为重点场景）会把这条放大到最痛。

建议明确：Snapshot 是**周期性 checkpoint**（每 K 帧或每 T 秒或 close 时落一次），实时链路只 append `projection_frames`；恢复 = 最近 Snapshot + 其后的 frame replay。§7 已有 `lastSeq` 水位,天然支持这个模型。否则 `projection_frames`（append-only,便宜）和 `snapshot_json`（每帧全量重写,昂贵）的成本没拉开,等于没分表。这点主设计**没写清落 Snapshot 的频率**,需要补。

### 7.3 `run.completed` 被拒后的收敛路径未定义（§11.4 表）

§11.4 规定 `run.completed` 时“校验所有 descendant 已 close，否则拒绝完成投影”。问题：**拒绝之后呢?**

- 若上游顺序正确（见 7.1），到 run.completed 时子节点本应都已 close，拒绝几乎不会触发。
- 但一旦触发（某 tool 因 bug 没发 close），run 就卡在无法 completed、又没有 done、前端永远转圈。

`failed/cancelled` 有 §6 规则 7 的级联 close 兜底,但 `completed` 这条**没有兜底**。建议二选一并写入文档:
1. `completed` 也允许对残留 descendant 做级联 close（但要区分:completed 下残留 child 更像 bug，级联成 completed 还是 failed？语义要定）。
2. 保持拒绝,但定义超时后转 `run.failed` 走 7-规则级联,保证一定收口。

推荐 2:completed 严格,超时降级为 failed,保证 stream 一定 done,不留挂起态。

### 7.4 次要项（记录,不阻塞）

- **checkpoint 与 frame 事务的原子性表述**:§11.3 把“更新 projection_streams 与 projector checkpoint”和“写 projection_frames、分配 seq”放同一事务,正确。但要确认 `projection_checkpoints` 的 last_domain_event_id 推进也在这同一事务里——文档列在流程内,实现时别拆出去,否则崩溃恢复会重复投影同一 Domain Event 产生重复 seq。§14.4 已有相关用例,保持。
- **approval Block 跨 resume 的存活**:§11.4 `interaction.waiting -> open/delta(approval)`、`interaction.resumed -> close(approval)`。因为是同一 runId 单 stream,resume 恢复同一棵树,approval Block 能延续,成立。仅需确认 resume 不新起 stream key、不重置 seq。

## 8. 全链路复核初始结论

协议层已定型,达成“前端只认生命周期、加功能只加 kind”。后端全链路方向正确（事实/投影/传输三段分离、双事务幂等衔接),但有一个**架构级必答题**和两个**收敛路径必答题**:

1. §7.1 全局领域顺序——不解决,父子 open 乱序上线必现。**Phase 1 硬性前置。**
2. §7.2 Snapshot 落盘频率——不明确,高频 delta 下写放大 O(N²)。
3. §7.3 completed 被拒后的收敛——不定义,残留 child 会让 run 永久挂起。

这三项都属于“文档没写死、实现时各写各的就会踩”的类型,建议在 §4.4/§11.3/§11.4 就地补齐后再进 Phase 1。

## 9. 全链路复核处理结果

主设计已对 §7 的问题作出明确决策：

1. `chat_domain_events` 增加 Run stream 内全局 `stream_ordinal`；同一 stream 由单一带锁/fencing 的 `ProjectionRunner` 串行消费，`projection_streams.last_domain_ordinal` 是唯一热路径 checkpoint。per-kind projector 不再拥有独立消费游标。
2. `projection_streams` 区分 `last_seq` 与 `snapshot_seq`。实时事务只 append Frame 并推进水位，Snapshot 按 128 Frame、2 秒、stream done、retention/schema 操作周期落盘；恢复使用 Snapshot + tail replay，避免每个 delta 全量重写 JSON。
3. Domain service 在 append `run.completed` 前执行有界 completion barrier；超时直接 append `run.failed`。若已经提交的 completed 在投影时仍遇到 open descendant，先全量重建；仍不一致时将异常 child `close(failed)`、根 Block 保持 completed 并 done，同时记录 `projection.invariant_violation`，不得反向修改不可逆 Run 终态。
4. resume 明确复用原 `stream_key`、`last_domain_ordinal` 和 seq 空间；全局领域 checkpoint 与 Frame 在同一投影事务推进，零 Frame 事件也只消费一次。

对应测试已加入主设计 §14：跨 aggregate 顺序、双 runner fencing、周期 Snapshot + tail replay、retention 边界、completion barrier、全量重建和应急收口。设计层已具备进入 Phase 1 的条件，实施仍需先完成这些测试再接生产 Chat。
