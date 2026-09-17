# 群聊工作室 V2：使用者组的群

> 状态：第一版已落地。更新时间：2026-09-16。
>
> 现行事实以本文、`docs/architecture-overview.md`「群聊工作室」和 `docs/group-studio-event-contract.md` 为准。不要恢复 Host / intake / 阶段机。
>
> 改造原则：**房间留下，主持流程拆掉。** 不整包重写 `group_studio/`，重写调度与阶段机。

锁定决策若被推翻，先改本节对应条目，禁止实施时另做一套。

| ID | 锁定值 |
| --- | --- |
| D1 | 时间线没有主持人。`group-studio-host` 不再入座、不对群发言 |
| D2 | 新建 / 进群先空白画布，不进列表；**第一条消息才 from-chat 落库为 `open`**。进群不自动发言、不自动开跑 |
| D3 | 人类点发送才开会。没有确认开始、没有确认短语、没有 drafting |
| D4 | 发言队列只看请求体 `mentions[]`（`membershipId`）。后端不解析正文里的 `@名字` |
| D5 | `mentions` 非空：按数组顺序、去重、丢掉不可跑席位后串行各说一轮；若一个都没有 → 只落人类消息，发 `no_eligible_speaker`，**不**回退成全员一轮 |
| D6 | `mentions` 为空：当前未静音、未移除、未崩溃的 Worker 按 `joinedAt` 升序各说一轮 |
| D7 | 房间状态只对外 `open \| paused \| done`，内部另有 `deleted` |
| D8 | 人数硬顶 = **6 个 Worker 席位**。人类不是 Membership |
| D9 | 同时只有一条 `GroupRun`。说完队列就停，不派任何系统角色 |
| D10 | 置顶备注可选，空 Goal 不挡发言，改置顶不切状态 |
| D11 | 第一版不做黑板、工作项、DoD、验收、复述、真并行、时间旅行、群事件进 Trace |

---

## 1. 产品要解决什么

现行群聊叠了三套流程：Intake 对谈、主持人控场、任务看板。使用者体感是：

1. 想开始还要点「确认并开始」。
2. 不能先组班：拉人 / 踢人是流程附属。
3. 每一轮都回到主持人，时间线变成「人类 ↔ 主持人」。

第一版做成普通群：

```text
进群 → 拉人 / 踢人 → 打一句 → 被点到的人接力说完 → 停，等你
```

群里没有「主持人」角色。需要控场的是人类：再 @ 一个人、补一句、把人静音或移出。后台保证同一时刻只有一个人在生成，时间线上不得出现「主持人：请确认目标」。

群聊仍是跨多轮的多人房间，不是一次性 TeamRun，也不替换 Chat Team 模式。不共用 `POST /api/chat/send`、Chat SSE、Mailbox、DisplaySnapshot V4。

---

## 2. 使用者路径

### 2.1 新开一间

1. 当前 Chat 没有进行中的 Run（含 `waiting_*`）。否则顶栏入口禁用，后端 `409 chat_run_active`。
2. 点顶栏群聊、`⌘⇧G` / `Ctrl+Shift+G`，或群聊列表「新建」。
3. 同一 Chat session 若已有 `open` / `paused` 房间，打开那一间，不新建。
4. 否则只进入空白画布：**不调用 from-chat、不进左侧列表**。可先本地拉人、改置顶、打字。
5. 若当前选中的 Agent 可拉（普通 Chat Agent，不是 `default` / Job / `group-studio-host`），先出现在右侧（仍未落库）。
6. **发送第一条消息**才 `POST /from-chat` 落库为 `open`，再把本地成员 / 置顶写上去，然后 `POST /messages`。按第 8 节算队列。
7. 左侧「新建」与 Chat 一样：点了只切到空白画布，不产生新对话。

### 2.2 回到已有房间

同一 Chat session 若已有 `open` 或 `paused` 房间，入口打开那一间，不新建。`done` 不占用这个唯一约束：结束后可以再开一间新的。

### 2.3 Composer 预填（不发送）

| 进群时输入框 | 行为 |
| --- | --- |
| Chat 输入框已有未发送草稿 | 把草稿带到群聊 Composer，**不发送、不开跑** |
| Chat 输入框为空，且最近一条 Chat 是用户消息 | 把该句预填进 Composer，**不发送**。使用者可改、可清、可先拉人再发 |
| 两者都没有 | Composer 空着 |

禁止：进群把 Chat 上一句写成置顶 Goal；禁止用 `gstu_local_*` 假 id 打 API；禁止 persist 时用 goal 覆盖 `draftText`。未发送的新群聊只在前端画布，不进列表。

### 2.4 之后怎么控场

- 想全员各说一轮：直接发，不要 @。
- 想指定人：用 @ 选择器（写入 `mentions[]`）再发。
- 想打断：再发一条（取消当前、按新消息重算队列）；或点输入列「停止」（取消当前、清空队列、房间仍 `open`）。
- 没有顶栏暂停 / 继续 / 结束。熔断或启动对账把房间标 `paused` 时，再发一条会自动恢复并按新消息开跑。
- 想从列表拿掉：左侧删除。房间标 `deleted`，不再出现。

---

## 3. 非目标（第一版禁止做）

- 可见 Host、`group-studio-host` 进群发言或控场工具。
- Intake / `awaitingIntakeConfirm` / 确认短语 / 确认按钮 / `POST /confirm-intake`。
- 工作项、认领轮、验收 accept/rework、`group_work_items` 读写。
- 黑板 consensus 写入、DoD 勾选驱动、Goal 变更强制复述（`awaiting_human`）。
- 真并行（`maxParallelWorkers` 仍恒为 1）。
- 时间旅行、群事件进 Trace 页。
- 把群聊并回 `POST /api/chat/send` 或 DisplaySnapshot V4。
- 把群聊做成一次性 TeamRun。Team 仍是 Chat 里「这一轮组队做完」。
- 隐形协调者在没人接话时自动提醒（以后可以加，不进第一版）。
- 后端根据正文 `@张三` 猜成员。
- `@全体` 特殊语法。无 `mentions` 已经是全员一轮。

---

## 4. 改造边界

### 4.1 留下（壳）

- Chat 顶栏 / 快捷键，520ms 重叠过渡，独立 pane。不改 `WorkspaceMode`，不进 ChatComposer Agent/Team 分段。
- 一 Chat session 最多一间未结束（`open`/`paused`）房间。
- SQLite：`group_studios`、`group_memberships`、`group_messages`、`group_runs`、`group_studio_events`、`group_approvals`、`group_artifacts`、`group_task_cards`（降为置顶备注）。
- 产物目录 `{project_root}/.agent-workstation/group-studios/{group_id}/`。
- 独立 SSE、`GET /{id}` 快照、`afterSeq` 续推。
- 串行：`currentRunId` 非空时不得再开第二条 Run。
- 拉人 / 踢人 / 静音 / 插话取消 / 暂停 / 恢复 / 结束。
- Workspace / Project / `modelConfigId` / Worker 工具与权限 / 上下文治理 / 通知通道。
- 产品档 `surfaces.groupStudio` + `json_store system_settings/group_studio`。

### 4.2 拆掉（核）

- 阶段：`drafting | discussing | executing | reviewing | awaiting_human | closing | circuit_broken | archived | failed`。
- `host_runtime.py`、`tools_host.py`、`intake.py`、`workboard.py` 的在线读写。
- Host 成员行作为活席位。
- 确认开会 API、确认短语、复述确认 API、`start-execution` / `start-review`。
- Host 回合结束自动 `schedule_host_turn`；队列耗尽再派综合 Host。
- `GROUP_INTAKE`、`GROUP_WORK_ITEM`、`GROUP_DOD`、`GROUP_BLACKBOARD` 新事件。
- 前端：确认并开始、阶段条、工作项列表、DoD 弹层、黑板弹层、用 `gstu_local_*` 假 id 打 API。

### 4.3 重写（调度）

`scheduler.py` + `speaker_queue.py` + 人类 `POST /messages`：

```text
人类消息（mentions[]）
  → 若 paused：先回到 open
  → 取消当前 GroupRun（若有）
  → 清空旧队列
  → 写入人类消息
  → 计算本轮 speaker 队列
  → 串行跑队列中的 Worker
  → 队列空则停，不派系统角色
```

---

## 5. 领域对象

三个对外 ID 仍不得互冒：`chat_session_id`、`group_id`、`membership_id`。

### 5.1 GroupStudio

| 字段 | 说明 |
| --- | --- |
| `id` | `gstu_` |
| `projectId` / `workspaceId` | 创建时冻结，不得跨 Project |
| `chatSessionId` | 绑定的 Chat session |
| `modelConfigId` | 创建时记录 Chat 所选配置。无 Run 且 `open` 时可改；有 Run 时 409 |
| `title` | 人类可改；默认取首条人类消息前 40 字或 Chat 会话标题 |
| `status` | `open` / `paused` / `done` / `deleted` |
| `pauseReason` | `null` / `user` / `startup` / `circuit`。只在 `paused` 有值 |
| `currentRunId` | 当前生成；没有则为 `null` |
| `speakerQueue` | 剩余 `membershipId[]` |
| `pendingSpeakerMemberId` | 队列下一位，等于 `speakerQueue[0]` 或 `null`。不是正在说话的人 |
| `currentSpeakerMemberId` | 当前 Run 的发言工人；无 Run 时 `null` |
| `pin` | 置顶备注，见 5.2 |
| `members` | 只返回 `status != removed` 的 Worker；Host 历史行不出现 |
| `messages` | 快照默认最近 100 条 `visibleInTimeline=true` |
| `currentApproval` | 兜底审批，没有则为 `null` |
| `budget` | 见第 12 节 |
| `createdAt` / `updatedAt` | ISO-8601 UTC |

删除这些快照字段（不再下发，前端不得再读）：

`phaseBeforePause`、`awaitingIntakeConfirm`、`awaitingRestatement`、`claimRoundMemberIds`、`workItems`、`blackboard`、`taskCard`（改名为 `pin`，见下）、`currentTaskCardId` 对外可保留内部用。

列表项：`id, title, status, projectId, workspaceId, chatSessionId, updatedAt`。`deleted` 不进列表。`done` 进列表。

### 5.2 Pin（原 TaskCard）

一群一张，只是置顶备注。

| 字段 | 说明 |
| --- | --- |
| `title` | 短标题，可空 |
| `goal` | 可空。空不挡发言 |
| `notes` | 可空。原 `background` + `constraints` 可迁到这里一段文本 |
| `updatedAt` | |

不再使用：`dodChecklist`、`status=draft/active/frozen_for_close`、`seedFromChat`、`revision` 对外。库表可留列，读写层忽略。

人类 `PUT /pin`（或沿用 `PUT /task-card` 但语义变成改 pin）。工人没有改 pin 的工具。改 pin：**不取消**当前生成，**不**重算队列，**不**切状态。

### 5.3 Membership

| 字段 | 说明 |
| --- | --- |
| `id` | `gmbr_` |
| `role` | 恒为 `worker`。新行禁止 `host` |
| `agentId` | 持久 Agent id |
| `displayName` | 展示名 |
| `status` | `active` / `crashed` / `removed` |
| `muted` | 静音者不能进队列、不能被点名 |
| `joinedAt` | 首次入座时间。全员一轮按此升序。同秒用 `id` 升序 |
| `lastSpokeAt` | 上一轮成功发言 |
| `crashCount` | |

人类不是 Membership。人类消息 `speakerId="human"`，`role="human"`。

同一房间同一 `agentId` 只能有一条未 `removed` 的席位。再拉同一个人 → `409 already_member`。

可拉条件（与现网 Agent Workspace 规则一致）：

1. 属于当前 Workspace，或系统默认 Chat Agent（但 `default` 仍不可拉）。
2. `role` 不是 `job`。
3. 不是 `group-studio-host`。
4. 尚未是当前群未移除成员。

`available-agents` 只由后端算，前端不拼绑定缓存。

### 5.4 GroupMessage

| 字段 | 说明 |
| --- | --- |
| `id` | `gmsg_` |
| `runId` | 人类消息为 `null`；工人消息绑这一轮 Run |
| `speakerId` | `human` 或 `membershipId` |
| `role` | `human` / `worker` / `system`。第一版**不再新写** `host` |
| `intent` | 人类：`human_directive`。工人：`discuss`（可忽略细分）。系统：`system_notice` |
| `body` | 对群可见正文 |
| `thinking` | 工人思考，不进 `body` |
| `mentions` | 仅人类消息：本轮点名的 `membershipId[]`（已去重） |
| `visibleInTimeline` | 默认 `true`。系统提示也可见 |
| `createdAt` | |

历史 `role=host` 气泡保留只读，前端按普通成员气泡渲染，名字写「主持人（历史）」，不再用阶段样式。

### 5.5 GroupRun

一次工人生成。状态：`queued | running | waiting_permission | completed | cancelled | failed`。

房间不因单条 Run `failed` 变成失败。失败写该条 `GROUP_MESSAGE_CLOSE outcome=failed` + `GROUP_ERROR`，房间留在 `open`，队列弹下一人继续。

### 5.6 仍保留、第一版不作为产品对象

- `group_blackboards` / `group_work_items`：表可留空，API 与工具关闭，快照不下发。
- `group_approvals`：仅当运行时仍打出 `REQUIRE_USER_CONFIRM` 时兜底，禁止写入 Chat `PENDING_INTERACTIONS`。

---

## 6. 房间状态

```text
（创建）→ open ⇄ paused
         open → done
         任一未 deleted → deleted
```

| 状态 | 使用者含义 | 可拉人踢人 | 可发送 | 可生成 |
| --- | --- | --- | --- | --- |
| `open` | 正常群 | 是 | 是 | 是 |
| `paused` | 停一下 | 是 | 是（发送即恢复） | 否，直到恢复或发送 |
| `done` | 已结束，只读 | 否 | 否 | 否 |
| `deleted` | 列表隐藏 | 否 | 否 | 否 |

没有 `failed`、`circuit_broken`、`archived` 对外状态。旧 `archived` 迁成 `done`。熔断把房间打成 `paused` 且 `pauseReason=circuit`。

进程重启：未收口 Run 按暂停收口，`pauseReason=startup`，保留当时队列。

`GROUP_STATUS` 的 `from`/`to` 只走上面四态（外加内部 `deleted`）。`reason`：`created | human_pause | human_resume | human_message | circuit | startup | close | delete`。

---

## 7. 入口、开关、模型

### 7.1 开关

两层都开才可用：产品档 `surfaces.groupStudio`，以及 `system_settings/group_studio.enabled`（默认 `true`）。关闭后写/建接口 `404 group_studio_disabled`。已有 Run 收到关闭请求时先收到口再拒绝新 from-chat。删除 Chat session **无论开关**都把绑定的未结束房间标 `done`（或已是 `done` 则不动），`deleted` 会话对应房间一并 `deleted`。

设置页仍暴露 `enabled`、`maxMembers`（2–6）、`maxTurns`（1–200）。`maxParallelWorkers` 只读 1。

### 7.2 升级约束

- Chat 活动 Run（含审批等待）→ `409 chat_run_active`。
- 切换带走 Workspace / Project、Composer 草稿、附件、选中 Agent、模型配置 ID。
- 不搬 Chat 气泡、TeamRun、Plan、AskUser。
- 群聊附件走工作室文件库，不进 Chat 消息区。

### 7.3 模型

创建请求带 `modelConfigId`。各轮 Worker 解析该配置并固定本轮连接。历史空值用文件默认模型。无 Key → 不创建 Run，`GROUP_ERROR code=model_not_configured`，人类消息已落库。

`PATCH /{id}/model`：仅 `open` 且 `currentRunId=null`。`paused` 允许改，恢复后用新配置。`done` 拒绝。

---

## 8. 发言调度（核心）

### 8.1 谁可以进队列

席位必须同时满足：`role=worker`、`status=active`、`muted=false`、成员行仍在本群。

`crashed` / `removed` / 缺失 id / Host 历史行：写入时丢掉，弹出时也丢掉。

### 8.2 从人类消息算队列

输入：`mentions: string[]`（membershipId）、当前成员表。

```text
seen = []
if mentions 非空:
    for id in mentions:          # 保持请求顺序
        if id 可跑 and id not in seen:
            seen.append(id)
    queue = seen
    if queue 为空:
        发 GROUP_ERROR no_eligible_speaker
        不入队、不开跑
else:
    queue = 可跑 Worker 按 joinedAt asc, id asc
    if queue 为空:
        写一条 system_notice「还没有可以发言的成员，请先拉人」
        不入队、不开跑
```

去重只保留第一次出现。人类不能自己进队列。

### 8.3 跑队列

1. `set_queue(queue, kind=assign|all_round)`，发 `GROUP_SPEAKER_QUEUE`。
2. 取队首，发 `GROUP_FLOOR`（`reason=mention` 若来自 mentions，否则 `queue_next`），创建 `GroupRun`，跑 Worker。
3. Run 终态后：从队列弹出该人（若仍在队首），`kind=queue_next`。
4. 还有剩余 → 下一人。空了 → `GROUP_SPEAKER_QUEUE kind=drained`，**停**。不派 Host。
5. `GROUP_RUN_ENDED` 每条 Run 都发。SSE 连接保持。

`drained` 只表示「这一轮点名说完了」。不是综合、不是阶段切换。

### 8.4 插话、停止、暂停、恢复

| 动作 | 当前 Run | 旧队列 | 房间状态 | 接着做什么 |
| --- | --- | --- | --- | --- |
| 人类再发一条 | 取消，`interrupt source=human_message` | 丢弃 | `open`（若原是 paused 也回到 open） | 按新消息重算 |
| 输入列停止 | 取消，`source=stop_button` | 清空 | 仍 `open` | 等下一条 |
| 踢走正在说话的人 | 取消其 Run | 去掉此人 | 不变 | 下一人；没人则 drained |
| 静音正在说话的人 | 取消其 Run | 去掉此人 | 不变 | 同上 |
| 踢走 / 静音队列里还没说的人 | 不动当前 Run | 去掉此人 | 不变 | — |
| 拉人 | 不动当前 Run | **不加进本轮队列** | 不变 | 下一条人类消息才可能点到他 |
| 熔断 | 取消当前 | 清空 | `paused`，`pauseReason=circuit` | 再发一条恢复 |
| 启动对账 | 标取消 | 保留 | `paused`，`pauseReason=startup` | 再发一条恢复 |

暂停期间仍可拉人踢人、改 pin、改模型。暂停期间发送走「人类再发一条」那一行。

`done` / `deleted` 拒绝发送、拒绝开跑，`409 studio_not_open`。

### 8.5 工人失败与崩溃

- Run `failed`：该条消息 `outcome=failed`，`GROUP_ERROR worker_failed`，弹下一人。不把房间标失败。
- 工人进程 / session 丢失：该席位 `crashed`，`crashCount+1`，移出队列，队列继续。不解散房间。
- 不因一人失败重派同一人。要他说：人类再 @ 他。
- 已 `crashed` 的人可以被人类重新拉（新席位）或以后做「恢复席位」；第一版不做一键复活，只允许再拉同一个 Agent（旧行保持 removed/crashed，新行新 id）。若 `already_member` 因 crashed 行挡住：允许对 `crashed` 行改回 `active` 并刷新 `joinedAt`，当作重新入座。锁定：**同一 agentId 的 crashed 行可以 reactivate，removed 行再拉则新建。**

### 8.6 没有「综合」

需要总结时，使用者 @ 某个成员说「汇总一下」，或自己写。系统不自动综合。

---

## 9. 成员操作

硬顶 6 个未移除 Worker。满员再拉 → `409 member_limit`。

| 操作 | open | paused | done |
| --- | --- | --- | --- |
| 拉人 | 是 | 是 | 否 |
| 踢人 | 是 | 是 | 否 |
| 静音 / 取消静音 | 是 | 是 | 否 |

踢人：`status=removed`，从队列拿掉，取消其正在跑的 Run。不物理删行。

静音：切断其后续上下文注入（提示词不再带他的旧长文，只保留时间线里已有气泡）。取消静音不自动入当前队列。

Host 历史行：启动迁移标 `removed`，列表与 available 都不出现。

---

## 10. Worker 运行时

### 10.1 创建与缓存

继续走 `agents/factory.py`，`include_default_tools` 按现网 Worker 装配。工具上下文按 `(group_id, member_id)` 单例，每轮只改 `run_id`。三条 Runtime 仍走 `runtime_governance.py` 同一套 middleware；群聊跨轮才落 `session_context_state`。

### 10.2 工具

工人**只保留**：

- 默认文件 / 检索：Read、Grep、Glob；Write / Edit 仍受权限与 Project root 约束，工作目录是房间产物目录。
- Bash：`GuardedBash` + `WORKER_SHELL_BLACKLIST`（bypass 免疫）。
- 按 Agent 配置 ∩ Project binding 的 MCP / HTTP / Skill / WebFetch。
- `group_post_message`：写出本轮对群可见正文并结束本轮。必做。空 body 拒绝。

工人**删除**：

- 全部 Host 控场工具。
- `group_read_blackboard` / `group_propose_decision` / `group_request_help`。
- 工作项 claim / complete / review。
- `AskUser`、`FinalAnswerStart`、DelegateTask / Plan / ScheduleJob。

权限：`human_approval=false` + `trusted_tools` → `PermissionMode.BYPASS`，不抄 Chat Agent profile。`group_approvals` 仅兜底。禁止写入 Chat `PENDING_INTERACTIONS`。

### 10.3 提示词

每轮注入：

1. 你是群成员 `{displayName}`，不是主持人。说完用 `group_post_message`，不要指挥别人。
2. 置顶备注（可空）。
3. 当前成员表（名字 / 是否静音）。
4. 最近 8 条可见时间线（人类 + 工人 + 系统；含历史主持人气泡）。
5. 本轮原因：被点名，或全员一轮轮到你。
6. 记忆两段：Markdown 摘要按会话冻结；召回查询用 pin.goal，空则用触发本轮的人类消息。

禁止再写「先读黑板」「认领工作项」「等待主持人」。

### 10.4 结束本轮

- 调用了 `group_post_message`：把 `posted_body` 写成可见消息，`outcome=completed`。
- 没调用但模型停了：若有流式可见正文，收成消息 `completed`；否则空消息 `completed` 并计一次无进展。
- 取消：`outcome=cancelled`，已流出的正文保留，标注已打断。

思考只走 `GROUP_MESSAGE_DELTA kind=thinking`，不得写入 `body`。

---

## 11. 时间线与 @

### 11.1 谁出现在时间线

- 人类气泡。
- 工人生气泡（流式）和完成气泡。
- 偶发系统提示（没人可跑、熔断「先停一下」）。
- 历史主持人气泡只读。

工具活动仍用 `GROUP_ACTIVITY_*`，展示在该工人气泡下，不进主时间线当独立说话人。

### 11.2 @ 选择器

Composer 输入 `@` 弹出当前可跑或全部未移除 Worker（静音者可显示但选中后发送会走 D5，若全是静音则 `no_eligible_speaker`）。选中后：

- 正文插入 `@{displayName}` 仅供人类阅读。
- 请求体 `mentions` 追加该 `membershipId`。
- 发送前以 `mentions[]` 为准，不要在前端从正文反解析。

没有选择器、只手打 `@张三`：`mentions=[]`，按全员一轮。文档和 placeholder 写清楚：「请用 @ 菜单点名」。

### 11.3 系统提示文案（写死）

| 场景 | 正文 |
| --- | --- |
| 无 Worker | 还没有可以发言的成员，请先在右侧把人拉进来。 |
| mentions 都不可跑 | 点到的人现在不能发言（已静音、已移出或不在群里）。 |
| 熔断 | 先停一下：这一轮说得有点多。点继续或再发一条就可以接着聊。 |
| 缺模型 Key | 模型还没配好 API Key，这条已经记下，配好后再发一条。 |

系统提示也是 `GroupMessage`，`role=system`。同时发 `GROUP_ERROR` 给前端决定要不要 toast。同一 `code` 的历史事件刷新后不得再 toast。

---

## 12. 预算与熔断

P0 只计工人轮次，不计金额。

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| `maxTurns` | 40（设置可 1–200） | 房间累计工人 Run 次数（completed/failed 都计；因插话/停止/暂停取消的不计） |
| `usedTurns` | 0 | 已计次数 |
| `maxNoProgressTurns` | 3 | 连续空正文工人轮 |
| `noProgressTurns` | 0 | 连续空轮；有正文则清零 |

触顶：取消当前，清空队列，`paused` + `pauseReason=circuit`，系统提示上表文案，`GROUP_CIRCUIT`（`reason=max_turns|no_progress`）。

继续：`usedTurns` 保留；`noProgressTurns` 清零；若是 `max_turns`，`maxTurns` 至少加 20 或沿用请求里的新上限。

drafting 不计费的旧规则删除（没有 drafting）。

---

## 13. 文件、审批、记忆、通知

### 13.1 文件

上传 / 列表 / 下载保留。下载只允许已登记且位于当前 Project 根内的路径。工人 Write 的 base 是房间产物目录，不能用更宽的 session cwd。

### 13.2 审批

`GROUP_APPROVAL` 保留。人类 `POST /approvals` approve/deny。deny 取消该 Run，队列弹下一人。

### 13.3 结束时的记忆

`close` 且 `confirm=true` 之后后台跑归档（失败只写 `MEMORY_AUDIT`，不影响 `done`）：

- 用户侧文本：pin.goal（空则「群聊《title》」）+ 最近 3 条人类消息摘要。
- Agent 侧文本：最近若干条工人可见正文（上限 6000 字），不要 Host 控场词，不要工作项列表。
- 归属第一个 **active 或历史上第一个** Worker 的 `agentId`。禁止 `group-studio-host`。
- 走现有 `evaluate_turn_admission` → 历史节点 + `MemoryReflectionAgent`。user / global 仍进待确认队列。

### 13.4 通知

`kind` 前缀 `group_studio_`，点击 `openGroupStudio`，不进 Chat SSE / ResultModal。第一版通知：工人 Run 失败、熔断、房间被暂停（启动对账）。不要每个工人发言都通知。

---

## 14. HTTP API

前缀 `/api/group-studio`。未列出的旧写接口删除或改成 410。

| 方法 | 路径 | V2 |
| --- | --- | --- |
| `GET` | `/` | 留。列表含 `open/paused/done` |
| `POST` | `/from-chat` | 改。立刻 `open`，无 drafting，不自动开跑。`reuseBlankDraft` 删除 |
| `GET` | `/by-chat/{sessionId}` | 留。只返回未结束；没有则 404 空 |
| `GET` | `/{id}` | 留。快照按 5.1 |
| `GET` | `/{id}/available-agents` | 留 |
| `DELETE` | `/{id}` | 留。标 `deleted` |
| `PATCH` | `/{id}/model` | 留。约束见 7.3 |
| `GET` | `/{id}/messages` | 留。分页 |
| `GET` | `/{id}/events?afterSeq=` | 留。见 16 |
| `POST` | `/{id}/messages` | 改。唯一人类写入口。按第 8 节调度 |
| `POST` | `/{id}/interrupt` | 留。停止，不清成 paused |
| `POST` | `/{id}/pause` | 留 |
| `POST` | `/{id}/resume` | 留 |
| `POST` | `/{id}/members` | 留。不检查阶段 |
| `POST` | `/{id}/members/{mid}/mute` | 留 |
| `POST` | `/{id}/members/{mid}/remove` | 留 |
| `PUT` | `/{id}/pin` | 新（可先复用 `PUT /task-card` 但只写 pin 字段） |
| `POST` | `/{id}/close` | 改。`confirm=true` → `done` + 记忆 |
| `POST` | `/{id}/approvals` | 留 |
| `POST` | `/{id}/files` | 留 |
| `GET` | `/{id}/files` | 留 |
| `GET` | `/{id}/files/{artifactId}/download` | 留 |
| `POST` | `/{id}/confirm-intake` | **删** |
| `POST` | `/{id}/confirm-restatement` | **删** |
| `POST` | `/{id}/start-execution` | **删** |
| `POST` | `/{id}/start-review` | **删** |

`POST /from-chat` 请求：

```text
projectId, chatSessionId, modelConfigId?, agentId?, draftText?, filePaths?, title?
```

删除 `reuseBlankDraft`。`draftText` 只用于预填，服务端**不**把它写成第一条消息。

`POST /{id}/messages`：

```text
projectId, body (1–20000), mentions: membershipId[]
```

`body` 仍必填。纯 @ 没有正文 → `400 empty_body`。

---

## 15. 前端

### 15.1 布局

```text
顶栏：标题、回 Chat、折叠右侧、通知、模型
左：历史房间列表（open / paused / done）
中：时间线（人类 / 工人 / 系统 / 历史主持）
右：成员、拉人、置顶备注、文件
底：Composer + @ 菜单 + 停止。没有「确认并开始」，没有阶段标签，没有顶栏暂停/结束
```

没有 Agent/Team 分段。没有工作项、DoD、黑板入口。

成员行状态：空闲 / 正在说（当前流式消息或 `currentSpeakerMemberId`）/ 静音 / 崩溃。不要用队列队首当「正在说」。

`done`：Composer 隐藏，右侧只读。

### 15.2 SSE（第一版必须做对）

- 订阅 `GET /{groupId}/events?afterSeq=`。
- 缺省 `afterSeq`：先 `GROUP_STUDIO_STATE`，再从持久 seq 续。
- 断线用**已应用到的最大持久 seq**重连，不要从 0 再来一遍。
- 切群：先关旧 EventSource，再订新 `groupId`。禁止两个房间共用一个连接。
- `GROUP_ERROR`：只 toast **本连接期间新到的**事件。快照重放 / 历史 seq 不弹。
- `GROUP_MESSAGE_DELTA` 不落库；刷新靠 `GET /{id}` 的 messages（运行中 body 仍可 256 字 / 200ms upsert）。
- heartbeat 是 comment `: heartbeat`，不进 reducer。
- `GROUP_RUN_ENDED` 不断连。

### 15.3 删除的前端模块

- `localStudio.ts` 与所有 `gstu_local_*`。
- 确认开始按钮与 intake 条。
- `StudioDodModal`、`StudioBlackboardModal`、工作项列表。
- 阶段文案映射（讨论中 / 执行中 / 验收中）。

`StudioGoalModal` 收成「置顶备注」。历史面板继续打开 `done` 房间只读。

消息区继续共用 `ScrollToBottomButton` / `useMessageScroll`。

---

## 16. 事件契约（实施第一刀写入正式契约）

公共信封不变：`seq, groupId, runId, type, status, payload, createdAt`。`status` 改为 V2 四态。对外 JSON 空值 `null`。DELTA 仍不落库。

### 16.1 保留

| type | 持久 | payload |
| --- | --- | --- |
| `GROUP_STUDIO_STATE` | 否 | 完整快照（5.1） |
| `GROUP_STATUS` | 是 | `from`, `to`, `reason`（第 6 节） |
| `GROUP_MEMBER` | 是 | 完整 Membership + `change`: `joined\|muted\|unmuted\|removed\|crashed\|reactivated` |
| `GROUP_FLOOR` | 是 | `memberId`, `runId`, `reason`: `mention\|queue_next`。可选 `remainingMemberIds` |
| `GROUP_MESSAGE_OPEN` | 是 | `messageId`, `speakerId`, `role`, `intent`, `visibleInTimeline` |
| `GROUP_MESSAGE_DELTA` | 否 | `messageId`, `text`；`kind`: `answer`（默认）\| `thinking` |
| `GROUP_MESSAGE_CLOSE` | 是 | `messageId`, `body`, `outcome`: `completed\|cancelled\|failed`；可选 `thinking` |
| `GROUP_ACTIVITY_OPEN/DELTA/CLOSE` | 同现网 | 现网字段 |
| `GROUP_BUDGET` | 是 | 完整 `GroupBudget` |
| `GROUP_CIRCUIT` | 是 | `reason`: `max_turns\|no_progress`；`usedTurns`, `noProgressTurns` |
| `GROUP_INTERRUPT` | 是 | `runId`, `source`: `human_message\|stop_button\|pause` |
| `GROUP_APPROVAL` | 是 | 完整 `GroupApproval` |
| `GROUP_FILE` | 是 | 完整 `GroupArtifact` |
| `GROUP_ERROR` | 是 | `code`, `message` |
| `GROUP_RUN_ENDED` | 是 | `runId`, `status` |
| `GROUP_SPEAKER_QUEUE` | 是 | `memberIds`, `kind`: `assign\|all_round\|queue_next\|interrupt\|drained`，`pendingMemberId` |
| `GROUP_PIN` | 是 | 完整 pin。替代 `GROUP_TASK_CARD` |

`GROUP_SPEAKER_QUEUE.kind=drained`：队列空且本轮不再派人时发一次。停止 / 插话发 `interrupt`，不发 `drained`。

### 16.2 删除（前端再收到必须忽略，服务端不再发）

`GROUP_INTAKE`、`GROUP_WORK_ITEM`、`GROUP_DOD`、`GROUP_BLACKBOARD`、`GROUP_TASK_CARD`（改 `GROUP_PIN`）。

`GROUP_FLOOR.reason` 删除：`host_assign`、`host_default`、`human_return`、`claim_round`。

`GROUP_ERROR.code` 删除：`host_failed`、`host_required`、`intake_*`、`work_item_*`、`floor_not_granted`。

保留并新增的 `GROUP_ERROR.code`：

`model_not_configured`、`worker_failed`、`member_muted`、`not_found`、`worker_session_lost`、`no_eligible_speaker`、`member_limit`、`already_member`、`studio_not_open`、`chat_run_active`、`group_studio_disabled`。

连接首包与刷新恢复规则同现网：不要 `replay=true`。禁止新事件名在未改契约前出现。

---

## 17. 旧房间迁移

启动时一次性、幂等。

| 旧 status | 新 status | pauseReason |
| --- | --- | --- |
| `drafting` `discussing` `executing` `reviewing` `awaiting_human` `closing` `failed` | `open` | `null` |
| `paused` | `paused` | `user` |
| `circuit_broken` | `paused` | `circuit` |
| `archived` | `done` | — |
| `deleted` | `deleted` | — |

同时：

- 所有 `role=host` 成员标 `removed`。
- 清空 `claimRoundMemberIds`；工作项停止读写。
- `speakerQueue` 丢掉 Host id 与不可跑 id。
- 未收口 Run 标 `cancelled`，房间若仍是活动态则 `paused` + `startup`（优先于上表的 `open`）。
- 旧消息、旧 Host 气泡、旧事件行保留。
- 任务卡 Goal/背景写入 pin；DoD 丢弃不展示。

迁移后 `group_studio_has_runs` 一类 intake 标记删除。

---

## 18. 代码落点

只改群聊，不改 Chat Team。

| 模块 | 动作 |
| --- | --- |
| `scheduler.py` / `speaker_queue.py` / `lifecycle.py` | 重写 |
| `service.py` | 第一条消息才 from-chat 落库 open；成员不再看阶段；删 intake/restatement/phase API |
| `worker_runtime.py` / `tools_worker.py` | 瘦提示词与工具 |
| `host_runtime.py` / `tools_host.py` / `intake.py` / `workboard.py` / `blackboard.py` | 已删除，不要恢复 |
| `circuit.py` | 按第 12 节 |
| `archive_memory.py` | 按 13.3 |
| `startup_checks.py` | 第 17 节迁移 |
| `models.py` + 前端 `types.ts` | 状态枚举与快照字段 |
| `routes/group_studio.py` | 第 14 节 |
| `GroupStudioPage` / `StudioContextPanel` / `StudioComposer` / `useGroupStudioStream` / `useGroupStudioEntry` | 第 15 节 |
| `localStudio.ts` 等 | 删除 |

`group-studio-host` 系统 Agent 可以暂时留在代码里，但 `require_agent` 继续跳过，迁移后不再创建 Host 席位。不要从 Agents 页露出。

Chat handlers / Team Runtime 仍禁止 import `group_studio`。catalog 继续通过 `session_studio.py` 徽章。

---

## 19. 实施顺序

1. 把第 16 节写入 `docs/group-studio-event-contract.md`，并改 TypeScript / Pydantic 类型。不要先改调度再改契约。
2. 启动迁移 + 快照字段。旧房间能打开、能看历史。
3. 第一条消息才落库 `open`；可先在画布拉人；删确认开始与 `gstu_local_*` 假 id。
4. 人类消息 → 队列 → 只跑 Worker → 停。
5. 卸 Host 运行时与控场工具。
6. 卸看板 / 黑板 / 阶段条。
7. SSE `afterSeq`、重连、切群换订、历史错误不 toast。
8. 归档记忆改纪要；补第 20 节测试。

---

## 20. 验收与测试

### 20.1 桌面点一遍

1. Chat 输入框先打「先别发」，进群：Composer 里是这句，没有人在说话，没有开始按钮。
2. 拉两个 Agent，再发「你们各自做个自我介绍」（无 @）：两人按加入顺序各一条，然后停。时间线没有新主持人。
3. `@第二个` 再发「只你说」：只有他回。
4. 生成中踢掉下一个还没说的人：他说完就停，被踢的不出现。
5. 生成中再打一句：当前工人停，按新消息重算。
6. 停止：当前停，队列空，房间仍可发。
7. 刷新：时间线还在；旧 `GROUP_ERROR` 不 toast。
8. 左侧删除：从列表消失。

### 20.2 后端单测必须覆盖

- 无 mentions + 两名 Worker → 队列等于 `joinedAt` 顺序。
- mentions `[B, A, B]` → `[B, A]`。
- mentions 全是静音 / 已踢 → 不开跑 + `no_eligible_speaker`。
- 零 Worker → 系统提示，不开跑。
- 插话清空旧队列并按新 mentions 重建。
- 停止清空队列且状态仍 `open`。
- 暂停保留队列；继续从队首跑。
- 踢当前说话人后跑下一人。
- 拉人进当前进行中的一轮不会被追加。
- 工人 failed 不把房间标 `done`/`deleted`，队列继续。
- 启动迁移：`drafting`→`open`，Host 行 removed，`archived`→`done`。
- `confirm-intake` 路由不存在或 410。
- Chat 活动 Run 时 from-chat → 409。

### 20.3 SSE

- 第二次连接带 `afterSeq=最后一条持久 seq` 不得再推一遍旧 ERROR。
- 快速切两个 `groupId`，页面只反映当前房间。

---

## 21. 以后可以加、现在不准加

写回 `docs/project-todo.md` 即可：

- 隐形协调者（不进时间线）在冷场时提醒人类。
- 真并行。
- 工作项看板、黑板、DoD。
- 群事件进 Trace。
- 跨群目录（第一版只有当前 session + 左侧历史列表）。
- 正文 `@名字` 服务端解析。
- 一键复活 crashed 席位以外的更复杂生命周期。

---

## 22. 和现行文档的关系

| 文档 | 关系 |
| --- | --- |
| `docs/architecture-overview.md` | 实施完成前仍是现行阶段机。完成后用本文第 5–13 节替换「群聊工作室」整节 |
| `docs/group-studio-event-contract.md` | 实施第一刀按第 16 节改 |
| `docs/project-todo.md` | 只记未做项，不复制本文 |
| Chat Team / `team-runtime-redesign.md` | 不动 |
