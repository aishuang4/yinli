"""Chat 运行完成/失败后的持久化与收尾。"""

import asyncio
from typing import AsyncGenerator

from src.exception_detail import format_agent_exception
from src.memory.professional_update import schedule_professional_update
from src.memory.reflection import (
    MemoryReflectionInput,
    is_manual_skill_generation_request,
    reflect_completed_run_memory_with_agents,
)
from src.memory.skill_evolution import evaluate_skill_evolution_router_gate
from src.memory.trace import (
    MEMORY_SKILL_GENERATING_EVENT_TYPE,
    memory_reflection_audit_payload,
    memory_skill_generating_payload,
    record_memory_audit_event,
)
from src.sessions.files import list_project_session_artifacts
from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.helpers import _set_skill_run_status, source_event
from src.server.routes.chat.models import ChatModelCacheRun, ChatSkillRun, ChatToolRun
from src.server.routes.chat.runtime_events import runtime_trace_source_event
from src.server.routes.chat.skill_runs import update_skill_run_status
from src.server.routes.chat.store import _append_assistant_message
from src.server.routes.chat.trace_recorder import _record_run_event, _trim_text
from src.config.runtime_paths import tenant_root
from src.storage.database import get_latest_completed_user_assistant_turn
from src.storage.permission_records import PermissionRecordStore
from src.runtime.session_context import SessionContext, build_session_context


def missing_final_answer_error() -> str:
    """生成没有最终答案时返回给前端的错误说明。"""

    return (
        "Agent 执行已结束，但没有生成最终答案。"
        "请查看 Run Trace 中最后一个失败事件，或重新发起该任务。"
    )


def max_react_iterations_error() -> str:
    """生成达到最大 ReAct 迭代次数时返回给前端的错误说明。"""

    return "Agent 已达到最大 ReAct 迭代次数，执行已停止。请缩小任务范围或提高最大迭代次数后重试。"


def _permission_outcome(run_id: str) -> dict | None:
    """只从持久执行事实读取本轮部分成功摘要。"""

    if not str(run_id or "").strip():
        return None
    try:
        summary = PermissionRecordStore().summarize_run(run_id)
    except (OSError, ValueError):
        return None
    return summary if summary.get("items") else None


def _append_partial_execution_notice(text: str, outcome: dict | None) -> str:
    """部分执行时把已落盘/未执行/结果不明写入最终用户可见答复。"""

    if not outcome or outcome.get("outcome") != "partial":
        return text
    counts = outcome.get("counts") or {}
    return (
        f"{text.rstrip()}\n\n执行结果为部分完成：已成功 {counts.get('succeeded', 0)} 项，"
        f"失败 {counts.get('failed', 0)} 项，未执行 {counts.get('denied', 0)} 项，"
        f"结果不明 {counts.get('unknown', 0)} 项。已成功写入的内容已保留，请按明细继续处理。"
    ).strip()


def _trace_tool_records(tool_calls: list[ChatToolRun]) -> list[dict[str, str]]:
    """firing 不進 Chat 主訊息時，把工具記錄留在 Trace。"""

    return [
        {
            "id": item.id,
            "name": item.name,
            "status": item.status,
            "args": _trim_text(item.args, 500),
            "result": _trim_text(item.result, 500),
        }
        for item in tool_calls
    ]


async def emit_failed_startup_run(
    *,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    assistant_error: str,
    skill_runs: list[ChatSkillRun],
    assistant_model_caches: list[ChatModelCacheRun] | None = None,
    sandbox: bool = False,
    record_chat_message: bool = True,
) -> AsyncGenerator[str, None]:
    """启动阶段失败的统一持久化和 UI 事件。"""

    failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
    if persist and record_chat_message:
        _append_assistant_message(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            content="",
            error=assistant_error,
            skill_calls=failed_skill_runs,
            model_caches=assistant_model_caches or [],
        )
    yield runtime_trace_source_event(
        "RUN_FAILED",
        session_id=session_id,
        scope="run",
        agent_id=agent_id,
        agent_name=agent_name,
        status="failed",
        reason="startup_error",
        payload={"message": assistant_error},
    )
    if sandbox:
        yield runtime_trace_source_event(
            "SANDBOX_FAILED",
            session_id=session_id,
            scope="sandbox",
            agent_id=agent_id,
            agent_name=agent_name,
            status="failed",
            reason="startup_error",
            payload={"message": assistant_error},
        )
    if failed_skill_runs:
        for skill in failed_skill_runs:
            yield runtime_trace_source_event(
                "SKILL_FAILED",
                session_id=session_id,
                scope="skill",
                agent_id=agent_id,
                agent_name=agent_name,
                status="failed",
                payload=skill.model_dump(by_alias=True),
            )
        yield source_event("SKILL_END", {
            "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
            "status": "failed",
        })
    yield source_event("ERROR", {
        "message": assistant_error,
    })


async def finalize_successful_leader_run(
    *,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    user_message: str,
    assistant_text: str,
    assistant_thinking: str,
    assistant_tool_calls: list[ChatToolRun],
    assistant_model_caches: list[ChatModelCacheRun],
    runtime_events: list[dict],
    skill_runs: list[ChatSkillRun],
    active_skill_run: dict | None,
    runtime_context: SessionContext | None = None,
    session_workspace_dir: str = "",
    session_artifact_paths_before: list[str] | None = None,
    sandbox: bool = False,
    review_context: object | None = None,
    record_chat_message: bool = True,
) -> AsyncGenerator[str, None]:
    """完成 Leader run 成功路径的消息、trace 和 UI 事件。"""

    completed_skill_runs = _set_skill_run_status(skill_runs, "completed")
    if active_skill_run:
        update_skill_run_status(
            active_skill_run,
            status="completed",
            current_step="completed",
            pending_interaction_id="",
        )
    source_run_id = current_chat_background_run_id()
    if persist and session_id:
        from src.runtime.session_context import build_session_context
        from src.sessions.intake import ensure_session_for_message

        ensure_session_for_message(
            session_id=session_id,
            agent_id=agent_id,
        )
        owner = build_session_context(session_id, agent_id=agent_id, run_id=source_run_id)
        if owner.session_id != session_id:
            raise RuntimeError("消息、Run、Trace 與 Artifact 必須屬於同一個 Session")
    execution_outcome = _permission_outcome(source_run_id)
    final_assistant_text = _append_partial_execution_notice(assistant_text, execution_outcome)
    resolved_runtime_context = runtime_context
    if resolved_runtime_context is None and persist and session_id:
        resolved_runtime_context = build_session_context(
            session_id,
            agent_id=agent_id,
            run_id=source_run_id,
        )
    if resolved_runtime_context is None:
        resolved_runtime_context = _default_runtime_context(
            session_id=session_id,
            run_id=source_run_id,
            agent_id=agent_id,
        )
    previous_artifact_paths = set(session_artifact_paths_before or [])
    session_artifacts = [
        artifact
        for artifact in list_project_session_artifacts(
        project_root=resolved_runtime_context.project_root,
        session_id=session_id,
        session_workspace_dir=session_workspace_dir,
        )
        if str(artifact.get("path") or "") not in previous_artifact_paths
    ]
    if persist and session_artifacts:
        from src.sessions.errors import SessionError
        from src.sessions.file_paths import resolve_managed_file
        from src.sessions.files import catalog_output

        for artifact in session_artifacts:
            try:
                produced = resolve_managed_file(session_id, str(artifact.get("path") or ""))
            except SessionError:
                continue
            catalog_output(session_id=session_id, run_id=source_run_id, path=produced, kind="managed")
    if persist and record_chat_message:
        _append_assistant_message(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            content=final_assistant_text,
            error=None,
            thinking=assistant_thinking,
            skill_calls=completed_skill_runs,
            tool_calls=assistant_tool_calls,
            model_caches=assistant_model_caches,
            runtime_events=runtime_events,
        )
    if persist:
        _record_run_event(
            session_id=session_id,
            event_type="ASSISTANT_MESSAGE",
            persist=persist,
            scope="leader",
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            title="Assistant response",
            content=_trim_text(final_assistant_text, 1200),
            payload={
                "model": model_name,
                "toolCallCount": len(assistant_tool_calls),
                "skillIds": [item.id for item in completed_skill_runs],
                **(
                    {"toolCalls": _trace_tool_records(assistant_tool_calls)}
                    if not record_chat_message
                    else {}
                ),
            },
        )
        reflection_input = _build_memory_reflection_input(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            user_message=user_message,
            assistant_text=final_assistant_text,
            source_run_id=source_run_id,
            tool_calls=assistant_tool_calls,
            runtime_events=runtime_events,
            runtime_context=resolved_runtime_context,
            review_context=review_context,
            skill_runs=[*completed_skill_runs, *([active_skill_run] if active_skill_run else [])],
        )
        _schedule_skill_evolution(
            reflection_input,
            model_name=model_name,
            persist=persist,
        )
        schedule_professional_update(
            agent_id=agent_id,
            agent_name=agent_name,
            user_message=user_message,
            assistant_text=assistant_text,
            model_name=model_name,
            session_id=session_id,
            persist_audit=persist,
        )
        if _should_emit_skill_generating_notice(reflection_input):
            yield runtime_trace_source_event(
                MEMORY_SKILL_GENERATING_EVENT_TYPE,
                session_id=session_id,
                run_id=source_run_id,
                scope="memory",
                source="memory",
                agent_id=agent_id,
                agent_name=agent_name,
                status="running",
                payload=memory_skill_generating_payload(),
            )
    if session_artifacts:
        yield runtime_trace_source_event(
            "ARTIFACT_CREATED",
            session_id=session_id,
            run_id=source_run_id,
            scope="artifact",
            source="runtime",
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            payload={"files": session_artifacts},
        )
    yield runtime_trace_source_event(
        "RUN_COMPLETED",
        session_id=session_id,
        scope="run",
        agent_id=agent_id,
        agent_name=agent_name,
        status="completed",
        payload={
            "model": model_name,
            "toolCallCount": len(assistant_tool_calls),
            **({"executionOutcome": execution_outcome} if execution_outcome else {}),
        },
    )
    if sandbox:
        yield runtime_trace_source_event(
            "SANDBOX_COMPLETED",
            session_id=session_id,
            scope="sandbox",
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            payload={
                "model": model_name,
                "toolCallCount": len(assistant_tool_calls),
                **({"executionOutcome": execution_outcome} if execution_outcome else {}),
            },
        )


def _schedule_skill_evolution(
    item: MemoryReflectionInput,
    *,
    model_name: str,
    persist: bool,
) -> None:
    """后台只跑普通技能自进化，不阻塞 Chat 成功收尾。"""

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            _run_skill_evolution_background(item, model_name=model_name, persist=persist),
            name=f"skill-evolution-{item.session_id}-{item.agent_id}",
        )
    except RuntimeError:
        pass


async def _run_skill_evolution_background(
    item: MemoryReflectionInput,
    *,
    model_name: str,
    persist: bool,
) -> None:
    """后台运行 Skill 自进化，异常只记录为内部失败。"""

    try:
        result = await reflect_completed_run_memory_with_agents(
            item,
            model_name=model_name,
            include_markdown=False,
        )
        payload = memory_reflection_audit_payload(result)
        updated = int(payload.get("markdownUpdateCount") or 0)
        skill_status = str(payload.get("skillEvolutionStatus") or "")
        status = "completed" if updated or skill_status else "skipped"
        record_memory_audit_event(
            session_id=item.session_id,
            persist=persist,
            agent_id=item.agent_id,
            agent_name=item.agent_name,
            action="memory_reflection",
            status=status,
            title="Memory reflection completed" if status == "completed" else "Memory reflection skipped",
            content=payload.get("ignoredReason", ""),
            payload={
                **payload,
                "sourceRunId": item.source_run_id,
                "sourceMessageId": item.source_message_id,
            },
        )
    except Exception as exc:
        record_memory_audit_event(
            session_id=item.session_id,
            persist=persist,
            agent_id=item.agent_id,
            agent_name=item.agent_name,
            action="memory_reflection",
            status="failed",
            title="Memory reflection failed",
            content=format_agent_exception(exc, prefix="记忆反思失败"),
            payload={
                "sourceRunId": item.source_run_id,
                "sourceMessageId": item.source_message_id,
            },
        )


def _should_emit_skill_generating_notice(item: MemoryReflectionInput) -> bool:
    """判断本轮是否会进入 Skill 自进化生成阶段。"""

    if item.manual_skill_request:
        return True
    gate = evaluate_skill_evolution_router_gate(
        user_message=item.user_message,
        assistant_text=item.assistant_text,
        tool_calls=item.tool_calls,
        runtime_events=item.runtime_events,
    )
    return gate.should_evaluate


def _build_memory_reflection_input(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    user_message: str,
    assistant_text: str,
    source_run_id: str,
    tool_calls: list[ChatToolRun],
    runtime_events: list[dict],
    runtime_context: SessionContext | None = None,
    review_context: object | None = None,
    skill_runs: list | None = None,
) -> MemoryReflectionInput:
    """构建运行后反思输入，显式 Skill 请求会切换到上一轮完成工作。"""

    resolved_context = runtime_context or _default_runtime_context(
        session_id=session_id,
        run_id=source_run_id,
        agent_id=agent_id,
    )

    from src.memory.model_review import turn_only_snapshot

    context = review_context or turn_only_snapshot(
        user_message=user_message,
        assistant_text=assistant_text,
        skill_runs=skill_runs,
    )
    if is_manual_skill_generation_request(user_message):
        previous_turn = get_latest_completed_user_assistant_turn(
            session_id,
            before_user_content=user_message,
        )
        if previous_turn and previous_turn.get("userMessage") and previous_turn.get("assistantText"):
            return MemoryReflectionInput(
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                user_message=str(previous_turn["userMessage"]),
                assistant_text=str(previous_turn["assistantText"]),
                source_run_id=source_run_id,
                source_message_id=str(previous_turn.get("userMessageId") or ""),
                tool_calls=list(previous_turn.get("toolCalls") or []),
                runtime_events=list(previous_turn.get("runtimeEvents") or []),
                manual_skill_request=True,
                review_context=context,
                workspace_id="",
                project_id="",
            )

    return MemoryReflectionInput(
        session_id=session_id,
        agent_id=agent_id,
        agent_name=agent_name,
        user_message=user_message,
        assistant_text=assistant_text,
        source_run_id=source_run_id,
        tool_calls=tool_calls,
        runtime_events=runtime_events,
        review_context=context,
        workspace_id="",
        project_id="",
    )


def _default_runtime_context(*, session_id: str, run_id: str, agent_id: str) -> SessionContext:
    """为不持久化运行和纯函数测试构造稳定的默认归属。"""

    root = tenant_root()
    return SessionContext(
        tenant_id="",
        session_id=session_id,
        session_kind="chat",
        session_root=root,
        working_directory=root,
        allowed_roots=(),
        directory_grant_version=0,
        resource_grant_version=0,
        agent_id=agent_id,
        run_id=run_id,
        run_snapshot_id="",
    )


async def finalize_failed_leader_run(
    *,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    assistant_text: str,
    assistant_error: str,
    assistant_model_caches: list[ChatModelCacheRun],
    skill_runs: list[ChatSkillRun],
    sandbox: bool = False,
    record_chat_message: bool = True,
) -> AsyncGenerator[str, None]:
    """完成 Leader run 失败路径的持久化与 UI 事件。"""

    failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
    _record_run_event(
        session_id=session_id,
        event_type="ERROR",
        persist=persist,
        scope="leader",
        agent_id=agent_id,
        agent_name=agent_name,
        status="failed",
        title="Agent execution failed",
        content=assistant_error,
        payload={"model": model_name, **({"executionOutcome": _permission_outcome(current_chat_background_run_id())}
                                         if _permission_outcome(current_chat_background_run_id()) else {})},
    )
    if persist and record_chat_message:
        _append_assistant_message(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            content=assistant_text,
            error=assistant_error,
            skill_calls=failed_skill_runs,
            model_caches=assistant_model_caches,
        )
    if failed_skill_runs:
        for skill in failed_skill_runs:
            yield runtime_trace_source_event(
                "SKILL_FAILED",
                session_id=session_id,
                scope="skill",
                agent_id=agent_id,
                agent_name=agent_name,
                status="failed",
                payload=skill.model_dump(by_alias=True),
            )
        yield source_event("SKILL_END", {
            "skills": [item.model_dump(by_alias=True) for item in failed_skill_runs],
            "status": "failed",
        })
    yield runtime_trace_source_event(
        "RUN_FAILED",
        session_id=session_id,
        scope="run",
        agent_id=agent_id,
        agent_name=agent_name,
        status="failed",
        reason="agent_error",
        payload={"message": assistant_error, **({"executionOutcome": _permission_outcome(current_chat_background_run_id())}
                                                 if _permission_outcome(current_chat_background_run_id()) else {})},
    )
    if sandbox:
        yield runtime_trace_source_event(
            "SANDBOX_FAILED",
            session_id=session_id,
            scope="sandbox",
            agent_id=agent_id,
            agent_name=agent_name,
            status="failed",
            reason="agent_error",
            payload={"message": assistant_error},
        )
    yield source_event("ERROR", {
        "message": assistant_error,
    })
