# Tasks

## 1. 存储与快照

- [x] 1.1 给 `group_studios` 增加 `last_turn_json` / `wave_spoken_json`（`_ensure_column`），`get_snapshot` 输出 `lastTurn`（无记录则为 null）与 `canRerun`；用一条写入后再 GET 的测试证明旧房间缺列时也能读到 null/false
- [x] 1.2 扩展 `GroupStudio` 模型与前端 `GroupStudioSnapshot`：`lastTurn?: { kind: 'assign' | 'all_round'; mentions: string[] } | null`、`canRerun: boolean`；`uv run pytest test/test_group_studio_v2.py -q` 仍能解析 V2 快照

## 2. 工人 handoff

- [x] 2.1 `group_post_message` 增加可选 `nextMemberId`；在 `speaker_queue` 增加 `enqueue_handoff(group_id, from_id, next_id)`：有效则队尾追加并 `GROUP_SPEAKER_QUEUE kind=handoff`，无效返回 false 且不改队列；单测覆盖「A 剩余 B 提名 C → [B,C]」以及自己/已排队/静音被忽略
- [x] 2.2 Worker 回合成功落地正文后调用 enqueue，并把该工人写入 `waveSpoken`；人类 `POST /messages`、停止、插话清空队列且 `waveSpoken=[]`；v2 测试断言时间线无 host 气泡
- [x] 2.3 Worker 提示词补一句「可带 nextMemberId，你不是主持人」；`rg "先读黑板|认领工作项|等待主持人" backend/src/group_studio` 仍无匹配

## 3. 人类再来一轮

- [x] 3.1 `post_human_message` 在写出人类气泡后写入 `lastTurn`（有 mentions 为 assign，否则 all_round）；新增 `rerun_last_turn` + `POST /{id}/rerun`：open、无 Run、队列空、有 lastTurn 才 `build_turn_queue` + `kind=rerun` + `schedule_next`，不插入人类消息
- [x] 3.2 补测试：mentions=[B,C] 跑完后 rerun 队列为当前可跑的 B、C 且消息条数不增；有 currentRunId 或队列未空时 409 且队列不变；无 lastTurn / 无人可跑 409；`paused` 不 rerun
- [x] 3.3 前端输入列「停止」旁加「再来一轮」，仅 `canRerun` 可点；`api.ts` 增加 `rerunStudio`；`frontend` 下 `./node_modules/.bin/tsc -b` 通过，且没有阶段条/确认开始

## 4. 契约与隔离

- [x] 4.1 更新 `docs/group-studio-event-contract.md`（`GROUP_SPEAKER_QUEUE.kind` 含 `handoff`/`rerun`，快照 `lastTurn`/`canRerun`）和 `docs/group-studio-v2.md` 第 8 节调度；`rg "confirm-intake|可见 Host" docs/group-studio-v2.md` 仍写禁止恢复
- [x] 4.2 `rg "from src.group_studio|import group_studio" backend/src/server/routes/chat/handlers backend/src/server/routes/chat/team_runtime` 无匹配
- [x] 4.3 `cd backend && uv run pytest test/test_group_studio_v2.py test/test_group_studio_pr1.py test/test_group_studio_pr10.py test/test_group_studio_archive_memory.py -q` 全过
