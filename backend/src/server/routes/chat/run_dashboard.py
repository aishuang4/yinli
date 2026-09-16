"""从 DisplaySnapshot 汇总 Runs 页进度、阶段和结果摘要。"""

from __future__ import annotations

from typing import Any

from src.server.routes.chat.lifecycle import TERMINAL_RUN_STATUSES, is_waiting_run_status

_RESULT_PREVIEW_LIMIT = 4_000
_TERMINAL_WORK = {"completed", "succeeded", "failed", "cancelled", "stopped", "skipped"}
_ACTIVE_WORK = {"running", "working", "claimed", "assigned", "queued"}
_STAGE_BY_STATUS = {
    "waiting_user": "等待用户补充",
    "waiting_permission": "等待权限确认",
    "waiting_external": "等待外部结果",
    "waiting": "等待人工处理",
    "cancelling": "正在取消",
    "completed": "已完成",
    "failed": "已失败",
    "cancelled": "已取消",
}


def summarize_run_dashboard(
    *,
    status: str,
    agent_name: str = "",
    mode: str = "agent",
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 Run 状态和 Snapshot 收成 Runs 列表/详情需要的字段。"""

    blocks = _blocks(snapshot)
    texts = [_block_text(item) for item in _of_kind(blocks, "text")]
    result = next((item for item in reversed(texts) if item), "")
    error = _first_error(blocks)
    members = _of_kind(blocks, "team-member")
    tasks = _of_kind(blocks, "team-task")
    tools = _of_kind(blocks, "tool")
    plans = _of_kind(blocks, "plan")
    team = next(iter(_of_kind(blocks, "team")), None)

    member_total = len(members)
    member_completed = sum(1 for item in members if _is_done(item))
    active_actors = [
        _member_name(item)
        for item in members
        if _is_active(item)
    ]
    if not active_actors:
        active_actors = [agent_name or "Assistant"]

    progress, current_stage = _progress_and_stage(
        status=status,
        mode=mode,
        team=team,
        tasks=tasks,
        tools=tools,
        plans=plans,
        has_text=bool(result),
    )
    latest = _latest_event(tools, current_stage)
    return {
        "progress": progress,
        "currentStage": current_stage,
        "activeActors": active_actors,
        "resultPreview": result[:_RESULT_PREVIEW_LIMIT],
        "resultLength": len(result),
        "memberTotal": member_total,
        "memberCompleted": member_completed,
        "latestEvent": latest,
        "error": error,
    }


def _blocks(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    by_id = snapshot.get("blocksById") or {}
    order = snapshot.get("order") or []
    blocks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block_id in order:
        item = by_id.get(block_id)
        if isinstance(item, dict):
            blocks.append(item)
            seen.add(block_id)
    for block_id, item in by_id.items():
        if block_id not in seen and isinstance(item, dict):
            blocks.append(item)
    return blocks


def _of_kind(blocks: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [item for item in blocks if str(item.get("kind") or "") == kind]


def _data(block: dict[str, Any]) -> Any:
    return block.get("data")


def _block_text(block: dict[str, Any]) -> str:
    data = _data(block)
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, dict):
        for key in ("content", "text", "markdown"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _first_error(blocks: list[dict[str, Any]]) -> str:
    for block in reversed(blocks):
        data = _data(block)
        if not isinstance(data, dict):
            continue
        message = data.get("message") or data.get("error")
        if isinstance(message, str) and message.strip():
            if str(block.get("kind") or "") in {"error", "message", "tool"}:
                return message.strip()
    return ""


def _member_name(block: dict[str, Any]) -> str:
    data = _data(block)
    if isinstance(data, dict):
        name = data.get("name") or data.get("displayName") or data.get("role")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return "Member"


def _status(block: dict[str, Any]) -> str:
    data = _data(block)
    if isinstance(data, dict):
        value = data.get("status") or data.get("phase")
        if isinstance(value, str) and value.strip():
            return value.strip()
    phase = str(block.get("phase") or "")
    if phase == "closed":
        return str(block.get("outcome") or "completed")
    return phase or "running"


def _is_done(block: dict[str, Any]) -> bool:
    if str(block.get("phase") or "") == "closed":
        return True
    return _status(block) in _TERMINAL_WORK


def _is_active(block: dict[str, Any]) -> bool:
    if _is_done(block):
        return False
    return str(block.get("phase") or "") == "open" or _status(block) in _ACTIVE_WORK


def _progress_and_stage(
    *,
    status: str,
    mode: str,
    team: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    has_text: bool,
) -> tuple[int, str]:
    if status in TERMINAL_RUN_STATUSES:
        return 100, _STAGE_BY_STATUS.get(status, "已结束")
    if status in _STAGE_BY_STATUS and is_waiting_run_status(status):
        working, _label = _working_progress(mode, team, tasks, tools, plans, has_text)
        return max(working, 20), _STAGE_BY_STATUS[status]
    if status == "cancelling":
        return 100, _STAGE_BY_STATUS["cancelling"]

    progress, stage = _working_progress(mode, team, tasks, tools, plans, has_text)
    return progress, stage


def _working_progress(
    mode: str,
    team: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    has_text: bool,
) -> tuple[int, str]:
    team_progress = _team_progress(team, tasks)
    if team_progress is not None:
        done, total = team_progress
        ratio = 0 if total <= 0 else done / total
        return _cap(10 + round(80 * ratio)), f"Team 协作中 · {done}/{total} 个任务"

    plan_progress = _plan_progress(plans)
    if plan_progress is not None:
        done, total = plan_progress
        ratio = 0 if total <= 0 else done / total
        return _cap(10 + round(80 * ratio)), f"计划执行中 · {done}/{total} 步"

    if tools:
        done = sum(1 for item in tools if _is_done(item))
        total = len(tools)
        open_tool = next((item for item in reversed(tools) if _is_active(item)), None)
        name = _tool_name(open_tool) if open_tool else _tool_name(tools[-1])
        stage = f"正在调用 {name}" if open_tool else f"已完成 {done}/{total} 个工具"
        return _cap(15 + round(75 * done / total)), stage

    if has_text:
        return 55, "正在生成回复"
    if mode == "team":
        return 12, "Team 正在组建"
    return 8, "正在运行"


def _team_progress(
    team: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
) -> tuple[int, int] | None:
    if tasks:
        return sum(1 for item in tasks if _is_done(item)), len(tasks)
    data = _data(team) if team else None
    if not isinstance(data, dict):
        return None
    counts = data.get("counts")
    if not isinstance(counts, dict):
        return None
    total = _as_int(counts.get("tasks"))
    if total <= 0:
        return None
    done = _as_int(counts.get("succeeded"))
    return done, total


def _plan_progress(plans: list[dict[str, Any]]) -> tuple[int, int] | None:
    for plan in reversed(plans):
        data = _data(plan)
        if not isinstance(data, dict):
            continue
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            continue
        total = len(steps)
        done = 0
        for step in steps:
            if not isinstance(step, dict):
                continue
            if str(step.get("status") or "") in _TERMINAL_WORK:
                done += 1
        return done, total
    return None


def _tool_name(block: dict[str, Any] | None) -> str:
    if not block:
        return "工具"
    data = _data(block)
    if isinstance(data, dict):
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return "工具"


def _latest_event(tools: list[dict[str, Any]], stage: str) -> str:
    if tools:
        return _tool_name(tools[-1])
    return stage


def _cap(value: int) -> int:
    return max(0, min(95, value))


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
