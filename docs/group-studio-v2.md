# 群聊工作室 V2：使用者组的群

> 状态：方案草案，待按本文实施。更新时间：2026-09-16。
>
> 当前在线实现仍以 `docs/architecture-overview.md`「群聊工作室」和 `docs/group-studio-event-contract.md` 为准。本文是下一版产品模型，不是现行事实。
>
> 改造原则：**房间留下，主持流程拆掉。** 不整包重写 `group_studio/`，重写调度与阶段机。

## 1. 为什么难用

现行群聊把三套流程叠在一起：Intake 对谈、主持人控场、任务看板。使用者体感是：

1. 想开始还要点「确认并开始」，像过门禁。
2. 不能先组班：拉人 / 踢人是流程附属，不是进群就能做的主操作。
3. 每一轮都回到主持人，时间线变成「人类 ↔ 主持人」一问一答，工人要等被点名。

这和「Agent 群聊」的直觉相反。第一版要做成普通群：你组人、你说话、点到谁谁回。

## 2. 第一版目标

群聊是跨多轮的多人房间，不是一次性 TeamRun，也不替换 Chat Team 模式。

使用者路径：

```text
进群 → 拉人 / 踢人 → 打第一句 → 被点到的 Agent 接力说完 → 等你
```

四条产品规则：

1. **先进人，再说话。** 进群即可拉人、踢人，不经过确认开会。
2. **你发的第一句就是开会。** 没有 drafting、没有开始按钮、没有确认短语。
3. **点到谁谁说，说完停。** `@成员` 只让被点到的人按顺序各说一轮；没有 @ 则按当前成员加入顺序各说一轮；全部说完停，等人类。
4. **没有阶段。** 没有 discussing / executing / reviewing / closing。任务卡若保留，只是置顶备注，不挡发言、不驱动状态。

群里**没有「主持人」角色**。需要控场的是人类：再 @ 一个人、补一句、或把人移出。后台可以保证同一时刻只有一个人在生成，但时间线上不得出现「主持人：请确认目标」。

## 3. 非目标（第一版不做）

- 不恢复可见 Host、`group-studio-host` 进群发言。
- 不做 Intake / `awaitingIntakeConfirm` / 确认短语 / 确认按钮。
- 不做工作项、认领轮、验收 accept/rework。
- 不做 Goal 变更强制复述（`awaiting_human`）。
- 不做真并行 Worker（仍 `maxParallelWorkers=1`）。
- 不做时间旅行、群事件进 Trace 页。
- 不把群聊并回 `POST /api/chat/send` 或 DisplaySnapshot V4。
- 不把群聊做成一次性 TeamRun。Team 仍是 Chat 里「这一轮组队做完」。

## 4. 改造边界

### 4.1 留下（壳）

- Chat 顶栏 / 快捷键进入独立 pane，一 Chat session 一间未归档房间。
- SQLite `group_studios` / `group_memberships` / `group_messages` / `group_runs` / `group_studio_events` / 产物目录。
- 独立 SSE 与 `GET /{id}` 快照恢复。事件名能复用的继续复用，删除不再存在的类型。
- 串行硬规则：同时只有一条 `GroupRun`。
- 拉人 / 踢人 / 静音 / 人类插话取消当前生成。
- Workspace / Project / 模型配置 / Worker 工具与权限边界。

### 4.2 拆掉（核）

- 阶段机：`drafting | discussing | executing | reviewing | awaiting_human | closing`。
- 可见 Host 成员、`host_runtime` 对群发言、Host 控场工具（`group_request_intake_confirm`、`group_assign_floor`、`group_start_execution` / `review` / `propose_close`、`group_publish_work_items`、`group_review_work_item`）。
- `confirm-intake`、intake 短语、本地空白草稿魔术（进群就落库一间 `open` 房间）。
- `group_work_items` 认领 / 验收流程（表可留空，第一版读写关闭）。
- Host 回合结束自动 `schedule_host_turn`、队列耗尽再派 Host。

### 4.3 重写（调度）

用「发言轮」替换「主持流程」：

```text
人类消息（可带 mentions[]）
  → 取消当前生成
  → 写入时间线
  → 计算本轮 speaker 队列
  → 串行跑队列中的 Worker
  → 队列空则停，不派任何系统角色
```

队列规则：

| 人类输入 | 本轮谁说 |
| --- | --- |
| `@A` 或 mentions=`[A]` | 仅 A |
| `@A @B` | A 然后 B，加入顺序或 mentions 顺序取一种并写死：**mentions 数组顺序** |
| 无 @ | 当前未静音、未移除的 Worker，按 `joinedAt` 升序各一轮 |
| 房间里没有 Worker | 只落人类消息，提示去拉人，不造 Host |

静音者不能进队列。踢出进行中的人：取消其 Run，从队列拿掉。人类再发一条：取消当前队列，按新消息重算。

没有「综合 Host」。需要总结时，使用者 @ 某个成员说「汇总一下」，或自己说。

## 5. 状态

房间只留三个使用者看得到的状态：

```text
open → paused → open
open → done
```

| 状态 | 含义 |
| --- | --- |
| `open` | 可拉人、踢人、说话、成员接力 |
| `paused` | 人类暂停或进程重启对账；消息可先入列，恢复后再跑队列 |
| `done` | 人类结束房间；只读，可从列表再打开看历史 |

内部仍可有 `deleted`（列表隐藏）。不再使用 `failed` 作为产品状态；Run 失败记在该条消息 / `GROUP_ERROR`，房间留在 `open`。

熔断（轮次过多）→ `paused`，文案说明「停一下」，恢复按钮回到 `open`。不做单独的 `circuit_broken` 对外状态。

任务卡：

- 可选置顶：`title` / `goal` / `notes`。
- 空 Goal 不阻止发言。
- 改 Goal 只更新置顶，不切状态、不要求复述。

## 6. 成员与入口

- 进群立刻创建 `open` 房间（绑定当前 Chat session），右侧就是成员列表。
- 创建时把当前 Chat 选中的 Agent 加为第一个 Worker；使用者可立刻再拉人。
- `default` 与 Job Agent 仍不可拉；人数硬顶仍 6（只计 Worker + 人类，不再为 Host 占席）。
- Chat 上一句若存在，可作为时间线第一条人类消息并按「无 @」触发一轮；**输入框未发送的草稿不再丢掉，也不再被历史消息覆盖**——有草稿就用草稿，没有才用 Chat 上一句。
- 没有「确认开始」。Composer 一直可发。

## 7. 前端

第一版界面按普通群聊收：

- 中间时间线：人类 + Worker 气泡。没有主持人气泡。
- 右侧：成员（在线/生成中/静音）、拉人、踢人；可选置顶 Goal。
- 底部：输入框 + @ 成员。没有 Agent/Team 分段，没有「确认并开始」。
- 顶栏：暂停 / 继续 / 结束。没有阶段标签（讨论中、执行中、验收中）。

SSE：继续独立通道。第一版前端必须：

- 用 `afterSeq` 续推，断线重连。
- 以当前 `groupId` 订阅，切换房间先卸旧连接。
- 历史 `GROUP_ERROR` 不重复 toast。

事件契约在实施第一刀前改 `docs/group-studio-event-contract.md`。预计删除：`GROUP_INTAKE`、`GROUP_WORK_ITEM`、`GROUP_DOD`（若 DoD 不再驱动）、Host 相关 `GROUP_FLOOR reason=host_*`。保留：`GROUP_STUDIO_STATE`、`GROUP_STATUS`、`GROUP_MEMBER`、`GROUP_MESSAGE_*`、`GROUP_SPEAKER_QUEUE`、`GROUP_INTERRUPT`、`GROUP_ERROR`、`GROUP_RUN_ENDED`。

## 8. 旧房间迁移

启动时一次性把未归档房间收成 V2：

| 旧 status | 新 status |
| --- | --- |
| `drafting` / `discussing` / `executing` / `reviewing` / `awaiting_human` / `closing` | `open` |
| `paused` / `circuit_broken` | `paused`（`phaseBeforePause` 可丢） |
| `archived` | `done` |
| `deleted` | `deleted` |

- Host 成员行标 `removed`，不再出现在成员列表，也不进发言队列。
- 未完成工作项停止读写；列表不展示。
- 旧消息保留，包括历史上的主持人气泡（只读历史，不再新写）。

## 9. 实施顺序

只改群聊，不改 Chat Team。

1. **契约**：先改 `docs/group-studio-event-contract.md` 与本文状态表，再动代码。
2. **房间与成员**：进群即 `open`；拉人/踢人不依赖阶段；去掉确认开始 API 与按钮。
3. **调度**：人类消息 → mentions 队列 → 只跑 Worker → 停。删除 `schedule_host_turn` 自动开口。
4. **卸 Host**：Host 工具与 `host_runtime` 对群发言下线；`group-studio-host` 不再入座。
5. **卸看板与阶段**：工作项 / 复述 / closing 入口与工具下线；前端去掉阶段条。
6. **SSE 恢复**：`afterSeq` + 重连 + 切群换订阅。
7. **迁移**：启动时收旧 status；补测试覆盖「无 @ 全员一轮 / @ 指定 / 无人可说 / 插话重算队列」。

验收（桌面点一次即可）：

- 进群不点开始，先拉两个 Agent，发一句无 @ 的话，两人依次回复后停止，没有主持人插入。
- `@其中一个` 再发一句，只有该人回复。
- 生成中把另一人踢掉，队列不再包含他。
- 刷新后时间线还在，不会把旧错误再 toast 一遍。

## 10. 以后可以加、现在不准加

- 隐形协调者（不进时间线）在没人 @、也没人接话时提醒人类。
- 真并行。
- 工作项看板。
- 群事件进 Trace。
- 跨群目录。

这些写回 `docs/project-todo.md` 即可，不进第一版。
