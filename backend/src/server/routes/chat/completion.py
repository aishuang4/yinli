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
from src.project_files import list_project_session_artifacts
from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.helpers import _set_skill_run_status, source_event
from src.server.routes.chat.models import ChatModelCacheRun, ChatSkillRun, ChatToolRun
from src.server.routes.chat.runtime_events import runtime_trace_source_event
from src.server.routes.chat.skill_runs import update_skill_run_status
from src.server.routes.chat.store import _append_assistant_message
from src.server.routes.chat.trace_recorder import _record_run_event, _trim_text
from src.config.runtime_paths import default_project_root
from src.storage.database import DEFAULT_PROJECT_ID, get_latest_completed_user_assistant_turn
from src.runtime_context import RuntimeContext, resolve_runtime_context


def _permission_summary(run_id: str) -> dict:
    """完成事件使用和 Trace、展示快照相同的权限事实。"""

    if not run_id:
        return {"outcome": "complete", "counts": {}, "items": []}
    try:
        from src.storage.permission_records import PermissionRecordStore
        return PermissionRecordStore().summarize_run(run_id)
    except Exception:
        return {"outcome": "complete", "counts": {}, "items": []}


def missing_final_answer_error() -> str:
    """生成没有最终答案时返回给前端的错误说明。"""

    return (
        "Agent 执行已结束，但没有生成最终答案。"
        "请查看 Run Trace 中最后一个失败事件，或重新发起该任务。"
    )


def max_react_iterations_error() -> str:
    """生成达到最大 ReAct 迭代次数时返回给前端的错误说明。"""

    return "Agent 已达到最大 ReAct 迭代次数，执行已停止。请缩小任务范围或提高最大迭代次数后重试。"


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
) -> AsyncGenerator[str, None]:
    """启动阶段失败的统一持久化和 UI 事件。"""

    failed_skill_runs = _set_skill_run_status(skill_runs, "failed")
    if persist:
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
    runtime_context: RuntimeContext | None = None,
    session_workspace_dir: str = "",
    session_artifact_paths_before: list[str] | None = None,
    sandbox: bool = False,
    review_context: object | None = None,
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
    resolved_runtime_context = runtime_context
    if resolved_runtime_context is None and persist:
        resolved_runtime_context = resolve_runtime_context(
            project_id=None,
            session_id=session_id,
            run_id=source_run_id,
            agent_id=agent_id,
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
    if persist:
        _append_assistant_message(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            content=assistant_text,
            error=None,
            thinking=assistant_thinking,
            skill_calls=completed_skill_runs,
            tool_calls=assistant_tool_calls,
            model_caches=assistant_model_caches,
            runtime_events=runtime_events,
        )
        _record_run_event(
            session_id=session_id,
            event_type="ASSISTANT_MESSAGE",
            persist=persist,
            scope="leader",
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            title="Assistant response",
            content=_trim_text(assistant_text, 1200),
            payload={
                "model": model_name,
                "toolCallCount": len(assistant_tool_calls),
                "skillIds": [item.id for item in completed_skill_runs],
                "permissionSummary": _permission_summary(source_run_id),
            },
        )
        reflection_input = _build_memory_reflection_input(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            user_message=user_message,
            assistant_text=assistant_text,
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
    runtime_context: RuntimeContext | None = None,
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
                workspace_id=resolved_context.workspace_id,
                project_id=resolved_context.project_id,
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
        workspace_id=resolved_context.workspace_id,
        project_id=resolved_context.project_id,
    )


def _default_runtime_context(*, session_id: str, run_id: str, agent_id: str) -> RuntimeContext:
    """为不持久化运行和纯函数测试构造稳定的默认归属。"""

    return RuntimeContext(
        workspace_id="default",
        project_id=DEFAULT_PROJECT_ID,
        project_root=default_project_root(),
        session_id=session_id,
        run_id=run_id,
        agent_id=agent_id,
        project_name="Default Project",
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
        payload={"model": model_name},
    )
    if persist:
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
            reason="agent_error",
            payload={"message": assistant_error},
        )
    yield source_event("ERROR", {
        "message": assistant_error,
    })
