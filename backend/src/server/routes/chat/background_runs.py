"""基于 Event Stream V3 Projection 的后台 Chat Run 管理。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from src.config.runtime_settings import set_model_binding, reset_model_binding

from src.server.routes.chat.lifecycle import (
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    can_transition_run_status,
    is_waiting_run_status,
)
from src.server.routes.chat.projection import (
    BlockLifecycleCoordinator,
    DomainEventRepository,
    ProjectionRunner,
    ProjectionStreamService,
    ProjectionStreamStore,
    default_projection_registry,
)
from src.server.routes.chat.projection.frame_schema import RuntimeFrame, dump_protocol
from src.server.routes.chat.projection.projection_writer import ProjectionConflict
from src.server.routes.chat.projection.source_normalizer import ChatProjectionNormalizer
from src.server.routes.chat.run_dashboard import summarize_run_dashboard
from src.server.routes.chat.projection.source_events import SourceEvent
from src.server.routes.chat.store import (
    _append_assistant_message,
    _update_latest_assistant_display_snapshot,
)
from src.server.routes.chat.observation_runtime import (
    close_run_root,
    observe_source_event,
    open_run_root,
)
from src.storage.database import (
    get_chat_run,
    list_chat_runs,
    list_chat_runs_by_statuses,
    update_chat_run_status,
    upsert_chat_run,
)


SourceStreamFactory = Callable[[], AsyncGenerator[SourceEvent, None]]
CURRENT_BACKGROUND_RUN_ID: ContextVar[str] = ContextVar("current_chat_background_run_id", default="")
MAX_BACKGROUND_RUNS = 50
COMPLETED_RUN_TTL = timedelta(hours=2)
PROCESS_RESTART_ERROR = "进程已重启，请重新发送"

_domain_events = DomainEventRepository()
_projection_store = ProjectionStreamStore()
_coordinator = BlockLifecycleCoordinator()
_stream_service = ProjectionStreamService(store=_projection_store, coordinator=_coordinator)


def _current_tenant_id() -> str:
    from src.tenancy.context import current_tenant

    return current_tenant().tenant_id


@dataclass
class ChatBackgroundRun:
    """进程内执行句柄；展示状态的唯一真相在 Projection Stream。"""

    id: str
    session_id: str
    workspace_id: str = "default"
    project_id: str = "default"
    agent_id: str = ""
    agent_name: str = ""
    task_text: str = ""
    mode: str = "agent"
    trigger_source: str = "user"
    scheduled_job_id: str = ""
    scheduled_firing_id: str = ""
    status: str = "running"
    tenant_id: str = field(default_factory=_current_tenant_id)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    event_count: int = 0
    subscribers: set[asyncio.Queue[str | None]] = field(default_factory=set)
    async_task: asyncio.Task | None = None
    persisted: bool = False
    pending_steers: list[str] = field(default_factory=list)
    normalizer: ChatProjectionNormalizer | None = None
    model_binding: object | None = field(default=None, repr=False)
    model_metadata: dict = field(default_factory=dict)

    @property
    def stream_key(self) -> str:
        return f"chat:{self.id}"


_runs: dict[str, ChatBackgroundRun] = {}
_runs_lock = asyncio.Lock()


def _owned_run(run_id: str) -> ChatBackgroundRun | None:
    """内存里的 Run 必须属于当前租户，否则按不存在处理。"""

    run = _runs.get(run_id)
    if run is None or run.tenant_id != _current_tenant_id():
        return None
    return run


def current_chat_background_run_id() -> str:
    return CURRENT_BACKGROUND_RUN_ID.get()


def has_live_chat_background_task(run_id: str) -> bool:
    """本进程 _runs 中仍有未结束 Task 的 Chat Run。热重载不得误杀。"""

    run = _owned_run(run_id)
    if run is None:
        return False
    task = run.async_task
    return task is not None and not task.done()


def reconcile_interrupted_chat_runs() -> int:
    """把无活 Task 的 running/waiting_* 标 failed，error 提示进程重启。"""

    completed_at = datetime.now(timezone.utc).isoformat()
    count = 0
    for record in list_chat_runs_by_statuses(ACTIVE_RUN_STATUSES):
        run_id = str(record.get("id") or "")
        if not run_id or has_live_chat_background_task(run_id):
            continue
        update_chat_run_status(run_id, "failed", completed_at)
        in_memory = _owned_run(run_id)
        if in_memory is not None and in_memory.status in ACTIVE_RUN_STATUSES:
            in_memory.status = "failed"
            in_memory.completed_at = completed_at
        _append_process_restart_message(record)
        count += 1
    return count


def _append_process_restart_message(record: dict) -> None:
    session_id = str(record.get("sessionId") or "")
    if not session_id:
        return
    try:
        _append_assistant_message(
            session_id=session_id,
            agent_id=str(record.get("agentId") or ""),
            agent_name=str(record.get("agentName") or "Assistant"),
            model_name="",
            content="",
            error=PROCESS_RESTART_ERROR,
        )
    except Exception:
        return


async def get_chat_background_run(run_id: str) -> ChatBackgroundRun | None:
    async with _runs_lock:
        run = _owned_run(run_id)
    if run is not None:
        return run
    record = get_chat_run(run_id)
    return _run_from_persisted_record(record) if record else None


async def attach_chat_background_run_task(run_id: str, task: asyncio.Task) -> bool:
    """把 HITL resume 等后续执行任务绑定回原 Run，供 cancel 精确中止。"""

    async with _runs_lock:
        run = _owned_run(run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            task.cancel()
            return False
        run.async_task = task
        return True


async def list_chat_background_runs(
    project_id: str | None = None,
    trigger_source: str | None = None,
) -> list[ChatBackgroundRun]:
    async with _runs_lock:
        tenant_id = _current_tenant_id()
        by_id = {
            run.id: run
            for run in _runs.values()
            if run.tenant_id == tenant_id
            and (not project_id or run.project_id == project_id)
            and (not trigger_source or run.trigger_source == trigger_source)
        }
    for record in list_chat_runs(
        MAX_BACKGROUND_RUNS,
        project_id=project_id,
        trigger_source=trigger_source,
    ):
        if record["id"] not in by_id:
            by_id[record["id"]] = _run_from_persisted_record(record)
    return sorted(by_id.values(), key=lambda item: item.completed_at or item.created_at, reverse=True)


async def start_chat_background_run(
    *,
    session_id: str,
    factory: SourceStreamFactory,
    agent_id: str = "",
    agent_name: str = "",
    task: str = "",
    mode: str = "agent",
    workspace_id: str = "default",
    project_id: str = "default",
    persist: bool = True,
    trigger_source: str = "user",
    scheduled_job_id: str = "",
    scheduled_firing_id: str = "",
    model_binding=None,
) -> ChatBackgroundRun:
    """创建 Run、V3 stream 和根 message Block 后启动后台执行。"""

    run = ChatBackgroundRun(
        id=f"run-{uuid.uuid4().hex}",
        tenant_id=_current_tenant_id(),
        session_id=session_id,
        workspace_id=workspace_id,
        project_id=project_id,
        agent_id=agent_id,
        agent_name=agent_name,
        task_text=task,
        mode=mode,
        trigger_source=trigger_source or "user",
        scheduled_job_id=scheduled_job_id or "",
        scheduled_firing_id=scheduled_firing_id or "",
        model_binding=model_binding,
        model_metadata={
            "modelConfigId": model_binding.primary.entry.id,
            "providerId": model_binding.primary.provider,
            "modelName": model_binding.primary.default_model,
            "revision": model_binding.catalog.revision,
            "chain": [{"modelConfigId": item.entry.id, "providerId": item.provider,
                       "model": item.default_model, "adapter": item.adapter,
                       "baseUrl": item.base_url, "parameters": item.entry.parameters.model_dump(by_alias=True)}
                      for item in model_binding.chain],
        } if model_binding else {},
    )
    if persist:
        _persist_run(run)
        open_run_root(
            persist=True,
            run_id=run.id,
            session_id=run.session_id,
            task=task,
            workspace_id=workspace_id,
            project_id=project_id,
            agent_name=agent_name,
            mode=mode,
        )
    _projection_store.ensure_stream(run.stream_key, run.id)
    run.normalizer = ChatProjectionNormalizer(
        stream_key=run.stream_key,
        run_id=run.id,
        session_id=run.session_id,
        events=_domain_events,
    )
    run.event_count += len(run.normalizer.start(agent_name=agent_name))
    _run_projection(run)
    async with _runs_lock:
        _prune_runs_locked()
        _runs[run.id] = run
    run.async_task = asyncio.create_task(_consume_source_events(run, factory), name=f"chat-run-{run.id}")
    return run


async def subscribe_chat_background_run(
    run_id: str,
    *,
    after_seq: int | None = None,
) -> AsyncGenerator[str, None]:
    """订阅唯一 V3 stream，并按 afterSeq 恢复。"""
    queue: asyncio.Queue[str | None] | None = None
    async with _runs_lock:
        run = _owned_run(run_id)
        if run is not None:
            queue = asyncio.Queue()
            run.subscribers.add(queue)
            should_wait = run.status not in TERMINAL_RUN_STATUSES
        else:
            should_wait = False
    if run is None:
        record = get_chat_run(run_id)
        if record is None:
            return
        stream_key = f"chat:{run_id}"
    else:
        stream_key = run.stream_key

    try:
        batch = _stream_service.read(stream_key, after_seq=after_seq)
        for frame in batch.frames:
            yield _frame_sse(frame)
        if not should_wait or batch.stream_done or queue is None:
            return
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event
    finally:
        if queue is not None:
            async with _runs_lock:
                current = _owned_run(run_id)
                if current is not None:
                    current.subscribers.discard(queue)


def get_chat_projection_snapshot(run_id: str) -> dict:
    """返回物化到最新 last_seq 的 DisplaySnapshot V4。"""

    state = _projection_store.load_materialized(f"chat:{run_id}", _coordinator)
    return dump_protocol(state.snapshot)


def take_run_steers(run_id: str) -> list[str]:
    """取出本轮用户补充，供下一轮推理消费。"""

    run = _owned_run(run_id)
    if run is None or not run.pending_steers:
        return []
    notes = [item for item in run.pending_steers if str(item).strip()]
    run.pending_steers.clear()
    return notes


async def steer_chat_background_run(
    run_id: str,
    *,
    message: str,
    agent_id: str = "",
    agent_name: str = "",
) -> ChatBackgroundRun:
    """把用户补充写入进行中的 Run，不新开一轮。"""

    text = str(message or "").strip()
    if not text:
        raise ValueError("补充内容不能为空")
    async with _runs_lock:
        run = _owned_run(run_id)
    if run is None:
        raise KeyError(run_id)
    if run.status in TERMINAL_RUN_STATUSES:
        raise RuntimeError("已结束的 Run 不能再补充")
    run.pending_steers.append(text)
    from src.server.routes.chat.store import _append_user_message

    _append_user_message(
        session_id=run.session_id,
        agent_id=agent_id or run.agent_id,
        agent_name=agent_name or run.agent_name,
        mode=run.mode,
        model_name="",
        content=text,
        images=[],
        workspace_id=run.workspace_id,
        project_id=run.project_id,
    )
    return run


async def cancel_chat_background_run(run_id: str) -> ChatBackgroundRun | None:
    async with _runs_lock:
        run = _owned_run(run_id)
    if run is None:
        return await get_chat_background_run(run_id)
    if run.status in TERMINAL_RUN_STATUSES:
        return run
    run.status = "cancelled"
    run.completed_at = datetime.now(timezone.utc).isoformat()
    if run.normalizer is not None:
        await _project_terminal(run, "cancelled", {"completedAt": run.completed_at})
    update_chat_run_status(run.id, run.status, run.completed_at)
    if run.async_task is not None and not run.async_task.done():
        run.async_task.cancel()
    await _close_subscribers(run)
    return run


async def mark_chat_background_run_status(run_id: str, status: str) -> ChatBackgroundRun | None:
    async with _runs_lock:
        run = _owned_run(run_id)
    if run is None or not can_transition_run_status(run.status, status):
        return run
    run.status = status
    run.completed_at = None if status == "running" else run.completed_at
    update_chat_run_status(run.id, run.status, run.completed_at)
    return run


async def mark_chat_background_run_resuming(
    run_id: str,
    *,
    interaction_id: str = "",
    action: str = "approve",
    external_results_preview: dict[str, str] | None = None,
) -> ChatBackgroundRun | None:
    """恢复同一 Run；interaction 终态仍由 resume 源信号进入 Domain Stream。"""

    _ = interaction_id, action, external_results_preview
    return await mark_chat_background_run_status(run_id, "running")


async def emit_chat_background_runtime_event(run_id: str, event: SourceEvent) -> bool:
    async with _runs_lock:
        run = _owned_run(run_id)
    if run is None:
        return False
    await _ingest_source_event(run, event)
    return True


async def relay_chat_background_run_events(
    run_id: str,
    events: AsyncGenerator[SourceEvent, None],
) -> AsyncGenerator[str, None]:
    """Resume 产生的源信号写回同一 projection seq 空间，并只向 HTTP 返回 V3 Frame。"""

    run = await get_chat_background_run(run_id)
    if run is None or run.normalizer is None:
        raise RuntimeError(f"Chat Run {run_id} 没有可恢复的活动 projection context")
    await mark_chat_background_run_status(run_id, "running")
    token = CURRENT_BACKGROUND_RUN_ID.set(run_id)
    model_token = set_model_binding(run.model_binding)
    try:
        async for event in events:
            frames = await _ingest_source_event(run, event)
            for frame in frames:
                yield frame
        if run.status not in TERMINAL_RUN_STATUSES and not is_waiting_run_status(run.status):
            for frame in await _project_terminal(run, "completed", {}):
                yield frame
    except asyncio.CancelledError:
        if run.status != "cancelled":
            for frame in await _project_terminal(run, "cancelled", {}):
                yield frame
        raise
    except Exception as exc:
        from src.exception_detail import format_agent_exception

        message = format_agent_exception(exc, prefix="Agent 执行异常")
        for frame in await _ingest_source_event(run, _source_error_signal(message)):
            yield frame
        for frame in await _project_terminal(run, "failed", {"message": message}):
            yield frame
    finally:
        _finalize_run_persistence(run)
        if run.status in TERMINAL_RUN_STATUSES:
            await _close_subscribers(run)
        CURRENT_BACKGROUND_RUN_ID.reset(token)
        reset_model_binding(model_token)


def serialize_chat_background_run(run: ChatBackgroundRun) -> dict:
    dashboard = summarize_run_dashboard(
        status=run.status,
        agent_name=run.agent_name,
        mode=run.mode,
        snapshot=_dashboard_snapshot(run.id),
    )
    return {
        "id": run.id,
        "sessionId": run.session_id,
        "workspaceId": run.workspace_id,
        "projectId": run.project_id,
        "agentId": run.agent_id,
        "agentName": run.agent_name,
        "task": run.task_text,
        "mode": run.mode,
        **{key: run.model_metadata.get(key, "") for key in ("modelConfigId", "providerId", "modelName")},
        "triggerSource": run.trigger_source,
        "scheduledJobId": run.scheduled_job_id,
        "scheduledFiringId": run.scheduled_firing_id,
        "status": run.status,
        "createdAt": run.created_at,
        "completedAt": run.completed_at,
        "eventCount": run.event_count,
        "subscriberCount": len(run.subscribers),
        **dashboard,
    }


def _dashboard_snapshot(run_id: str) -> dict | None:
    """Runs 汇总只读 Snapshot；没有投影流时退回状态机。"""

    try:
        return get_chat_projection_snapshot(run_id)
    except (KeyError, ProjectionConflict):
        return None


async def _consume_source_events(run: ChatBackgroundRun, factory: SourceStreamFactory) -> None:
    token = CURRENT_BACKGROUND_RUN_ID.set(run.id)
    model_token = set_model_binding(run.model_binding)
    try:
        async for event in factory():
            await _ingest_source_event(run, event)
        if run.status not in TERMINAL_RUN_STATUSES and not is_waiting_run_status(run.status):
            await _project_terminal(run, "completed", {})
    except asyncio.CancelledError:
        if run.status != "cancelled":
            await _project_terminal(run, "cancelled", {})
        raise
    except Exception as exc:
        from src.exception_detail import format_agent_exception

        message = format_agent_exception(exc, prefix="Agent 执行异常")
        await _ingest_source_event(run, _source_error_signal(message))
        await _project_terminal(run, "failed", {"message": message})
    finally:
        _finalize_run_persistence(run)
        if run.status in TERMINAL_RUN_STATUSES:
            await _close_subscribers(run)
        CURRENT_BACKGROUND_RUN_ID.reset(token)
        reset_model_binding(model_token)


async def _ingest_source_event(run: ChatBackgroundRun, source_event: SourceEvent) -> list[str]:
    if run.normalizer is None or run.status in TERMINAL_RUN_STATUSES:
        return []
    event_type, data = source_event.event_type, source_event.data
    _observe_run_source(run, event_type, data)
    previous_seq = _projection_store.load_materialized(run.stream_key, _coordinator).last_seq
    produced = run.normalizer.ingest(event_type, data)
    if event_type == "ERROR":
        produced.extend(run.normalizer.finish("failed", data))
    run.event_count += len(produced)
    _update_status_from_signal(run, event_type, data)
    if produced:
        _run_projection(run)
    frames = [_frame_sse(frame) for frame in _projection_store.frames_after(run.stream_key, previous_seq)]
    for payload in frames:
        await _publish(run, payload)
    if is_waiting_run_status(run.status) and event_type in {
        "HITL_REQUIRED",
        "CLARIFICATION_REQUIRED",
    }:
        _persist_display_snapshot(run)
    return frames


async def _project_terminal(run: ChatBackgroundRun, outcome: str, data: dict) -> list[str]:
    if run.normalizer is None:
        return []
    previous_seq = _projection_store.load_materialized(run.stream_key, _coordinator).last_seq
    produced = run.normalizer.finish(outcome, data)
    run.event_count += len(produced)
    close_run_root(
        persist=run.persisted,
        run_id=run.id,
        outcome=outcome if outcome in {"completed", "failed", "cancelled"} else run.status,
        output=_run_observation_output(run, outcome, data),
    )
    if not produced:
        return []
    _run_projection(run)
    run.status = outcome
    run.completed_at = datetime.now(timezone.utc).isoformat()
    payloads = [_frame_sse(frame) for frame in _projection_store.frames_after(run.stream_key, previous_seq)]
    for payload in payloads:
        await _publish(run, payload)
    return payloads


def _run_projection(run: ChatBackgroundRun) -> None:
    ProjectionRunner(
        events=_domain_events,
        store=_projection_store,
        registry=default_projection_registry(),
        coordinator=_coordinator,
    ).run_available(run.stream_key, run.id)


async def _publish(run: ChatBackgroundRun, event: str) -> None:
    for queue in list(run.subscribers):
        await queue.put(event)


async def _close_subscribers(run: ChatBackgroundRun) -> None:
    for queue in list(run.subscribers):
        await queue.put(None)


def _update_status_from_signal(run: ChatBackgroundRun, event_type: str, data: dict) -> None:
    runtime_type = event_type
    status = ""
    if runtime_type == "RUN_WAITING" or event_type in {"HITL_REQUIRED", "CLARIFICATION_REQUIRED"}:
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        status = str(payload.get("waitState") or data.get("status") or "waiting_user")
        if not is_waiting_run_status(status):
            status = "waiting_user"
    elif runtime_type == "RUN_COMPLETED":
        status = "completed"
    elif runtime_type == "RUN_FAILED":
        status = "failed"
    elif runtime_type == "RUN_CANCELLED":
        status = "cancelled"
    elif runtime_type in {"RUN_RESUMED", "RUN_STARTED"}:
        status = "running"
    elif event_type == "ERROR":
        status = "failed"
    if status and can_transition_run_status(run.status, status):
        run.status = status
        update_chat_run_status(run.id, status, run.completed_at)


def _observe_run_source(run: ChatBackgroundRun, event_type: str, data: dict) -> None:
    """把源信号映射为 Observation；失败不影响投影。"""

    try:
        observe_source_event(
            persist=run.persisted,
            run_id=run.id,
            session_id=run.session_id,
            event_type=event_type,
            data=data if isinstance(data, dict) else {},
            workspace_id=run.workspace_id,
            project_id=run.project_id,
        )
    except Exception as exc:
        print(f"[TraceObservation] ingest failed run={run.id} type={event_type}: {exc}")


def _run_observation_output(run: ChatBackgroundRun, outcome: str, data: dict) -> dict:
    """从当前 Snapshot 抽取最终回答，供根 Observation output。"""

    answer = ""
    try:
        snapshot = get_chat_projection_snapshot(run.id)
        blocks = snapshot.get("blocksById") if isinstance(snapshot.get("blocksById"), dict) else {}
        answer_generation = ""
        for block_id in snapshot.get("order") or []:
            block = blocks.get(block_id)
            if not isinstance(block, dict) or block.get("kind") != "message":
                continue
            payload = block.get("data") if isinstance(block.get("data"), dict) else {}
            candidate = str(payload.get("answerGeneration") or "")
            if candidate:
                answer_generation = candidate
        for block_id in snapshot.get("order") or []:
            block = blocks.get(block_id)
            if not isinstance(block, dict) or block.get("kind") != "text":
                continue
            if answer_generation and block_id != answer_generation:
                continue
            payload = block.get("data")
            if isinstance(payload, str):
                text = payload.strip()
            elif isinstance(payload, dict):
                text = str(payload.get("text") or payload.get("content") or "").strip()
            else:
                text = ""
            if text:
                answer = text
                if answer_generation:
                    break
    except Exception:
        answer = ""
    return {
        "answer": answer,
        "error": str(data.get("message") or data.get("error") or ""),
        "outcome": outcome,
    }


def _persist_display_snapshot(run: ChatBackgroundRun) -> None:
    try:
        state = _projection_store.load_materialized(run.stream_key, _coordinator)
    except KeyError:
        return
    _update_latest_assistant_display_snapshot(
        session_id=run.session_id,
        display_snapshot=dump_protocol(state.snapshot),
        min_created_at=run.created_at,
    )


def _finalize_run_persistence(run: ChatBackgroundRun) -> None:
    """把终态元数据和最终 DisplaySnapshot 一次性写回历史消息。"""

    if run.status not in TERMINAL_RUN_STATUSES:
        return
    run.completed_at = run.completed_at or datetime.now(timezone.utc).isoformat()
    update_chat_run_status(run.id, run.status, run.completed_at)
    close_run_root(
        persist=run.persisted,
        run_id=run.id,
        outcome=run.status,
        output=_run_observation_output(run, run.status, {}),
    )
    _persist_display_snapshot(run)


def _frame_sse(frame: RuntimeFrame) -> str:
    payload = json.dumps(dump_protocol(frame), ensure_ascii=False, separators=(",", ":"))
    return f"event: RUNTIME_EVENT\ndata: {payload}\n\n"


def _source_error_signal(message: str) -> SourceEvent:
    return SourceEvent("ERROR", {"message": message})


def _persist_run(run: ChatBackgroundRun) -> None:
    upsert_chat_run({
        "id": run.id,
        "modelConfig": run.model_metadata,
        "sessionId": run.session_id,
        "workspaceId": run.workspace_id,
        "projectId": run.project_id,
        "agentId": run.agent_id,
        "agentName": run.agent_name,
        "task": run.task_text,
        "mode": run.mode,
        "status": run.status,
        "triggerSource": run.trigger_source,
        "scheduledJobId": run.scheduled_job_id,
        "scheduledFiringId": run.scheduled_firing_id,
        "createdAt": run.created_at,
        "completedAt": run.completed_at,
    })
    run.persisted = True


def _run_from_persisted_record(record: dict) -> ChatBackgroundRun:
    return ChatBackgroundRun(
        id=record["id"],
        tenant_id=_current_tenant_id(),
        model_metadata=record.get("modelConfig") or {},
        session_id=record["sessionId"],
        workspace_id=record.get("workspaceId", "default"),
        project_id=record.get("projectId", "default"),
        agent_id=record.get("agentId", ""),
        agent_name=record.get("agentName", ""),
        task_text=record.get("task", ""),
        mode=record.get("mode", "agent"),
        trigger_source=record.get("triggerSource") or "user",
        scheduled_job_id=record.get("scheduledJobId") or "",
        scheduled_firing_id=record.get("scheduledFiringId") or "",
        status=record.get("status", "completed"),
        created_at=record.get("createdAt") or datetime.now(timezone.utc).isoformat(),
        completed_at=record.get("completedAt"),
        event_count=int(record.get("eventCount") or 0),
        persisted=True,
    )


def _prune_runs_locked() -> None:
    now = datetime.now(timezone.utc)
    removable: list[str] = []
    for run_id, run in _runs.items():
        if run.status in ACTIVE_RUN_STATUSES or run.subscribers:
            continue
        if run.completed_at:
            try:
                completed_at = datetime.fromisoformat(run.completed_at)
            except ValueError:
                completed_at = now
            if now - completed_at > COMPLETED_RUN_TTL:
                removable.append(run_id)
    for run_id in removable:
        _runs.pop(run_id, None)
    if len(_runs) <= MAX_BACKGROUND_RUNS:
        return
    candidates = sorted(
        [run for run in _runs.values() if run.status not in ACTIVE_RUN_STATUSES and not run.subscribers],
        key=lambda item: item.completed_at or item.created_at,
    )
    for run in candidates[:len(_runs) - MAX_BACKGROUND_RUNS]:
        _runs.pop(run.id, None)
