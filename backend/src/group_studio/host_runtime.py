"""Host 串行讨论运行时。禁止接入 Chat Leader 事件流或 PENDING 交互字典。"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from agentscope.message import UserMsg

from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, RuntimeToolScope
from src.config.runtime_settings import get_effective_model_settings
from src.group_studio.events import emit_envelope
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.orchestrator import assert_floor_allowed
from src.group_studio.tools_host import HOST_STOP_TOOLS, HostToolContext, host_tools
from src.security.permissions import build_permission_profile
from src.server.routes.chat.runtime_builder import (
    RuntimeModelConfig,
    build_main_agent_runtime_spec,
    create_main_agent_from_spec,
)
from src.storage.database import (
    get_group_studio_snapshot,
    insert_group_studio_message,
    insert_group_studio_run,
    set_group_studio_current_run_id,
    update_group_studio_message_body,
    update_group_studio_run_status,
)

HOST_MAX_REACT_ITERS = 16
HOST_DENYLIST = {"AskUser", "FinalAnswerStart"}
DISABLED_TOOL_SCOPE = RuntimeToolScope(
    allow_mcp=False,
    allow_knowledge=False,
    allow_bash=False,
    allow_http=False,
    allow_web=False,
)

AgentFactory = Callable[[Any], Any]


def build_host_runtime_spec(snapshot: dict[str, Any], host_member: dict[str, Any]):
    """构造 Host Agent spec：无默认工具、控场 allowlist、BYPASS 权限。"""

    runtime = get_effective_model_settings()
    guardrails = AgentGuardrails(
        human_approval=False,
        tool_permission_profile="trusted_tools",
        network_enabled=False,
    )
    guardrails = AgentGuardrails(
        filesystem_scope=guardrails.filesystem_scope,
        shell_blacklist=list(guardrails.shell_blacklist),
        network_enabled=False,
        human_approval=False,
        tool_permission_mode="bypass",
        tool_permission_profile="trusted_tools",
        permission_profile=build_permission_profile(guardrails),
    )
    ctx = HostToolContext(
        group_id=str(snapshot["id"]),
        run_id="",
        host_member_id=str(host_member["id"]),
    )
    tools = host_tools(ctx)
    allowlist = {tool.name for tool in tools}
    model_config = RuntimeModelConfig(
        model_name=runtime.default_model,
        fallback_names=list(runtime.fallback_models or []),
        model_options=AgentModelOptions(max_react_iters=HOST_MAX_REACT_ITERS),
    )
    spec = build_main_agent_runtime_spec(
        agent_id=GROUP_STUDIO_HOST_AGENT_ID,
        agent_name="主持人",
        session_id=f"{snapshot['id']}:{host_member['id']}",
        system_prompt=_host_system_prompt(snapshot),
        model_config=model_config,
        deep_thinking=False,
        used_skills=[],
        guardrails=guardrails,
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=DISABLED_TOOL_SCOPE,
        guardrail_cache_key="group-studio-host-bypass",
        runtime_features_cache_key="group-studio",
        extra_tools=tools,
        tool_name_allowlist=allowlist,
        tool_name_denylist=HOST_DENYLIST,
        include_default_tools=False,
    )
    return spec, ctx, tools


def _host_system_prompt(snapshot: dict[str, Any]) -> str:
    card = snapshot.get("taskCard") or {}
    members = snapshot.get("members") or []
    member_lines = "\n".join(
        f"- {item.get('displayName')} ({item.get('role')}) id={item.get('id')} agent={item.get('agentId')} muted={item.get('muted')}"
        for item in members
        if item.get("status") != "removed"
    )
    return (
        "你是群聊工作室的主持人，只负责控场、拆解目标和指派发言，不要亲自写代码或长文交付。\n"
        "每次发言先阅读任务卡和黑板。\n"
        f"目标：{card.get('goal') or '（空）'}\n"
        f"约束：{card.get('constraints') or []}\n"
        f"成员：\n{member_lines}\n"
        "可用工具：group_read_task_card、group_read_blackboard、group_patch_blackboard、"
        "group_assign_floor、group_invite_member、group_mute_member、group_remove_member、"
        "group_start_execution、group_start_review、group_propose_close。\n"
        "每次先读黑板，把已确认决策和未决问题写进 group_patch_blackboard；"
        "不得丢掉未 resolved 的 openQuestions。\n"
        "指派工人后应立刻 group_assign_floor。方案已定后用 group_start_execution。\n"
        "不要调用 AskUser 或 FinalAnswerStart。纯文本发言会结束本轮。"
    )


async def run_host_discussion(
    group_id: str,
    *,
    agent_factory: AgentFactory | None = None,
) -> None:
    """给 Host 一轮讨论发言权并消费 reply_stream。"""

    snapshot = get_group_studio_snapshot(group_id)
    if snapshot is None:
        return
    assert_floor_allowed(str(snapshot.get("status") or ""))
    host_member = next(
        (
            item for item in (snapshot.get("members") or [])
            if item.get("role") == "host" and item.get("status") != "removed"
        ),
        None,
    )
    if host_member is None:
        emit_envelope(group_id=group_id, event_type="GROUP_ERROR", payload={"code": "host_required", "message": "缺少主持人"})
        return
    spec, ctx, _tools = build_host_runtime_spec(snapshot, host_member)
    run_id = f"grun_{uuid.uuid4().hex[:16]}"
    ctx.run_id = run_id
    insert_group_studio_run({
        "id": run_id,
        "groupId": group_id,
        "kind": "discussion",
        "status": "running",
        "speakerMemberId": host_member["id"],
    })
    set_group_studio_current_run_id(group_id, run_id)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_FLOOR",
        run_id=run_id,
        payload={"memberId": host_member["id"], "runId": run_id, "reason": "host_default"},
    )
    message_id = f"gmsg_{uuid.uuid4().hex[:16]}"
    insert_group_studio_message({
        "id": message_id,
        "groupId": group_id,
        "runId": run_id,
        "speakerId": host_member["id"],
        "role": "host",
        "intent": "discuss",
        "body": "",
        "visibleInTimeline": True,
    })
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_OPEN",
        run_id=run_id,
        payload={
            "messageId": message_id,
            "speakerId": host_member["id"],
            "role": "host",
            "intent": "discuss",
            "visibleInTimeline": True,
        },
    )
    factory = agent_factory or (lambda built_spec: create_main_agent_from_spec(built_spec))
    body_parts: list[str] = []
    outcome = "completed"
    run_status = "completed"
    error_text = ""
    try:
        agent = factory(spec)
        last_chars_flush = 0
        async for raw_event in agent.reply_stream(UserMsg(
            name="user",
            content=_host_turn_user_message(snapshot),
        )):
            data = raw_event.model_dump(mode="json") if hasattr(raw_event, "model_dump") else dict(raw_event)
            event_type = str(data.get("type") or "")
            if event_type == "TEXT_BLOCK_DELTA":
                delta = str(data.get("delta") or "")
                if not delta:
                    continue
                body_parts.append(delta)
                emit_envelope(
                    group_id=group_id,
                    event_type="GROUP_MESSAGE_DELTA",
                    run_id=run_id,
                    persist=False,
                    payload={"messageId": message_id, "text": delta},
                )
                joined = "".join(body_parts)
                if len(joined) - last_chars_flush >= 256:
                    update_group_studio_message_body(message_id, joined)
                    last_chars_flush = len(joined)
            elif event_type == "TOOL_RESULT_END":
                tool_name = str(data.get("tool_call_name") or "")
                failed = str(data.get("state") or "").lower() in {"error", "failed", "denied"}
                if not failed and tool_name in HOST_STOP_TOOLS:
                    break
            elif event_type == "EXCEED_MAX_ITERS":
                outcome = "failed"
                run_status = "failed"
                error_text = "host_max_iters"
                break
            elif event_type == "REPLY_END":
                reason = str(data.get("finished_reason") or "completed").lower()
                if reason not in {"completed", ""}:
                    outcome = "failed"
                    run_status = "failed"
                    error_text = reason
        if ctx.last_stop_tool:
            outcome = "completed"
            run_status = "completed"
    except Exception as exc:
        outcome = "failed"
        run_status = "failed"
        error_text = str(exc)
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_ERROR",
            run_id=run_id,
            payload={"code": "host_failed", "message": error_text},
        )
    body = "".join(body_parts)
    update_group_studio_message_body(message_id, body)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_CLOSE",
        run_id=run_id,
        payload={"messageId": message_id, "body": body, "outcome": outcome},
    )
    update_group_studio_run_status(run_id, run_status, error_text)
    set_group_studio_current_run_id(group_id, None)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_RUN_ENDED",
        run_id=run_id,
        payload={"runId": run_id, "status": run_status},
    )


def _host_turn_user_message(snapshot: dict[str, Any]) -> str:
    card = snapshot.get("taskCard") or {}
    return (
        "请主持本轮讨论。先简要复述目标，指出下一步该谁发言或是否进入执行。"
        f"目标：{card.get('goal') or ''}"
    )
