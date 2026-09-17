# Proposal

## Why

群聊工作室 V2 已上线（先进人、说话即开始、@ 谁谁说），但 V1 主持流程仍留在仓库里：死模块、死函数、410 桩、被 skip 的阶段机测试，以及快照里仍带出的黑板 / 工作项 / intake 字段。这些遗留会让后续改调度时误接旧阶段机，也会让旧客户端以为主持流程还活着。V2 第一版已可测，现在适合把核拆干净，而不是继续「先留文件、不要 import」。

## What Changes

- 删除 V1 主持/阶段机实现：`host_runtime.py`、`tools_host.py`、`intake.py`、`workboard.py`、`blackboard.py`，以及 `service.py` 里只服务这些路径的函数。
- 切断 V2 在线路径对上述模块的 import（`sessions.drop_group_tool_contexts`、`worker_runtime` 认领队列、`circuit` 黑板指纹、`record_worker_delivery` 等）。
- 对外快照不再携带 V1 看板字段（`workItems`、`blackboard`、`awaitingIntakeConfirm`、`awaitingRestatement`、`claimRoundMemberIds`、`dodChecklist` 等）。置顶仍走 `group_task_cards` / `pin`。
- **BREAKING（旧客户端）**：`GET /{id}` 与 `GROUP_STUDIO_STATE` 不再保证上述 V1 字段。现网前端 `types.ts` 已不读它们。
- 旧阶段推进 HTTP 入口保持 **410** `illegal_transition`，不改成 404，也不恢复实现：`POST .../confirm-intake`、`confirm-restatement`、`start-execution`、`start-review`。
- 删除整文件 skip 的 V1 测试（`test_group_studio_pr2/4/5/6/7/8/9/12/13/14.py`）。V2 与仍在跑的隔离墙测试留下。
- 不删除 SQLite `group_work_items` / `group_blackboards` 表（历史库仍在）；停止读写工作项，黑板行仅在建房时按现网插入空行，不再作为产品事实源。
- 不恢复可见 Host、intake、阶段机、确认开始、工作项 UI。`pause` / `resume` / `close` / `interrupt` / `group_approvals` 仍是 V2 能力，不在本变更删除。

## Capabilities

### New Capabilities

- `group-studio`: 群聊工作室在线契约。本变更把「V2 房间留下、V1 主持核拆掉」写成可验收要求：状态、快照、旧 API、调度不得再派 Host。

### Modified Capabilities

- （无。仓库尚无主 spec。）

## Impact

- 后端：`backend/src/group_studio/*`、`backend/src/server/routes/group_studio.py`、`backend/src/storage/group_studio_records.py`、相关 pytest。
- 前端：现网已按 V2 快照消费；若 Pydantic 去掉必填 V1 字段，只需确认 `types.ts` / reducer 不回归读取。
- 文档：`docs/group-studio-v2.md` 第 18 节从「可先留文件」改为「文件已删除」；事件契约保持「忽略历史 V1 事件名」。
- 不改 Chat Team Runtime、`POST /api/chat/send`、产品档 `groupStudio`。
- 不删远程旧分支；不迁出 `group-studio-host` 哨兵常量（`require_agent` 仍跳过）。
