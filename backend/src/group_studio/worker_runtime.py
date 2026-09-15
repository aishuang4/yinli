"""Worker 串行运行时。群聊内工具权限 BYPASS；禁止接入 Chat pending 字典。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, Callable

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode

from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, RuntimeToolScope
from src.config.runtime_settings import get_effective_model_settings
from src.group_studio.approvals import create_pending_approval
from src.group_studio.circuit import on_run_finished, progress_fingerprint
from src.group_studio.errors import MODEL_NOT_CONFIGURED, GroupStudioError
from src.group_studio.events import emit_envelope
from src.group_studio.startup_checks import get_group_studio_model_error
from src.group_studio.files import ensure_studio_dirs
from src.group_studio.orchestrator import assert_floor_allowed, assert_serial_run
from src.group_studio.sessions import (
    WorkerSession,
    drop_run_task,
    drop_session,
    register_run_task,
    register_session,
    remember_fingerprint,
    take_interrupt,
)
from src.group_studio.text_events import message_text_delta
from src.group_studio.turn_context import format_turn_context, note_member_spoke
from src.group_studio.tools_worker import (
    WORKER_STOP_TOOLS,
    acquire_worker_tool_context,
    worker_tools,
)
from src.project_resources import resolve_project_resource_access
from src.runtime_context import resolve_runtime_context
from src.security.permissions import build_permission_profile
from src.server.routes.chat.runtime import (
    _build_runtime_knowledge_tools,
    _build_runtime_mcp_servers,
    scope_guardrails_to_project,
)
from src.server.routes.chat.runtime_builder import (
    RuntimeModelConfig,
    build_main_agent_runtime_spec,
    create_main_agent_from_spec,
)
from src.server.routes.chat.runtime_governance import (
    append_user_note,
    build_context_governance_middlewares,
    build_memory_prompt_and_recall,
    persist_agent_session_state,
    prepare_agent_governance,
    restore_agent_session_state,
)
from src.storage.database import (
    get_group_studio_snapshot,
    get_project,
    insert_group_studio_message,
    insert_group_studio_run,
    set_group_studio_current_run_id,
    update_group_studio_message_body,
    update_group_studio_run_status,
)

WORKER_MAX_REACT_ITERS = 40
WORKER_DENYLIST = {"AskUser", "FinalAnswerStart"}
WORKER_BASE_TOOLS = {
    "Read",
    "Write",
    "Edit",
    "Grep",
    "Glob",
    "Bash",
    "HttpRequest",
    "WebFetch",
    "memory_node_tree",
    "memory_node_query",
}

AgentFactory = Callable[[Any], Any]


def build_worker_runtime_spec(snapshot: dict[str, Any], member: dict[str, Any]):
    """构造 Worker spec：复制 Agent 能力，群聊内工具权限全部放开。"""

    agent_row = _agent_row(str(member.get("agentId") or "default"))
    project = get_project(str(snapshot.get("projectId") or "")) or {}
    from pathlib import Path

    project_root = Path(str(project.get("rootPath") or "")).expanduser().resolve()
    context = resolve_runtime_context(
        project_id=str(snapshot.get("projectId") or ""),
        session_id=str(snapshot.get("chatSessionId") or ""),
        run_id="",
        agent_id=str(agent_row.get("id") or member.get("agentId") or "default"),
    )
    access = resolve_project_resource_access(context)
    raw_guardrails = dict(agent_row.get("guardrails") or {})
    guardrails = AgentGuardrails(
        filesystem_scope=str(raw_guardrails.get("filesystem_scope") or context.project_root),
        shell_blacklist=list(raw_guardrails.get("shell_blacklist") or ["rm -rf /", "sudo"]),
        network_enabled=True,
        human_approval=False,
        tool_permission_mode="bypass",
        tool_permission_profile="trusted_tools",
        execution_backend=str(raw_guardrails.get("execution_backend") or "local"),
        docker_image=str(raw_guardrails.get("docker_image") or "python:3.11-slim"),
        persistent_sandbox=bool(raw_guardrails.get("persistent_sandbox", False)),
        timeout_seconds=int(raw_guardrails.get("timeout_seconds") or 300),
    )
    guardrails = AgentGuardrails(
        filesystem_scope=guardrails.filesystem_scope,
        shell_blacklist=list(guardrails.shell_blacklist),
        network_enabled=True,
        human_approval=False,
        tool_permission_mode="bypass",
        tool_permission_profile="trusted_tools",
        execution_backend=guardrails.execution_backend,
        docker_image=guardrails.docker_image,
        persistent_sandbox=guardrails.persistent_sandbox,
        timeout_seconds=guardrails.timeout_seconds,
        permission_profile=build_permission_profile(guardrails),
    )
    if project_root.is_dir():
        guardrails = scope_guardrails_to_project(guardrails, project_root)

    mcp_ids = access.filter_ids("mcp", [str(item) for item in (agent_row.get("mcpTools") or [])])
    knowledge_ids = access.filter_ids(
        "http_tool",
        [str(item) for item in (agent_row.get("knowledgeTools") or [])],
    )
    cfg = SimpleNamespace(mcp_tools=mcp_ids, knowledge_tools=knowledge_ids)
    mcp_servers = _build_runtime_mcp_servers(cfg, allowed_server_ids=access.mcp_server_ids)
    knowledge_tools = _build_runtime_knowledge_tools(cfg, allowed_tool_ids=access.http_tool_ids)
    skill_ids = access.filter_ids("skill", [str(item) for item in (agent_row.get("skills") or [])])
    skill_paths = _skill_paths(skill_ids)

    ctx = acquire_worker_tool_context(
        str(snapshot["id"]),
        str(member["id"]),
        str(agent_row.get("id") or member.get("agentId") or ""),
    )
    tools = worker_tools(ctx)
    allowlist = set(WORKER_BASE_TOOLS)
    allowlist.update(tool.name for tool in tools)
    allowlist.update(item.name for item in knowledge_tools)
    for server in mcp_servers:
        allowlist.update(server.enable_tools or [])

    network = bool(guardrails.network_enabled)
    scope = RuntimeToolScope(
        allow_mcp=bool(mcp_servers) and network,
        allow_knowledge=bool(knowledge_tools),
        allow_bash=True,
        allow_http=network,
        allow_web=network,
    )
    studio_root = ensure_studio_dirs(context.project_root, str(snapshot["id"]))
    workspace = str((studio_root / "workspace").resolve())
    runtime = get_effective_model_settings()
    model_config = RuntimeModelConfig(
        model_name=runtime.default_model,
        fallback_names=list(runtime.fallback_models or []),
        model_options=AgentModelOptions(max_react_iters=WORKER_MAX_REACT_ITERS),
    )
    worker_agent_id = str(agent_row.get("id") or member.get("agentId") or "default")
    worker_agent_name = str(member.get("displayName") or agent_row.get("name") or "工人")
    worker_session_id = f"{snapshot['id']}:{member['id']}"
    # Worker 是 Chat catalog 里的 Agent：与单 Agent 一样带自己的 Markdown 记忆摘要（按工作室会话冻结）。
    memory_prompt = _worker_memory_prompt(
        snapshot,
        agent_id=worker_agent_id,
        agent_name=worker_agent_name,
        session_id=worker_session_id,
    )
    spec = build_main_agent_runtime_spec(
        agent_id=worker_agent_id,
        agent_name=worker_agent_name,
        session_id=worker_session_id,
        system_prompt=_with_memory_prompt(_worker_system_prompt(snapshot, member), memory_prompt),
        model_config=model_config,
        deep_thinking=True,
        used_skills=[],
        skill_paths=skill_paths,
        guardrails=guardrails,
        mcp_servers=mcp_servers,
        knowledge_tools=knowledge_tools,
        runtime_tool_scope=scope,
        guardrail_cache_key=f"group-studio-worker-bypass:{guardrails.permission_profile.cache_key()}",
        runtime_features_cache_key="group-studio-workboard-v1",
        extra_tools=tools,
        tool_name_allowlist=allowlist,
        tool_name_denylist=WORKER_DENYLIST,
        # 已读 Skill 正文保留 + 一级无模型整理 + 压缩可观测，与单 Agent 同一套。
        middlewares=build_context_governance_middlewares(skill_context=True),
        include_default_tools=True,
        workspace_dir=workspace,
        write_scope_dir=workspace,
        extra_filesystem_scopes=_catalog_scopes(str(snapshot.get("workspaceId") or "default")),
    )
    return spec, ctx, tools


def _catalog_scopes(workspace_id: str) -> list[str]:
    from src.catalog.paths import extra_filesystem_scopes_for_workspace

    try:
        return extra_filesystem_scopes_for_workspace(workspace_id)
    except ValueError:
        return []


def _agent_row(agent_id: str) -> dict[str, Any]:
    if agent_id == "default":
        return {
            "id": "default",
            "name": "默认助手",
            "guardrails": {},
            "mcpTools": [],
            "knowledgeTools": [],
            "skills": [],
        }
    from src.server.routes.agents import get_registered_agent

    agent = get_registered_agent(agent_id)
    if agent is not None:
        return agent.model_dump(by_alias=True)
    return {
        "id": agent_id,
        "name": agent_id,
        "guardrails": {},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
    }


def _skill_paths(skill_ids: list[str]) -> list[str]:
    if not skill_ids:
        return []
    try:
        from src.server.routes.skills import get_installed_skill, resolve_runtime_skill_paths

        skills = []
        for skill_id in skill_ids:
            item = get_installed_skill(skill_id)
            if item is not None:
                skills.append(item)
        return list(resolve_runtime_skill_paths(skills) or [])
    except Exception:
        return []


def _with_memory_prompt(system_prompt: str, memory_prompt: str) -> str:
    return f"{system_prompt}\n\n{memory_prompt}" if memory_prompt else system_prompt


def _worker_memory_prompt(snapshot: dict[str, Any], *, agent_id: str, agent_name: str, session_id: str) -> str:
    """Worker 的 Markdown 记忆摘要；召回在每轮 `_worker_memory_recall` 单独做。"""

    try:
        memory_prompt, _recall = build_memory_prompt_and_recall(
            agent_id=agent_id,
            agent_name=agent_name,
            workspace_id=str(snapshot.get("workspaceId") or "default"),
            project_id=str(snapshot.get("projectId") or "default"),
            query_text="",
            freeze_session_id=session_id,
        )
    except Exception:
        return ""
    return memory_prompt


def _worker_memory_recall(snapshot: dict[str, Any], *, agent_id: str, agent_name: str, duty: str) -> str:
    """按任务卡目标 + 开放工作项召回历史记忆；群聊事件暂不写 run_events，这里不做审计。"""

    card = snapshot.get("taskCard") or {}
    items = [
        str(item.get("title") or "")
        for item in (snapshot.get("workItems") or [])
        if item.get("status") in {"open", "claimed"}
    ]
    query = " ".join(part for part in [str(card.get("goal") or ""), *items] if part)
    if duty == "claim" or not query.strip():
        return ""
    try:
        _prompt, recall = build_memory_prompt_and_recall(
            agent_id=agent_id,
            agent_name=agent_name,
            workspace_id=str(snapshot.get("workspaceId") or "default"),
            project_id=str(snapshot.get("projectId") or "default"),
            query_text=query,
            freeze_session_id=None,
            exclude_session_id=str(snapshot.get("chatSessionId") or ""),
            include_markdown=False,
        )
    except Exception:
        return ""
    return recall.text if recall else ""


def _worker_system_prompt(snapshot: dict[str, Any], member: dict[str, Any]) -> str:
    card = snapshot.get("taskCard") or {}
    board = snapshot.get("blackboard") or {}
    return (
        f"你是群聊工作室的执行成员「{member.get('displayName') or member.get('agentId')}」。\n"
        "先读黑板，再行动。讨论期用文字或 group_post_message 发言；"
        "执行期可以读写文件和跑命令，最后必须 group_post_message 交付；"
        "系统会把交稿摘要写入黑板，你仍必须发群消息。\n"
        f"目标：{card.get('goal') or ''}\n"
        f"约束：{card.get('constraints') or []}\n"
        f"未决问题：{[item.get('text') for item in (board.get('openQuestions') or []) if not item.get('resolved')]}\n"
        "对群可见的发言只写结论与证据，不要把内心推理过程写进正文。\n"
        "口头「我来」无效，必须 group_claim_work_item。"
        "认领轮只认领或跳过，不要写交付文件。"
        "被点名干活时先 claim 再做，最后 group_complete_work_item 和 group_post_message。\n"
        "不要调用 AskUser 或 FinalAnswerStart。"
    )


def _worker_user_message(
    snapshot: dict[str, Any],
    member: dict[str, Any],
    *,
    duty: str = "queue",
) -> str:
    card = snapshot.get("taskCard") or {}
    status = str(snapshot.get("status") or "")
    items = snapshot.get("workItems") or []
    open_items = [
        f"{item.get('id')}:{item.get('title')}"
        for item in items
        if item.get("status") in {"open", "claimed"}
    ]
    if duty == "claim":
        return (
            "这是认领回合，认领或说跳过，不要开工；用 group_claim_work_item，最后 group_post_message。"
            f"开放工作项：{open_items}\n"
            f"{format_turn_context(snapshot)}"
        )
    if status == "executing":
        return (
            f"请推进执行并在结束时 group_post_message 汇报。"
            f"目标：{card.get('goal') or ''}\n"
            f"{format_turn_context(snapshot)}"
        )
    return (
        f"轮到你发言。简要给出看法或方案，并用 group_post_message 发到群里。"
        f"若有 open 工作项，先 group_claim_work_item 再干活。"
        f"目标：{card.get('goal') or ''}\n"
        f"工作项：{open_items}\n"
        f"{format_turn_context(snapshot)}"
    )


async def run_worker_turn(
    group_id: str,
    member_id: str,
    *,
    agent_factory: AgentFactory | None = None,
    duty: str = "queue",
) -> None:
    """一次 Worker 发言固定工作室的模型连接。"""
    from src.config.runtime_settings import resolve_model_binding, model_binding_scope
    snapshot = get_group_studio_snapshot(group_id)
    if snapshot is None:
        return
    with model_binding_scope(resolve_model_binding(snapshot.get("modelConfigId"), validate=False)):
        await _run_worker_turn(group_id, member_id, agent_factory=agent_factory, duty=duty)


async def _run_worker_turn(
    group_id: str,
    member_id: str,
    *,
    agent_factory: AgentFactory | None = None,
    duty: str = "queue",
) -> None:
    """给指定工人一轮发言/执行权。"""

    snapshot = get_group_studio_snapshot(group_id)
    if snapshot is None:
        return
    assert_floor_allowed(str(snapshot.get("status") or ""), role="worker")
    assert_serial_run(snapshot.get("currentRunId"))
    member = next(
        (
            item for item in (snapshot.get("members") or [])
            if item.get("id") == member_id and item.get("status") != "removed"
        ),
        None,
    )
    if member is None or member.get("role") != "worker":
        from src.group_studio.speaker_queue import pop_head, pop_unrunnable_head

        pop_unrunnable_head(group_id)
        emit_envelope(group_id=group_id, event_type="GROUP_ERROR", payload={"code": "not_found", "message": "工人不存在"})
        return
    if member.get("muted"):
        from src.group_studio.speaker_queue import pop_unrunnable_head

        pop_unrunnable_head(group_id)
        emit_envelope(group_id=group_id, event_type="GROUP_ERROR", payload={"code": "member_muted", "message": "工人已静音"})
        return
    if agent_factory is None:
        model_error = get_group_studio_model_error(snapshot.get("modelConfigId"))
        if model_error:
            emit_envelope(
                group_id=group_id,
                event_type="GROUP_ERROR",
                payload={"code": MODEL_NOT_CONFIGURED, "message": model_error},
            )
            return

    spec, ctx, _tools = build_worker_runtime_spec(snapshot, member)
    run_id = f"grun_{uuid.uuid4().hex[:16]}"
    ctx.begin_run(run_id)
    from src.group_studio.speaker_queue import get_queue, pop_head
    from src.group_studio.workboard import get_claim_round, pop_claim_head

    floor_reason = "host_assign"
    if duty == "claim":
        claim_before = get_claim_round(group_id)
        if claim_before and claim_before[0] == member_id:
            pop_claim_head(group_id, member_id)
            floor_reason = "claim_round"
        elif claim_before and claim_before[0] != member_id:
            emit_envelope(
                group_id=group_id,
                event_type="GROUP_ERROR",
                payload={"code": "floor_not_granted", "message": "当前没有你的发言权"},
            )
            return
        else:
            floor_reason = "claim_round"
    else:
        queue_before = get_queue(group_id)
        if queue_before and queue_before[0] == member_id:
            pop_head(group_id, member_id)
            floor_reason = "host_assign" if len(queue_before) > 1 else "queue_next"
        elif queue_before and queue_before[0] != member_id:
            emit_envelope(
                group_id=group_id,
                event_type="GROUP_ERROR",
                payload={"code": "floor_not_granted", "message": "当前没有你的发言权"},
            )
            return
    kind = "execution" if str(snapshot.get("status") or "") == "executing" else "discussion"
    intent = "report_progress" if kind == "execution" else "discuss"
    insert_group_studio_run({
        "id": run_id,
        "groupId": group_id,
        "kind": kind,
        "status": "running",
        "speakerMemberId": member_id,
    })
    set_group_studio_current_run_id(group_id, run_id)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_FLOOR",
        run_id=run_id,
        payload={"memberId": member_id, "runId": run_id, "reason": floor_reason},
    )
    message_id = f"gmsg_{uuid.uuid4().hex[:16]}"
    insert_group_studio_message({
        "id": message_id,
        "groupId": group_id,
        "runId": run_id,
        "speakerId": member_id,
        "role": "worker",
        "intent": intent,
        "body": "",
        "visibleInTimeline": True,
    })
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_OPEN",
        run_id=run_id,
        payload={
            "messageId": message_id,
            "speakerId": member_id,
            "role": "worker",
            "intent": intent,
            "visibleInTimeline": True,
        },
    )
    factory = agent_factory or (lambda built_spec: create_main_agent_from_spec(built_spec))
    agent = factory(spec)
    # 工作室跨多轮：offloader 按 studio+member 分目录；实例重建时恢复压缩摘要与已读 Skill。
    prepare_agent_governance(agent, session_id=spec.session_id)
    if not list(getattr(getattr(agent, "state", None), "context", []) or []):
        restore_agent_session_state(agent, spec.session_id)
    recall_note = _worker_memory_recall(snapshot, agent_id=spec.parent_agent_id, agent_name=spec.parent_agent_name, duty=duty)
    session = register_session(WorkerSession(
        group_id=group_id,
        run_id=run_id,
        member_id=member_id,
        agent=agent,
    ))
    body_parts: list[str] = []
    thinking_parts: list[str] = []
    outcome = "completed"
    run_status = "completed"
    error_text = ""
    remember_fingerprint(run_id, progress_fingerprint(snapshot))
    register_run_task(group_id, asyncio.current_task())
    try:
        next_input: Any = UserMsg(
            name="user",
            content=append_user_note(_worker_user_message(snapshot, member, duty=duty), recall_note),
        )
        last_chars_flush = 0
        while not session.cancelled:
            paused = False
            async for raw_event in agent.reply_stream(next_input):
                data = raw_event.model_dump(mode="json") if hasattr(raw_event, "model_dump") else dict(raw_event)
                event_type = str(data.get("type") or "")
                text_delta = message_text_delta(data)
                if text_delta is not None:
                    kind, delta = text_delta
                    target = thinking_parts if kind == "thinking" else body_parts
                    target.append(delta)
                    emit_envelope(
                        group_id=group_id,
                        event_type="GROUP_MESSAGE_DELTA",
                        run_id=run_id,
                        persist=False,
                        payload={"messageId": message_id, "text": delta, "kind": kind},
                    )
                    if kind == "answer":
                        joined = "".join(body_parts)
                        if len(joined) - last_chars_flush >= 256:
                            update_group_studio_message_body(
                                message_id,
                                joined,
                                thinking="".join(thinking_parts),
                            )
                            last_chars_flush = len(joined)
                elif event_type in {"TOOL_RESULT_START", "TOOL_CALL", "TOOL_START"}:
                    emit_envelope(
                        group_id=group_id,
                        event_type="GROUP_ACTIVITY_OPEN",
                        run_id=run_id,
                        payload={
                            "runId": run_id,
                            "memberId": member_id,
                            "toolName": str(data.get("tool_call_name") or data.get("name") or ""),
                        },
                    )
                elif event_type == "TOOL_RESULT_DELTA":
                    emit_envelope(
                        group_id=group_id,
                        event_type="GROUP_ACTIVITY_DELTA",
                        run_id=run_id,
                        persist=False,
                        payload={
                            "runId": run_id,
                            "memberId": member_id,
                            "text": str(data.get("delta") or ""),
                        },
                    )
                elif event_type == "TOOL_RESULT_END":
                    tool_name = str(data.get("tool_call_name") or data.get("name") or "")
                    emit_envelope(
                        group_id=group_id,
                        event_type="GROUP_ACTIVITY_CLOSE",
                        run_id=run_id,
                        payload={"runId": run_id, "memberId": member_id, "toolName": tool_name},
                    )
                    failed = str(data.get("state") or "").lower() in {"error", "failed", "denied"}
                    if not failed and tool_name in WORKER_STOP_TOOLS:
                        break
                elif event_type == "REQUIRE_USER_CONFIRM":
                    reply_id = str(data.get("reply_id") or data.get("replyId") or "")
                    tool_calls = list(data.get("tool_calls") or data.get("toolCalls") or [])
                    create_pending_approval(
                        group_id=group_id,
                        run_id=run_id,
                        member_id=member_id,
                        reply_id=reply_id,
                        tool_calls=tool_calls,
                    )
                    loop = asyncio.get_running_loop()
                    session.resume = loop.create_future()
                    resumed = await session.resume
                    if resumed is None or session.cancelled:
                        outcome = "cancelled"
                        run_status = "cancelled"
                        error_text = "cancelled"
                        paused = False
                        next_input = None
                        break
                    next_input = resumed
                    paused = True
                    update_group_studio_run_status(run_id, "running")
                    break
                elif event_type == "EXCEED_MAX_ITERS":
                    outcome = "failed"
                    run_status = "failed"
                    error_text = "worker_max_iters"
                    break
                elif event_type == "REPLY_END":
                    reason = str(data.get("finished_reason") or "completed").lower()
                    if reason not in {"completed", ""}:
                        outcome = "failed"
                        run_status = "failed"
                        error_text = reason
            if paused and next_input is not None:
                continue
            break
        if ctx.last_stop_tool:
            outcome = "completed"
            run_status = "completed"
        if ctx.posted_body:
            body_parts = [ctx.posted_body]
    except GroupStudioError:
        raise
    except asyncio.CancelledError:
        outcome = "cancelled"
        run_status = "cancelled"
        error_text = "cancelled"
    except Exception as exc:
        outcome = "failed"
        run_status = "failed"
        error_text = str(exc)
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_ERROR",
            run_id=run_id,
            payload={"code": "worker_failed", "message": error_text},
        )
    finally:
        persist_agent_session_state(agent, session_id=spec.session_id, agent_id=spec.parent_agent_id)
        drop_session(group_id, member_id, run_id)
        drop_run_task(group_id)

    body = "".join(body_parts)
    thinking = "".join(thinking_parts)
    update_group_studio_message_body(message_id, body, thinking)
    note_member_spoke(group_id, member_id, role="worker", body=body)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_CLOSE",
        run_id=run_id,
        payload={"messageId": message_id, "body": body, "thinking": thinking, "outcome": outcome},
    )
    interrupted = take_interrupt(group_id)
    if interrupted:
        return
    if run_status == "completed":
        from src.group_studio.service import record_worker_delivery

        record_worker_delivery(
            group_id,
            member=member,
            body=body,
            run_id=run_id,
            message_id=message_id,
        )
    update_group_studio_run_status(run_id, run_status, error_text)
    set_group_studio_current_run_id(group_id, None)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_RUN_ENDED",
        run_id=run_id,
        payload={"runId": run_id, "status": run_status},
    )
    on_run_finished(
        group_id,
        run_id=run_id,
        run_status=run_status,
        interrupt_source=interrupted,
        duty=duty,
    )


def worker_permission_mode(snapshot: dict[str, Any], member: dict[str, Any]) -> PermissionMode:
    """测试用：返回 Worker 生效的 AgentScope permission mode。"""

    spec, _ctx, _tools = build_worker_runtime_spec(snapshot, member)
    return spec.guardrails.permission_profile.to_agentscope_permission_mode()
