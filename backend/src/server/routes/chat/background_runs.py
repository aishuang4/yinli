"""后台 Chat Run 管理。

把 Agent 执行与前端 SSE 订阅解耦：后端创建后台 task 持续执行 Agent，
前端连接只负责订阅事件。页面切换、刷新或网络断开不会直接取消后台执行。
"""

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.event_types import SSE_EVENT_RUNTIME
from src.server.routes.chat.store import _append_assistant_message
from src.storage.database import (
    append_chat_run_event,
    get_chat_run,
    list_chat_runs,
    update_chat_run_status,
    upsert_chat_run,
)


EventStreamFactory = Callable[[], AsyncGenerator[str, None]]
MAX_BACKGROUND_RUNS = 50
MAX_EVENTS_PER_RUN = 5000
COMPLETED_RUN_TTL = timedelta(hours=2)
WAITING_EVENT_TYPES = {"HITL_REQUIRED", "CLARIFICATION_REQUIRED"}
RUNTIME_WAITING_TYPES = {
    "RUN_WAITING",
    "AGENT_WAITING_HUMAN",
    "TEAM_WORKER_WAITING_HUMAN",
    "CLARIFICATION_WAITING",
    "HITL_REQUIRED",
}
RUNTIME_RUNNING_TYPES = {
    "RUN_STARTED",
    "RUN_RESUMED",
    "AGENT_STARTED",
    "AGENT_RESUMED",
    "TEAM_WORKER_STARTED",
    "TEAM_LEADER_STARTED",
    "TEAM_LEADER_SYNTHESIZING",
}
RUNTIME_TERMINAL_STATUS = {
    "RUN_COMPLETED": "completed",
    "RUN_FAILED": "failed",
    "RUN_CANCELLED": "cancelled",
}
ACTIVE_RUN_STATUSES = {"running", "waiting", "cancelling"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
CURRENT_BACKGROUND_RUN_ID: ContextVar[str] = ContextVar("current_chat_background_run_id", default="")


@dataclass
class ChatBackgroundRun:
    """进程内后台 Run 状态。"""

    id: str
    session_id: str
    agent_id: str = ""
    agent_name: str = ""
    team_id: str = ""
    team_name: str = ""
    task_text: str = ""
    mode: str = "agent"
    status: str = "running"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    events: list[str] = field(default_factory=list)
    subscribers: set[asyncio.Queue[str | None]] = field(default_factory=set)
    async_task: asyncio.Task | None = None
    persisted: bool = False


_runs: dict[str, ChatBackgroundRun] = {}
_runs_lock = asyncio.Lock()


async def get_chat_background_run(run_id: str) -> ChatBackgroundRun | None:
    """读取后台 Run 状态。"""
    async with _runs_lock:
        run = _runs.get(run_id)
    if run is not None:
        return run
    persisted = get_chat_run(run_id)
    return _run_from_persisted_record(persisted) if persisted else None


async def list_chat_background_runs() -> list[ChatBackgroundRun]:
    """列出当前进程内后台 Run。"""
    async with _runs_lock:
        memory_runs = list(_runs.values())
    by_id = {run.id: run for run in memory_runs}
    for record in list_chat_runs(MAX_BACKGROUND_RUNS):
        if record["id"] not in by_id:
            by_id[record["id"]] = _run_from_persisted_record(record)
    return sorted(
        by_id.values(),
        key=lambda item: item.completed_at or item.created_at,
        reverse=True,
    )


async def cancel_chat_background_run(run_id: str) -> ChatBackgroundRun | None:
    """取消一个仍在运行的后台 Run。"""
    async with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            persisted = get_chat_run(run_id)
            if not persisted:
                return None
            completed_at = datetime.now(timezone.utc).isoformat()
            update_chat_run_status(run_id, "cancelled", completed_at)
            return _run_from_persisted_record({
                **persisted,
                "status": "cancelled",
                "completedAt": completed_at,
            })
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        run.status = "cancelling"
        task = run.async_task
        update_chat_run_status(run.id, run.status, run.completed_at)

    if task is not None and not task.done():
        task.cancel()
    else:
        await _finalize_cancelled_run(run)
    return run


def current_chat_background_run_id() -> str:
    """读取当前事件流所属的后台 Run ID。"""
    return CURRENT_BACKGROUND_RUN_ID.get()


async def mark_chat_background_run_status(run_id: str, status: str) -> ChatBackgroundRun | None:
    """手动更新后台 Run 状态。用于 HITL resume 后同步 Run Center。"""
    async with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            return None
        run.status = status
        if status == "running":
            run.completed_at = None
        elif status in TERMINAL_RUN_STATUSES:
            run.completed_at = datetime.now(timezone.utc).isoformat()
        update_chat_run_status(run.id, run.status, run.completed_at)
        return run


async def relay_chat_background_run_events(
    run_id: str,
    events: AsyncGenerator[str, None],
) -> AsyncGenerator[str, None]:
    """把 resume 流里的事件追加回原后台 Run，同时继续返回给前端。"""
    run = await get_chat_background_run(run_id)
    if run is None:
        async for event in events:
            yield event
        return

    await mark_chat_background_run_status(run_id, "running")
    token = CURRENT_BACKGROUND_RUN_ID.set(run_id)
    try:
        async for event in events:
            await _broadcast(run, event)
            yield event
        if run.status != "waiting":
            await mark_chat_background_run_status(run_id, "completed")
    except Exception:
        await mark_chat_background_run_status(run_id, "failed")
        raise
    finally:
        CURRENT_BACKGROUND_RUN_ID.reset(token)


def serialize_chat_background_run(run: ChatBackgroundRun) -> dict:
    """Serialize one in-memory Run for API responses."""
    summary = _summarize_run_events(run)
    return {
        "id": run.id,
        "sessionId": run.session_id,
        "agentId": run.agent_id,
        "agentName": run.agent_name,
        "teamId": run.team_id,
        "teamName": run.team_name,
        "task": run.task_text,
        "mode": run.mode,
        "status": run.status,
        "createdAt": run.created_at,
        "completedAt": run.completed_at,
        "eventCount": len(run.events),
        "subscriberCount": len(run.subscribers),
        "latestEvent": run.events[-1] if run.events else "",
        **summary,
    }


async def start_chat_background_run(
    *,
    session_id: str,
    factory: EventStreamFactory,
    agent_id: str = "",
    agent_name: str = "",
    team_id: str = "",
    team_name: str = "",
    task: str = "",
    mode: str = "agent",
    persist: bool = True,
) -> ChatBackgroundRun:
    """创建后台 Run 并启动执行 task。"""
    run = ChatBackgroundRun(
        id=f"run-{uuid.uuid4().hex}",
        session_id=session_id,
        agent_id=agent_id,
        agent_name=agent_name,
        team_id=team_id,
        team_name=team_name,
        task_text=task,
        mode=mode,
    )
    async with _runs_lock:
        _prune_runs_locked()
        _runs[run.id] = run
    if persist:
        _persist_run(run)
    run.async_task = asyncio.create_task(_consume_chat_events(run, factory), name=f"chat-run-{run.id}")
    return run


async def subscribe_chat_background_run(run_id: str) -> AsyncGenerator[str, None]:
    """订阅后台 Run 事件；订阅断开不会取消后台 task。"""
    async with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            error_event = _sse_event("ERROR", {"message": f"Run not found: {run_id}"})
            replay_events: list[str] = [error_event]
            queue = None
            already_finished = True
        else:
            replay_events = list(run.events)
            queue: asyncio.Queue[str | None] | None = asyncio.Queue()
            run.subscribers.add(queue)
            already_finished = run.status in TERMINAL_RUN_STATUSES

    try:
        for event in replay_events:
            yield event
        if already_finished or queue is None:
            return
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event
    finally:
        async with _runs_lock:
            current = _runs.get(run_id)
            if current is not None and queue is not None:
                current.subscribers.discard(queue)


async def _consume_chat_events(run: ChatBackgroundRun, factory: EventStreamFactory) -> None:
    await _broadcast(run, _sse_event("RUN", {
        "run_id": run.id,
        "session_id": run.session_id,
        "agent_id": run.agent_id,
        "agent_name": run.agent_name,
        "team_id": run.team_id,
        "team_name": run.team_name,
        "task": run.task_text,
        "mode": run.mode,
        "status": run.status,
        "created_at": run.created_at,
    }))
    token = CURRENT_BACKGROUND_RUN_ID.set(run.id)
    try:
        async for event in factory():
            await _broadcast(run, event)
        if run.status != "waiting":
            run.status = "completed"
            update_chat_run_status(run.id, run.status, run.completed_at)
    except asyncio.CancelledError:
        await _finalize_cancelled_run(run)
        raise
    except Exception as exc:
        run.status = "failed"
        from src.server.routes.chat.runtime_events import runtime_event_sse

        await _broadcast(run, runtime_event_sse(
            "RUN_FAILED",
            session_id=run.session_id,
            run_id=run.id,
            scope="run",
            agent_id=run.agent_id,
            agent_name=run.agent_name,
            status="failed",
            reason="background_run_error",
            payload={"message": str(exc)},
        ))
        await _broadcast(run, _sse_event("ERROR", {
            "message": f"Run failed: {exc}",
            "run_id": run.id,
            "session_id": run.session_id,
        }))
    finally:
        if run.status in TERMINAL_RUN_STATUSES:
            run.completed_at = datetime.now(timezone.utc).isoformat()
            update_chat_run_status(run.id, run.status, run.completed_at)
        await _close_subscribers(run)
        async with _runs_lock:
            _prune_runs_locked()
        CURRENT_BACKGROUND_RUN_ID.reset(token)


async def _broadcast(run: ChatBackgroundRun, event: str) -> None:
    event_type = _extract_sse_event_type(event)
    runtime_status = _status_from_runtime_event_chunk(event_type, event)
    if runtime_status:
        run.status = runtime_status
        if runtime_status == "running":
            run.completed_at = None
        elif runtime_status in TERMINAL_RUN_STATUSES and not run.completed_at:
            run.completed_at = datetime.now(timezone.utc).isoformat()
        update_chat_run_status(run.id, run.status, run.completed_at)
    elif event_type in WAITING_EVENT_TYPES:
        run.status = "waiting"
        update_chat_run_status(run.id, run.status, run.completed_at)
    elif run.status == "waiting":
        run.status = "running"
        update_chat_run_status(run.id, run.status, run.completed_at)
    run.events.append(event)
    if len(run.events) > MAX_EVENTS_PER_RUN:
        run.events = run.events[-MAX_EVENTS_PER_RUN:]
    if run.persisted:
        append_chat_run_event(run.id, event)
    for queue in list(run.subscribers):
        await queue.put(event)


async def _finalize_cancelled_run(run: ChatBackgroundRun) -> None:
    """将取消状态、事件和已生成内容落库，保证历史与任务中心一致。"""

    if run.status == "cancelled":
        return
    run.status = "cancelled"
    run.completed_at = datetime.now(timezone.utc).isoformat()
    _append_cancelled_assistant_message(run)
    from src.server.routes.chat.runtime_events import runtime_event_sse

    await _broadcast(run, runtime_event_sse(
        "RUN_CANCELLED",
        session_id=run.session_id,
        run_id=run.id,
        scope="run",
        agent_id=run.agent_id,
        agent_name=run.agent_name,
        status="cancelled",
        payload={"completedAt": run.completed_at},
    ))
    await _broadcast(run, _sse_event("RUN_CANCELLED", {
        "message": "Run cancelled",
        "run_id": run.id,
        "session_id": run.session_id,
        "completed_at": run.completed_at,
    }))
    update_chat_run_status(run.id, run.status, run.completed_at)


def _append_cancelled_assistant_message(run: ChatBackgroundRun) -> None:
    """从已缓存事件恢复停止前输出，追加到会话历史。"""

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    for raw_event in run.events:
        event_type, data = _parse_sse_event(raw_event)
        if event_type == "TEXT_BLOCK_DELTA":
            text_parts.append(str(data.get("delta") or ""))
        elif event_type == "THINKING_BLOCK_DELTA":
            thinking_parts.append(str(data.get("delta") or ""))

    content = "".join(text_parts).strip()
    thinking = "".join(thinking_parts).strip()
    if not content and not thinking:
        content = "本轮执行已由用户停止。"

    _append_assistant_message(
        session_id=run.session_id,
        agent_id=run.agent_id,
        agent_name=run.agent_name,
        model_name="",
        content=content,
        error="用户已停止本轮执行。",
        thinking=thinking,
    )


async def _close_subscribers(run: ChatBackgroundRun) -> None:
    for queue in list(run.subscribers):
        await queue.put(None)


def _prune_runs_locked() -> None:
    """Drop old completed in-memory runs while keeping active runs available."""

    now = datetime.now(timezone.utc)
    removable_ids: list[str] = []
    for run_id, run in _runs.items():
        if run.status in ACTIVE_RUN_STATUSES or run.subscribers:
            continue
        if run.completed_at:
            try:
                completed_at = datetime.fromisoformat(run.completed_at)
            except ValueError:
                completed_at = now
            if now - completed_at > COMPLETED_RUN_TTL:
                removable_ids.append(run_id)

    for run_id in removable_ids:
        _runs.pop(run_id, None)

    if len(_runs) <= MAX_BACKGROUND_RUNS:
        return

    candidates = sorted(
        [
            run
            for run in _runs.values()
            if run.status not in ACTIVE_RUN_STATUSES and not run.subscribers
        ],
        key=lambda item: item.completed_at or item.created_at,
    )
    for run in candidates[: max(0, len(_runs) - MAX_BACKGROUND_RUNS)]:
        _runs.pop(run.id, None)


def _extract_sse_event_type(event: str) -> str:
    """从 SSE 文本中提取事件类型。"""
    for line in event.splitlines():
        if line.startswith("event:"):
            return line.removeprefix("event:").strip()
    return ""


def _persist_run(run: ChatBackgroundRun) -> None:
    upsert_chat_run({
        "id": run.id,
        "sessionId": run.session_id,
        "agentId": run.agent_id,
        "agentName": run.agent_name,
        "teamId": run.team_id,
        "teamName": run.team_name,
        "task": run.task_text,
        "mode": run.mode,
        "status": run.status,
        "createdAt": run.created_at,
        "completedAt": run.completed_at,
    })
    run.persisted = True


def _run_from_persisted_record(record: dict) -> ChatBackgroundRun:
    return ChatBackgroundRun(
        id=record["id"],
        session_id=record["sessionId"],
        agent_id=record.get("agentId", ""),
        agent_name=record.get("agentName", ""),
        team_id=record.get("teamId", ""),
        team_name=record.get("teamName", ""),
        task_text=record.get("task", ""),
        mode=record.get("mode", "agent"),
        status=record.get("status", "completed"),
        created_at=record.get("createdAt") or datetime.now(timezone.utc).isoformat(),
        completed_at=record.get("completedAt"),
        events=list(record.get("events") or []),
        persisted=True,
    )


def _summarize_run_events(run: ChatBackgroundRun) -> dict:
    """从底层 SSE 事件提炼任务中心摘要。"""
    runtime_summary = _summarize_runtime_events(run)
    if runtime_summary is not None:
        return runtime_summary

    progress = _base_progress_for_status(run.status)
    current_stage = _stage_for_status(run.status)
    result_parts: list[str] = []
    active_actors: list[str] = []
    total_workers = 0
    completed_workers = 0

    for raw_event in run.events:
        event_type, data = _parse_sse_event(raw_event)
        if event_type == "MODEL_CALL_START":
            progress = max(progress, 25)
            current_stage = "模型正在思考"
        elif event_type == "TOOL_CALL_START":
            progress = max(progress, 40)
            current_stage = "正在调用工具"
        elif event_type == "TOOL_RESULT_END":
            progress = max(progress, 55)
            current_stage = "工具结果已返回"
        elif event_type == "TEXT_BLOCK_DELTA":
            progress = max(progress, 70)
            result_parts.append(str(data.get("delta") or ""))
            current_stage = "正在生成回复"
        elif event_type == "TEAM_ACTIVITY":
            activities = data.get("activities")
            if isinstance(activities, list):
                total_workers = len([item for item in activities if isinstance(item, dict) and item.get("role") == "worker"])
                completed_workers = len([
                    item
                    for item in activities
                    if isinstance(item, dict)
                    and item.get("role") == "worker"
                    and item.get("status") in {"completed", "failed"}
                ])
                active_actors = [
                    str(item.get("agentName") or item.get("name") or item.get("agentId"))
                    for item in activities
                    if isinstance(item, dict) and item.get("status") in {"running", "waiting"}
                ]
                if total_workers:
                    progress = max(progress, min(90, 20 + int((completed_workers / total_workers) * 55)))
                    current_stage = f"Worker 协作中：{completed_workers}/{total_workers} 完成"
        elif event_type in WAITING_EVENT_TYPES:
            progress = max(progress, 65)
            current_stage = "等待人工处理"
        elif event_type == "REPLY_END":
            progress = max(progress, 90)
            current_stage = "回复已生成，正在收尾"
        elif event_type == "ERROR":
            current_stage = "执行异常"

    if run.status == "completed":
        progress = 100
        current_stage = "已完成"
    elif run.status == "failed":
        progress = 100
        current_stage = "失败"
    elif run.status == "cancelled":
        progress = 100
        current_stage = "已取消"
    elif run.status == "waiting":
        current_stage = "等待人工处理"

    result_text = "".join(result_parts).strip()
    return {
        "progress": progress,
        "currentStage": current_stage,
        "activeActors": active_actors[:6],
        "resultPreview": result_text,
        "resultLength": len(result_text),
        "workerTotal": total_workers,
        "workerCompleted": completed_workers,
    }


def _summarize_runtime_events(run: ChatBackgroundRun) -> dict | None:
    """优先从应用级 Runtime Event 生成任务中心摘要。"""

    runtime_events: list[dict] = []
    result_parts: list[str] = []
    worker_status: dict[str, str] = {}
    active_actors: list[str] = []
    current_stage = _stage_for_status(run.status)
    progress = _base_progress_for_status(run.status)

    for raw_event in run.events:
        event_type, data = _parse_sse_event(raw_event)
        if event_type == "TEXT_BLOCK_DELTA":
            result_parts.append(str(data.get("delta") or ""))
        if event_type != SSE_EVENT_RUNTIME:
            continue
        runtime_events.append(data)
        runtime_type = str(data.get("type") or "")
        scope = str(data.get("scope") or "")
        agent_name = str(data.get("agentName") or data.get("agentId") or "")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}

        if runtime_type in RUNTIME_WAITING_TYPES:
            progress = max(progress, 65)
            current_stage = "等待人工处理"
        elif runtime_type == "AGENT_MODEL_CALL_STARTED":
            progress = max(progress, 25)
            current_stage = f"{agent_name or 'Agent'} 正在调用模型"
        elif runtime_type == "TOOL_CALL_STARTED":
            progress = max(progress, 40)
            tool_name = str(payload.get("tool_call_name") or "工具")
            current_stage = f"{agent_name or 'Agent'} 正在调用 {tool_name}"
        elif runtime_type == "TOOL_RESULT_COMPLETED":
            progress = max(progress, 55)
            current_stage = "工具结果已返回"
        elif runtime_type == "AGENT_OUTPUT_DELTA":
            progress = max(progress, 70)
            current_stage = "正在生成回复"
        elif runtime_type == "TEAM_WORKER_STARTED":
            progress = max(progress, 30)
            current_stage = f"{agent_name or 'Worker'} 正在执行"
        elif runtime_type == "TEAM_WORKER_COMPLETED":
            progress = max(progress, 75)
            current_stage = f"{agent_name or 'Worker'} 已完成"
        elif runtime_type == "TEAM_LEADER_SYNTHESIZING":
            progress = max(progress, 85)
            current_stage = "Leader 正在汇总"
        elif runtime_type == "ARTIFACT_CREATED":
            progress = max(progress, 72)
            current_stage = "已生成文件产物"
        elif runtime_type == "ARTIFACT_UPDATED":
            progress = max(progress, 72)
            current_stage = "已更新文件产物"
        elif runtime_type == "SANDBOX_STARTED":
            progress = max(progress, 15)
            current_stage = "沙盒开始运行"
        elif runtime_type == "SANDBOX_COMPLETED":
            progress = max(progress, 95)
            current_stage = "沙盒运行完成"
        elif runtime_type == "SANDBOX_FAILED":
            progress = 100
            current_stage = "沙盒运行失败"
        elif runtime_type == "MEMORY_SUMMARY_STARTED":
            progress = max(progress, 92)
            current_stage = "正在总结记忆"
        elif runtime_type == "MEMORY_SUMMARY_COMPLETED":
            progress = max(progress, 96)
            current_stage = "记忆总结完成"

        if scope in {"worker", "leader", "single"} and agent_name and str(data.get("status") or "") in {"running", "waiting"}:
            if agent_name not in active_actors:
                active_actors.append(agent_name)

        if runtime_type.startswith("TEAM_WORKER_"):
            worker_id = str(data.get("teamActivityId") or data.get("agentId") or agent_name)
            if worker_id:
                worker_status[worker_id] = str(data.get("status") or "")

    if not runtime_events:
        return None

    worker_total = len(worker_status)
    worker_completed = len([
        status for status in worker_status.values()
        if status in {"completed", "failed"}
    ])
    if worker_total:
        progress = max(progress, min(90, 25 + int((worker_completed / worker_total) * 55)))
        if run.status not in TERMINAL_RUN_STATUSES and run.status != "waiting":
            current_stage = f"Worker 协作中：{worker_completed}/{worker_total} 完成"

    if run.status == "completed":
        progress = 100
        current_stage = "已完成"
    elif run.status == "failed":
        progress = 100
        current_stage = "失败"
    elif run.status == "cancelled":
        progress = 100
        current_stage = "已取消"
    elif run.status == "waiting":
        current_stage = "等待人工处理"

    result_text = "".join(result_parts).strip()
    return {
        "progress": progress,
        "currentStage": current_stage,
        "activeActors": active_actors[:6],
        "resultPreview": result_text,
        "resultLength": len(result_text),
        "workerTotal": worker_total,
        "workerCompleted": worker_completed,
    }


def _status_from_runtime_event_chunk(event_type: str, raw_event: str) -> str:
    """Runtime Event 优先决定后台 Run 状态。"""

    if event_type != SSE_EVENT_RUNTIME:
        return ""
    _, data = _parse_sse_event(raw_event)
    runtime_type = str(data.get("type") or "")
    if runtime_type in RUNTIME_TERMINAL_STATUS:
        return RUNTIME_TERMINAL_STATUS[runtime_type]
    if runtime_type in RUNTIME_WAITING_TYPES:
        return "waiting"
    if runtime_type in RUNTIME_RUNNING_TYPES:
        return "running"
    return ""


def _base_progress_for_status(status: str) -> int:
    if status == "completed":
        return 100
    if status in {"failed", "cancelled"}:
        return 100
    if status == "waiting":
        return 65
    if status == "cancelling":
        return 90
    return 8


def _stage_for_status(status: str) -> str:
    if status == "waiting":
        return "等待人工处理"
    if status == "cancelling":
        return "正在取消"
    if status == "completed":
        return "已完成"
    if status == "failed":
        return "失败"
    if status == "cancelled":
        return "已取消"
    return "准备执行"


def _parse_sse_event(raw_event: str) -> tuple[str, dict]:
    event_type = ""
    data_text = "{}"
    for line in raw_event.splitlines():
        if line.startswith("event:"):
            event_type = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_text = line.removeprefix("data:").strip()
    try:
        data = json.loads(data_text)
    except json.JSONDecodeError:
        data = {}
    return event_type, data if isinstance(data, dict) else {}
