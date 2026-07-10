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
from src.server.routes.chat.event_types import SSE_EVENT_RUNTIME, SSE_EVENT_TEAM
from src.server.routes.chat.event_hub import (
    consume_run_state_display_events,
    initial_run_state,
    normalize_run_state,
    project_event,
    refresh_run_state_display_snapshot,
)
from src.server.routes.chat.run_state_schema import dump_run_state, dump_run_state_patch
from src.server.routes.chat.store import (
    _append_assistant_message,
    _update_latest_assistant_display_snapshot,
    _update_latest_assistant_team_snapshot,
)
from src.storage.database import (
    append_chat_run_event,
    get_chat_run_state,
    get_latest_assistant_message_preview,
    get_chat_run,
    list_chat_runs,
    update_chat_run_status,
    upsert_chat_run_state,
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
    "CLARIFICATION_WAITING",
    "HITL_REQUIRED",
}
RUNTIME_RUNNING_TYPES = {
    "RUN_STARTED",
    "RUN_RESUMED",
    "AGENT_STARTED",
    "AGENT_RESUMED",
}
RUNTIME_TERMINAL_STATUS = {
    "RUN_COMPLETED": "completed",
    "RUN_FAILED": "failed",
    "RUN_CANCELLED": "cancelled",
}
RUN_STATE_METADATA_PATCH_EVENTS = {
    "RUN",
    "MODEL_CALL_START",
    "MODEL_CALL_END",
    "RUN_CANCELLED",
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
    task_text: str = ""
    mode: str = "agent"
    status: str = "running"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    events: list[str] = field(default_factory=list)
    event_count: int = 0
    state: dict = field(default_factory=dict)
    subscribers: set[asyncio.Queue[str | None]] = field(default_factory=set)
    state_subscribers: set[asyncio.Queue[str | None]] = field(default_factory=set)
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
    persisted = get_chat_run(run_id, event_limit=100)
    return _run_from_persisted_record(persisted) if persisted else None


async def list_chat_background_runs() -> list[ChatBackgroundRun]:
    """列出当前进程内后台 Run。"""
    async with _runs_lock:
        memory_runs = list(_runs.values())
    by_id = {run.id: run for run in memory_runs}
    for record in list_chat_runs(MAX_BACKGROUND_RUNS, include_events=False):
        if record["id"] not in by_id:
            record = _normalize_stale_persisted_run(record)
            by_id[record["id"]] = _run_from_persisted_record(record)
    return sorted(
        by_id.values(),
        key=lambda item: item.completed_at or item.created_at,
        reverse=True,
    )


def _normalize_stale_persisted_run(record: dict) -> dict:
    """把不可恢复的历史 active run 收口为 cancelled。

    运行中的 run 只可靠保存在当前进程内；如果重启后仅剩持久化记录，
    waiting/running/cancelling 都无法继续订阅或 resume，不能继续显示为活跃任务。
    """
    if record.get("status") not in ACTIVE_RUN_STATUSES:
        return record
    completed_at = record.get("completedAt") or datetime.now(timezone.utc).isoformat()
    update_chat_run_status(str(record["id"]), "cancelled", completed_at)
    return {
        **record,
        "status": "cancelled",
        "completedAt": completed_at,
    }


async def cancel_chat_background_run(run_id: str) -> ChatBackgroundRun | None:
    """取消一个仍在运行的后台 Run。"""
    async with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            persisted = get_chat_run(run_id, event_limit=100)
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


async def mark_chat_background_run_resuming(
    run_id: str,
    *,
    interaction_id: str = "",
    action: str = "approve",
    external_results_preview: dict[str, str] | None = None,
) -> ChatBackgroundRun | None:
    """在 resume/start 返回前同步 RunState，避免前端重订阅读到旧 waiting 快照。"""

    now = datetime.now(timezone.utc).isoformat()
    patch_event = ""
    queues: list[asyncio.Queue[str | None]] = []
    async with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            return None

        run.status = "running"
        run.completed_at = None
        update_chat_run_status(run.id, run.status, run.completed_at)

        state = dict(run.state or initial_run_state(run))
        state["status"] = "running"
        state["completedAt"] = None
        state["updatedAt"] = now
        state["currentStage"] = "继续执行"
        if interaction_id:
            approvals = []
            for item in state.get("approvals") or []:
                if isinstance(item, dict) and str(item.get("interactionId") or "") == interaction_id:
                    approvals.append({
                        **item,
                        "status": "resolved",
                        "resolvedAt": now,
                        "action": action,
                    })
                else:
                    approvals.append(item)
            state["approvals"] = approvals
            runtime_event = {
                "type": "HITL_RESOLVED",
                "sessionId": run.session_id,
                "runId": run.id,
                "scope": "run",
                "status": "completed" if action == "approve" else "failed",
                "timestamp": now,
                "payload": {
                    "interactionId": interaction_id,
                    "action": action,
                    "externalResultsPreview": external_results_preview or {},
                },
            }
            state = project_event(state, SSE_EVENT_RUNTIME, runtime_event, run)
            display_events = consume_run_state_display_events(state)
        else:
            display_events = []
        refresh_run_state_display_snapshot(state)
        if run.persisted:
            run.state = upsert_chat_run_state(run.id, state)
        else:
            run.state = state
        _persist_latest_assistant_display_snapshot(run, SSE_EVENT_RUNTIME, force=True)

        patch = {
            "kind": "approval_resolved" if interaction_id else "snapshot_meta",
            "runId": run.id,
            "sessionId": run.session_id,
            "status": run.state.get("status") or run.status,
            "currentStage": run.state.get("currentStage") or "",
            "updatedAt": run.state.get("updatedAt") or now,
            "completedAt": run.state.get("completedAt"),
            "lastEventType": "RUN_RESUMED",
            "eventCount": run.state.get("eventCount") or run.event_count,
        }
        if interaction_id:
            patch["resolvedApprovalId"] = interaction_id
        patches = []
        if display_events:
            for display_event in display_events:
                patches.append(_sse_event("RUN_STATE_PATCH", dump_run_state_patch({
                    **patch,
                    "kind": "display_event",
                    "displayEvent": display_event,
                })))
        else:
            patches.append(_sse_event("RUN_STATE_PATCH", dump_run_state_patch(patch)))
        queues = list(run.state_subscribers)

    for queue in queues:
        for patch_event in patches:
            await queue.put(patch_event)
    return run


async def emit_chat_background_runtime_event(run_id: str, event: str) -> bool:
    """向仍在内存中的后台 Run 推送一个 Runtime Event。"""

    async with _runs_lock:
        run = _runs.get(run_id)
    if run is None:
        return False
    await _broadcast(run, event)
    return True


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
    summary = _summarize_run_events(run) if run.events else _empty_run_summary(run)
    summary = _fill_result_preview_from_history(run, summary)
    return {
        "id": run.id,
        "sessionId": run.session_id,
        "agentId": run.agent_id,
        "agentName": run.agent_name,
        "task": run.task_text,
        "mode": run.mode,
        "status": run.status,
        "createdAt": run.created_at,
        "completedAt": run.completed_at,
        "eventCount": run.event_count or len(run.events),
        "subscriberCount": len(run.subscribers),
        "latestEvent": run.events[-1] if run.events else "",
        **summary,
    }


def _fill_result_preview_from_history(run: ChatBackgroundRun, summary: dict) -> dict:
    """当事件摘要没有结果时，从历史 Assistant 消息补充轻量结果预览。"""
    if summary.get("resultPreview"):
        return summary
    latest = get_latest_assistant_message_preview(run.session_id)
    content = str((latest or {}).get("content") or "").strip()
    if not content:
        return summary
    preview = content[:4000]
    return {
        **summary,
        "resultPreview": preview,
        "resultLength": len(content),
    }


def _empty_run_summary(run: ChatBackgroundRun) -> dict:
    """Build a cheap summary when raw events are intentionally omitted."""
    return {
        "progress": _base_progress_for_status(run.status),
        "currentStage": _stage_for_status(run.status),
        "activeActors": [],
        "resultPreview": "",
        "resultLength": 0,
        "memberTotal": 0,
        "memberCompleted": 0,
    }


async def start_chat_background_run(
    *,
    session_id: str,
    factory: EventStreamFactory,
    agent_id: str = "",
    agent_name: str = "",
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
        task_text=task,
        mode=mode,
    )
    async with _runs_lock:
        _prune_runs_locked()
        _runs[run.id] = run
    if persist:
        _persist_run(run)
        run.state = upsert_chat_run_state(run.id, initial_run_state(run))
    else:
        run.state = initial_run_state(run)
    run.async_task = asyncio.create_task(_consume_chat_events(run, factory), name=f"chat-run-{run.id}")
    return run


async def subscribe_chat_background_run(run_id: str, *, replay: bool = True) -> AsyncGenerator[str, None]:
    """订阅后台 Run 事件；订阅断开不会取消后台 task。"""
    async with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            error_event = _sse_event("ERROR", {"message": f"Run not found: {run_id}"})
            replay_events: list[str] = [error_event]
            queue = None
            already_finished = True
        else:
            replay_events = list(run.events) if replay else []
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


async def subscribe_chat_run_state(run_id: str) -> AsyncGenerator[str, None]:
    """订阅 RunState 中间层状态；前端主界面优先消费该流。"""

    queue: asyncio.Queue[str | None] | None = None
    async with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            persisted = get_chat_run(run_id, event_limit=0)
            if not persisted:
                initial_event = _sse_event("ERROR", {"message": f"Run not found: {run_id}"})
            else:
                run = _run_from_persisted_record(_normalize_stale_persisted_run(persisted))
                initial_event = _run_state_sse(run)
            already_finished = True
        else:
            queue = asyncio.Queue()
            run.state_subscribers.add(queue)
            already_finished = _run_state_stream_should_close(run)
            initial_event = _run_state_sse(run)

    try:
        yield initial_event
        if already_finished:
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
                current.state_subscribers.discard(queue)


async def _consume_chat_events(run: ChatBackgroundRun, factory: EventStreamFactory) -> None:
    await _broadcast(run, _sse_event("RUN", {
        "run_id": run.id,
        "session_id": run.session_id,
        "agent_id": run.agent_id,
        "agent_name": run.agent_name,
        "task": run.task_text,
        "mode": run.mode,
        "status": run.status,
        "created_at": run.created_at,
    }))
    token = CURRENT_BACKGROUND_RUN_ID.set(run.id)
    try:
        async for event in factory():
            await _broadcast(run, event)
        if run.status not in TERMINAL_RUN_STATUSES and run.status != "waiting":
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
    _, event_data = _parse_sse_event(event)
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
    run.event_count += 1
    run.events.append(event)
    if len(run.events) > MAX_EVENTS_PER_RUN:
        run.events = run.events[-MAX_EVENTS_PER_RUN:]
    next_state = project_event(run.state, event_type, event_data, run)
    display_events = consume_run_state_display_events(next_state)
    if run.persisted:
        append_chat_run_event(run.id, event)
        run.state = upsert_chat_run_state(run.id, next_state)
    else:
        run.state = next_state
    _persist_latest_assistant_display_snapshot(run, event_type)
    state_patches = [
        _run_state_patch_sse(run, event_type, event_data, display_event=display_event)
        for display_event in display_events
    ]
    if not state_patches and _should_emit_run_state_metadata_patch(event_type, event_data):
        state_patches = [_run_state_patch_sse(run, event_type, event_data)]
    should_close_state_stream = False
    for state_patch in state_patches:
        for queue in list(run.state_subscribers):
            await queue.put(state_patch)
        should_close_state_stream = should_close_state_stream or _run_state_patch_should_close(state_patch)
    if should_close_state_stream:
        for queue in list(run.state_subscribers):
            await queue.put(None)
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


def _persist_latest_assistant_display_snapshot(run: ChatBackgroundRun, event_type: str, *, force: bool = False) -> None:
    """在 RunState 到达可回放节点后，把展示快照写回本轮 Assistant 消息。"""

    if (
        not force
        and event_type not in {"CLARIFICATION_REQUIRED", "HITL_REQUIRED"}
        and run.status not in TERMINAL_RUN_STATUSES
    ):
        return
    snapshot = normalize_run_state(run.state).get("displaySnapshot")
    if not isinstance(snapshot, dict):
        return
    _update_latest_assistant_display_snapshot(
        session_id=run.session_id,
        display_snapshot=snapshot,
        min_created_at=run.created_at,
    )
    team_snapshot = normalize_run_state(run.state).get("teamSnapshot")
    if isinstance(team_snapshot, dict):
        _update_latest_assistant_team_snapshot(
            session_id=run.session_id,
            team_snapshot=team_snapshot,
            min_created_at=run.created_at,
        )


async def _close_subscribers(run: ChatBackgroundRun) -> None:
    for queue in list(run.subscribers):
        await queue.put(None)
    for queue in list(run.state_subscribers):
        await queue.put(None)


def _run_state_stream_should_close(run: ChatBackgroundRun) -> bool:
    """判断 RunState 订阅在首包快照后是否应立即结束。"""

    return run.status in TERMINAL_RUN_STATUSES or _run_state_has_actionable_waiting_state(run.state)


def _run_state_patch_should_close(patch_event: str) -> bool:
    """判断单个 RunState patch 发送后是否应关闭订阅。"""

    _, patch = _parse_sse_event(patch_event)
    status = str(patch.get("status") or "")
    if status in TERMINAL_RUN_STATUSES:
        return True
    if status != "waiting":
        return False
    display_event = patch.get("displayEvent") if isinstance(patch.get("displayEvent"), dict) else {}
    return (
        display_event.get("block") == "feedback"
        or display_event.get("block") == "plan" and display_event.get("status") == "waiting"
    )


def _run_state_has_actionable_waiting_state(state: dict) -> bool:
    """判断 waiting 快照是否已有前端可操作内容。"""

    if str(state.get("status") or "") != "waiting":
        return False
    approvals = state.get("approvals") if isinstance(state.get("approvals"), list) else []
    if any(isinstance(item, dict) and item.get("status") == "waiting" for item in approvals):
        return True
    plans = state.get("plans") if isinstance(state.get("plans"), list) else []
    if any(isinstance(item, dict) and item.get("status") == "waiting" for item in plans):
        return True
    snapshot = state.get("displaySnapshot") if isinstance(state.get("displaySnapshot"), dict) else {}
    blocks = snapshot.get("blocks") if isinstance(snapshot.get("blocks"), list) else []
    return any(
        isinstance(block, dict)
        and block.get("status") == "waiting"
        and block.get("block") in {"feedback", "plan"}
        for block in blocks
    )


def _prune_runs_locked() -> None:
    """Drop old completed in-memory runs while keeping active runs available."""

    now = datetime.now(timezone.utc)
    removable_ids: list[str] = []
    for run_id, run in _runs.items():
        if run.status in ACTIVE_RUN_STATUSES or run.subscribers or run.state_subscribers:
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
            and not run.state_subscribers
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
        task_text=record.get("task", ""),
        mode=record.get("mode", "agent"),
        status=record.get("status", "completed"),
        created_at=record.get("createdAt") or datetime.now(timezone.utc).isoformat(),
        completed_at=record.get("completedAt"),
        events=list(record.get("events") or []),
        event_count=int(record.get("eventCount") or len(record.get("events") or [])),
        state=get_chat_run_state(str(record["id"])) or {},
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
    total_members = 0
    completed_members = 0

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
        elif event_type == SSE_EVENT_TEAM:
            snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
            counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
            members = snapshot.get("members") if isinstance(snapshot.get("members"), list) else []
            total_members = max(0, int(counts.get("members") or len(members)) - 1)
            completed_members = len([
                member for member in members
                if isinstance(member, dict) and member.get("role") != "leader" and member.get("status") == "idle"
            ])
            active_actors = [
                str(member.get("name") or member.get("id"))
                for member in members
                if isinstance(member, dict) and member.get("status") in {"running", "waiting"}
            ]
            task_total = int(counts.get("tasks") or 0)
            task_succeeded = int(counts.get("succeeded") or 0)
            if task_total:
                progress = max(progress, min(90, 20 + int((task_succeeded / task_total) * 65)))
                current_stage = f"Team 协作中：{task_succeeded}/{task_total} 任务完成"
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
        "memberTotal": total_members,
        "memberCompleted": completed_members,
    }


def _summarize_runtime_events(run: ChatBackgroundRun) -> dict | None:
    """优先从应用级 Runtime Event 生成任务中心摘要。"""

    runtime_events: list[dict] = []
    result_parts: list[str] = []
    member_status: dict[str, str] = {}
    active_actors: list[str] = []
    current_stage = _stage_for_status(run.status)
    progress = _base_progress_for_status(run.status)

    for raw_event in run.events:
        event_type, data = _parse_sse_event(raw_event)
        if event_type == "TEXT_BLOCK_DELTA":
            result_parts.append(str(data.get("delta") or ""))
        if event_type == SSE_EVENT_TEAM:
            snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
            members = snapshot.get("members") if isinstance(snapshot.get("members"), list) else []
            counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
            member_status = {
                str(member.get("id") or ""): str(member.get("status") or "")
                for member in members
                if isinstance(member, dict) and member.get("role") != "leader" and member.get("id")
            }
            active_actors = [
                str(member.get("name") or member.get("id"))
                for member in members
                if isinstance(member, dict) and member.get("status") in {"running", "waiting"}
            ]
            task_total = int(counts.get("tasks") or 0)
            task_succeeded = int(counts.get("succeeded") or 0)
            if task_total:
                progress = max(progress, min(90, 20 + int((task_succeeded / task_total) * 65)))
                current_stage = f"Team 协作中：{task_succeeded}/{task_total} 任务完成"
        if event_type != SSE_EVENT_RUNTIME:
            continue
        runtime_events.append(data)
        runtime_type = str(data.get("type") or "")
        scope = str(data.get("scope") or "")
        agent_name = str(data.get("agentName") or data.get("agentId") or "")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        if runtime_type == "AGENT_FINAL_ANSWER_DELTA":
            result_parts.append(str(payload.get("delta") or ""))

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
        elif runtime_type in {"AGENT_OUTPUT_DELTA", "AGENT_FINAL_ANSWER_DELTA"}:
            progress = max(progress, 70)
            current_stage = "正在生成回复"
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
        if scope in {"leader", "single", "single_agent"} and agent_name and str(data.get("status") or "") in {"running", "waiting"}:
            if agent_name not in active_actors:
                active_actors.append(agent_name)

    if not runtime_events:
        return None

    member_total = len(member_status)
    member_completed = len([
        status for status in member_status.values()
        if status in {"completed", "failed"}
    ])
    if member_total:
        progress = max(progress, min(90, 25 + int((member_completed / member_total) * 55)))
        if run.status not in TERMINAL_RUN_STATUSES and run.status != "waiting":
            current_stage = f"Member 协作中：{member_completed}/{member_total} 完成"

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
        "memberTotal": member_total,
        "memberCompleted": member_completed,
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


def _run_state_sse(run: ChatBackgroundRun) -> str:
    """把当前 RunState 包装为稳定 SSE 事件。"""

    state = run.state or initial_run_state(run)
    return _sse_event("RUN_STATE", dump_run_state(normalize_run_state(state)))


def _run_state_patch_sse(
    run: ChatBackgroundRun,
    event_type: str,
    data: dict,
    *,
    display_event: dict | None = None,
) -> str:
    """把单次事件转换为轻量 RunState patch。"""

    state = run.state or initial_run_state(run)
    patch: dict[str, object] = {
        "kind": "display_event" if display_event else _run_state_patch_kind(event_type, data),
        "runId": run.id,
        "sessionId": run.session_id,
        "status": state.get("status") or run.status,
        "currentStage": state.get("currentStage") or "",
        "updatedAt": state.get("updatedAt") or "",
        "completedAt": state.get("completedAt"),
        "lastEventType": event_type,
        "eventCount": state.get("eventCount") or run.event_count,
    }
    if display_event:
        patch["displayEvent"] = display_event
    if event_type in {"MODEL_CALL_START", "MODEL_CALL_END"}:
        patch["modelName"] = state.get("modelName") or ""
        patch["tokenUsage"] = state.get("tokenUsage")
    elif event_type == SSE_EVENT_RUNTIME:
        runtime_type = str(data.get("type") or "")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        if runtime_type == "HITL_RESOLVED":
            interaction_id = str(payload.get("interactionId") or data.get("interactionId") or "")
            if interaction_id:
                patch["resolvedApprovalId"] = interaction_id
        elif runtime_type == "MEMORY_SKILL_GENERATING":
            patch["notice"] = {
                "type": runtime_type,
                "message": str(payload.get("message") or "Skills 生成中"),
            }
    elif event_type == SSE_EVENT_TEAM:
        patch["teamEvent"] = data
    elif event_type == "ERROR":
        patch["error"] = str(data.get("message") or state.get("error") or "执行失败")
    return _sse_event("RUN_STATE_PATCH", dump_run_state_patch(patch))


def _should_emit_run_state_metadata_patch(event_type: str, data: dict) -> bool:
    """判断没有展示事件时，是否仍需通知前端状态变化。"""

    if event_type in RUN_STATE_METADATA_PATCH_EVENTS:
        return True
    if event_type == SSE_EVENT_RUNTIME:
        runtime_type = str(data.get("type") or "")
        return runtime_type in {"HITL_RESOLVED", "MEMORY_SKILL_GENERATING"}
    if event_type == SSE_EVENT_TEAM:
        return True
    return False


def _run_state_patch_kind(event_type: str, data: dict) -> str:
    """把原始事件归类为前端可稳定分支的 patch kind。"""

    if event_type in {
        "REASONING_DELTA",
        "TEXT_BLOCK_DELTA",
        "FINAL_ANSWER_DELTA",
        "FINAL_ANSWER_REPLACE",
        "THINKING_BLOCK_DELTA",
        "TOOL_CALL_START",
        "TOOL_CALL_DELTA",
        "TOOL_CALL_END",
        "TOOL_RESULT_TEXT_DELTA",
        "TOOL_RESULT_END",
        "SKILL_CONTEXT",
        "SKILL_END",
        "HITL_REQUIRED",
        "CLARIFICATION_REQUIRED",
    }:
        return "display_event"
    if event_type in {"MODEL_CALL_START", "MODEL_CALL_END"}:
        return "model_usage"
    if event_type == SSE_EVENT_RUNTIME:
        runtime_type = str(data.get("type") or "")
        if runtime_type == "HITL_RESOLVED":
            return "approval_resolved"
        if runtime_type == "MEMORY_SKILL_GENERATING":
            return "notice"
        if runtime_type.startswith("PLAN_"):
            return "display_event"
        return "snapshot_meta"
    if event_type == SSE_EVENT_TEAM:
        return "team_event"
    if event_type == "ERROR":
        return "display_event"
    return "snapshot_meta"
