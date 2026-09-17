# Tasks

## 1. 契约与快照收口

- [ ] 1.1 把 `GroupStudioStatus` 收成 `open | paused | done | deleted`，并从 `GroupStudio` / 相关请求模型去掉 `workItems`、`blackboard`、`awaitingIntakeConfirm`、`awaitingRestatement`、`claimRoundMemberIds`、`dodChecklist` 等 V1 必填字段；用 `uv run pytest backend/test/test_group_studio_v2.py -q` 确认模型仍能解析 V2 快照
- [ ] 1.2 改 `get_snapshot`：有 studio + task_card 即可返回，不再 SELECT 工作项，返回 dict 不含 1.1 所列字段；写一条「库内 status=discussing 时 GET 为 open」的测试并跑过
- [ ] 1.3 确认 `routes/group_studio.py` 四条旧阶段入口仍是 `_gone()` 410 `illegal_transition`，不调用 `service.confirm_intake` 等；在 `test_group_studio_v2.py` 为四条路径各补一测（房间状态不变）

## 2. 切断在线 import 后删除 V1 模块

- [ ] 2.1 从 `worker_runtime` 去掉 `duty=claim`、workboard 认领队列和 `record_worker_delivery`；Worker `kind` 不再按 `executing` 分支；`rg "workboard|record_worker_delivery|duty=.claim" backend/src/group_studio` 无匹配
- [ ] 2.2 改 `circuit.progress_fingerprint` 只使用可见工人正文 / pin / 产物，不再读 blackboard 或 DoD；相关熔断测试或 v2 测试仍通过
- [ ] 2.3 `sessions.drop_group_tool_contexts` 只清 Worker 上下文；删除 `scheduler.schedule_host_turn` 并确认 `service` 无调用；`rg "schedule_host_turn|tools_host|host_runtime|intake|blackboard" backend/src/group_studio` 无在线引用
- [ ] 2.4 从 `service.py` 按符号删除 `request_intake_confirm`、`confirm_intake`、`confirm_restatement`、`prepare_start_execution`、`prepare_start_review`、`propose_close`、`patch_blackboard`、`record_worker_delivery`、`_dod_complete` 及黑板辅助函数；保留 `pause_studio` / `resume_studio` / `close_studio` / `interrupt_run` / `decide_studio_approval`
- [ ] 2.5 删除 `host_runtime.py`、`tools_host.py`、`intake.py`、`workboard.py`、`blackboard.py`；`python -c "import src.group_studio.host_runtime"` 失败，且 `uv run pytest backend/test/test_group_studio_v2.py backend/test/test_group_studio_pr1.py backend/test/test_group_studio_pr10.py backend/test/test_group_studio_archive_memory.py -q` 通过

## 3. 测试与前端回归

- [ ] 3.1 删除整文件 skip 的 `test_group_studio_pr2/4/5/6/7/8/9/12/13/14.py`；`ls backend/test/test_group_studio_pr*.py` 只剩 pr1 与 pr10
- [ ] 3.2 修正 pr1 / pr10 里仍断言 drafting 或 Host 入座的用例，改为 V2（加 host 哨兵失败、快照无看板字段）；两份测试通过
- [ ] 3.3 在 v2 测试补「空 mentions 只跑工人、时间线无主持人」或确认现有用例已覆盖；`rg "group-studio-host" backend/test/test_group_studio_v2.py` 能看到拒绝入座断言
- [ ] 3.4 核对前端 `types.ts` / stream reducer 不含工作项与黑板渲染，历史 `GROUP_BLACKBOARD` 被忽略；`frontend` 下 `./node_modules/.bin/tsc -b` 通过，且群聊页没有阶段条 / 确认开始 / 工作项列表

## 4. 文档与隔离墙

- [ ] 4.1 更新 `docs/group-studio-v2.md` §18 与 `backend/src/group_studio/__init__.py`：写明 V1 文件已删除，而不是「可先留文件」；`docs/group-studio-event-contract.md` 仍列出必须忽略的历史事件名
- [ ] 4.2 确认 Chat handlers / Team Runtime 未新增 `group_studio` import（`rg "from src.group_studio|import group_studio" backend/src/server/routes/chat backend/src/server/routes/chat/team_runtime` 无匹配）
- [ ] 4.3 全量群聊相关测试再跑一遍：`cd backend && uv run pytest test/test_group_studio_v2.py test/test_group_studio_pr1.py test/test_group_studio_pr10.py test/test_group_studio_archive_memory.py -q`
