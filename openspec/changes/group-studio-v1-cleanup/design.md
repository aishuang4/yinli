# Design

## Context

V2 调度与产品路径已经切到 `speaker_queue` + `scheduler.schedule_next` + `worker_runtime`。V1 核仍以整文件和 `service.py` 死函数存在；`docs/group-studio-v2.md` 第 18 节当时允许「先留文件」。快照组装仍 JOIN `group_work_items` / `group_blackboards`，Pydantic `GroupStudio` 仍把这些字段当必填。动机见 `proposal.md`。

约束：不改 Chat Team；不恢复 Host / intake / 阶段机；历史 SQLite 不能靠 DROP TABLE 当迁移；旧 HTTP 路径保持 410。

## Goals / Non-Goals

**Goals:**

- V2 在线 import 图不再到达主持/阶段机模块，删除那些文件后测试仍绿。
- 对外 JSON 与前端 `types.ts` 对齐：只暴露 V2 快照。
- 熔断指纹不再依赖黑板 / DoD。
- 文档从「可先留文件」改成「已删除」。

**Non-Goals:**

- 不做真并行、时间旅行、群事件进 Trace。
- 不删 `group_work_items` / `group_blackboards` 表，不做数据回填脚本。
- 不删 `pause` / `resume` / `close` / `interrupt` / `approvals` 路由。
- 不从前端重新露出结束/暂停按钮（产品已收成输入列「停止」）。
- 不清理远程旧分支。

## Decisions

### 1. 删除 V1 模块，而不是留空壳

删除 `host_runtime.py`、`tools_host.py`、`intake.py`、`workboard.py`、`blackboard.py`。`service.py` 同步删掉只被这些路径调用的函数：`request_intake_confirm`、`confirm_intake`、`confirm_restatement`、`prepare_start_execution`、`prepare_start_review`、`propose_close`、`patch_blackboard`、`record_worker_delivery`、`_dod_complete` 及黑板辅助函数。

备选：留空模块只 export 抛错。否决：还会被测试/后续补丁 import。

### 2. 410 桩留在路由层

`routes/group_studio.py` 四个 `_gone()` 入口保留。请求模型 `GroupConfirmIntakeRequest` 等可留最小 body（只要 `projectId`），避免 FastAPI 因缺模型 500。不在 `service` 再留 410 实现。

备选：整段删路由，让框架 404。否决：契约与现测写明 410。

### 3. 快照停止组装看板，建房仍可插空黑板行

`GroupStudioRecordStore.get_snapshot` 不再 SELECT 工作项；`blackboard` / `workItems` / intake 标记不进入返回 dict。`GroupStudio` 模型去掉这些必填字段。`group_task_cards` 继续承载 pin。

建房路径若仍 INSERT 空 `group_blackboards` 行，仅为避免改 schema 与「缺行则 snapshot 为 None」的旧守卫。实现时应改守卫：只要有 studio + task_card 就能出快照，不再要求黑板行。

备选：立刻 ALTER DROP 列/表。否决：开发库与已装包共用演进方式，本项目不用破坏性 DROP。

### 4. Worker 只走 queue，熔断只看可见正文

`run_worker_turn` 去掉 `duty=claim` 与 `get_claim_round` / `pop_claim_head`。`kind` 不再按 `executing` 分支。`circuit.progress_fingerprint` 改为工人可见消息 / pin revision / 产物登记，不再读 blackboard revision 或 DoD checked。

`sessions.drop_group_tool_contexts` 只清 Worker 工具上下文。

`scheduler.schedule_host_turn` 空函数一并删除；确认无调用点。

### 5. 状态枚举收口，读取层继续归一

Pydantic `GroupStudioStatus` 对外只声明 `open | paused | done | deleted`。`normalize_studio_status` 保留，写路径只写四态。`GROUP_STUDIO_HOST_AGENT_ID` 与 `require_agent` 跳过逻辑保留。

事件类型字面量可保留历史名以便读旧 `group_studio_events` 行，但前端继续忽略；新写入 MUST NOT 再 emit `GROUP_INTAKE` / `GROUP_WORK_ITEM` / `GROUP_DOD` / `GROUP_BLACKBOARD`。

### 6. 测试：删 skip 文件，补契约测

删除整模块 skip 的 `test_group_studio_pr2/4/5/6/7/8/9/12/13/14.py`。留下 `test_group_studio_v2.py`、`test_group_studio_pr1.py`、`test_group_studio_pr10.py`、`test_group_studio_archive_memory.py`。在 v2 测试中补：四条 410、快照 keys、拉人拒绝 host 哨兵、`group_studio` 包不再能 import 已删模块。

## Risks / Trade-offs

- [旧客户端读 `workItems`] → 字段消失。现网前端已不读；410 与文档标明 breaking。
- [历史 `GROUP_BLACKBOARD` 行仍在事件表] → 前端忽略表已写在事件契约；不删历史事件。
- [误删仍被 V2 使用的 `close_studio` / `pause_studio`] → tasks 按符号清单删，不按「service 后半段一刀切」。
- [空黑板 INSERT 与「不要求黑板行」不一致] → 以「有 studio + task_card 即可快照」为准，INSERT 可留作无害兼容。
- [pr1/pr10 若仍断言 drafting/Host 席位] → apply 时先跑这两份，把断言改成 V2，而不是 skip。

## Migration Plan

1. 先改模型与 snapshot 组装，再删模块，避免半删除导致 import 爆炸。
2. 启动时继续 `normalize_studio_status`；无需单独迁移 job。
3. 回滚：恢复删除的文件（git）。已发出的瘦快照会在回滚后重新变胖，可接受。
4. 文档：改 `docs/group-studio-v2.md` §18、`group_studio/__init__.py` 职责说明、`docs/project-todo.md` 若仍写「可先留文件」。
