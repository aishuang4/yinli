"""定时任务启动 Chat Run 的窄实现。

调度器只应通过 src.runtime.scheduled_chat 调用，不要自己拼启动事件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncGenerator

from src.server.routes.chat.background_runs import (
    current_chat_background_run_id,
    start_chat_background_run,
)
from src.server.routes.chat.completion import emit_failed_startup_run
from src.server.routes.chat.initial_events import (
    emit_context_events,
    emit_start_events,
    persist_user_message_and_trace,
)
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.projection.source_events import SourceEvent
from src.server.routes.chat.run_context import build_scheduled_run_context
from src.server.routes.chat.runtime import _build_runtime_guardrails, scope_guardrails_to_project
from src.server.routes.chat.runtime_dispatcher import execute_chat_runtime
from src.server.routes.chat.startup_checks import get_startup_error
from src.storage.database import get_chat_session


def assert_job_workspace_in_scope(
    *,
    agent: object,
    project_root: Path,
    workspace_dir: Path,
) -> None:
    """Job cwd 必须落在 Agent 与 Project 文件范围的交集内。"""

    guardrails = scope_guardrails_to_project(
        _build_runtime_guardrails(agent),
        project_root,
    )
    scope = Path(guardrails.filesystem_scope).expanduser().resolve()
    if not workspace_dir.resolve().is_relative_to(scope):
        raise ValueError(
            f"Job firing 工作目录不在 Agent∩Project 文件范围内: {workspace_dir} vs {scope}",
        )


async def launch_scheduled_chat_run(
    *,
    session_id: str,
    agent_id: str,
    agent_name: str,
    frozen_prompt: str,
    skill_ids: list[str],
    required_skills: list[dict[str, Any]] | None = None,
    context_policy: str = "fresh",
    workspace_id: str,
    project_id: str,
    workspace_dir: Path,
    runtime_context: Any,
    job: Any,
    firing: Any,
    model_binding: Any,
    job_id: str,
    firing_id: str,
) -> str:
    """在已存在的 owner Session 下建立后台 Chat Run，返回 chat_run_id。"""

    if get_chat_session(session_id) is None:
        raise ValueError("Job 沒有 owner Session")
    run_data = ChatMessage(
        session_id=session_id,
        message=frozen_prompt,
        agent_id=agent_id,
        agent_name=agent_name,
        mode="agent",
        skill_ids=list(skill_ids),
        plan_requested=False,
        sandbox=False,
        approval_mode=job.execution_policy.approval_mode,
    )

    async def event_stream() -> AsyncGenerator[SourceEvent, None]:
        ctx = await build_scheduled_run_context(
            job,
            firing,
            run_data=run_data,
            runtime_context=runtime_context.with_run_id(current_chat_background_run_id()),
            session_workspace_dir=str(workspace_dir),
        )
        persist_user_message_and_trace(ctx, run_data)
        async for start_event in emit_start_events(ctx, run_data):
            yield start_event
        async for context_event in emit_context_events(ctx):
            yield context_event

        assistant_error = get_startup_error(ctx, run_data)
        if assistant_error:
            async for failure_event in emit_failed_startup_run(
                session_id=ctx.session_id,
                persist=ctx.persist_run,
                agent_id=ctx.agent_id,
                agent_name=ctx.agent_name,
                model_name=ctx.model_name,
                assistant_error=assistant_error,
                skill_runs=ctx.skill_runs,
            ):
                yield failure_event
            return

        async for main_event in execute_chat_runtime(ctx=ctx, run_data=run_data):
            yield main_event

    permission_review_binding = None
    if job.execution_policy.approval_mode == "model_review":
        from src.security.authorization.review_gate import validated_review_binding

        permission_review_binding = validated_review_binding()
    run = await start_chat_background_run(
        session_id=session_id,
        factory=event_stream,
        agent_id=agent_id,
        agent_name=agent_name,
        task=frozen_prompt,
        mode="agent",
        workspace_id="",
        project_id="",
        persist=True,
        trigger_source="scheduled_job",
        scheduled_job_id=job_id,
        scheduled_firing_id=firing_id,
        model_binding=model_binding,
        approval_mode=job.execution_policy.approval_mode,
        permission_review_binding=permission_review_binding,
    )
    return run.id
