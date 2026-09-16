"""Host 串行讨论运行时。禁止接入 Chat Leader 事件流或 PENDING 交互字典。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable

from agentscope.message import UserMsg

from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, RuntimeToolScope
from src.config.runtime_settings import get_effective_model_settings
from src.group_studio.circuit import on_run_finished, progress_fingerprint
from src.group_studio.errors import MODEL_NOT_CONFIGURED
from src.group_studio.events import emit_envelope
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.startup_checks import get_group_studio_model_error
from src.group_studio.orchestrator import assert_floor_allowed, assert_serial_run
from src.group_studio.sessions import (
    drop_run_task,
    register_run_task,
    remember_fingerprint,
    take_interrupt,
)
from src.group_studio.text_events import message_text_delta
from src.group_studio.turn_context import format_turn_context, note_member_spoke
from src.group_studio.tools_host import (
    HOST_STOP_TOOLS,
    acquire_host_tool_context,
    host_tools,
)
from src.security.permissions import build_permission_profile
from src.server.routes.chat.runtime_builder import (
    RuntimeModelConfig,
    build_main_agent_runtime_spec,
    create_main_agent_from_spec,
)
from src.server.routes.chat.runtime_governance import (
    build_context_governance_middlewares,
    persist_agent_session_state,
    prepare_agent_governance,
    restore_agent_session_state,
)
from src.storage.database import (
    get_group_studio_pending_speaker,
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
    ctx = acquire_host_tool_context(str(snapshot["id"]), str(host_member["id"]))
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
        deep_thinking=True,
        used_skills=[],
        guardrails=guardrails,
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=DISABLED_TOOL_SCOPE,
        guardrail_cache_key="group-studio-host-bypass",
        runtime_features_cache_key="group-studio-workboard-v1",
        extra_tools=tools,
        tool_name_allowlist=allowlist,
        tool_name_denylist=HOST_DENYLIST,
        # Host 没有 Skill；只挂一级整理 + 压缩可观测，长讨论不至于把窗口撑满。
        middlewares=build_context_governance_middlewares(skill_context=False),
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
    ) or "- （尚无工人）"
    from src.group_studio.service import list_invitable_agents

    invite_lines = "\n".join(
        f"- {item['name']} agentId={item['id']}：{item['description'] or '暂无描述'}"
        for item in list_invitable_agents(str(snapshot.get("id") or ""))
    ) or "- （没有可邀请的 Agent）"
    status = str(snapshot.get("status") or "")
    if status == "drafting":
        return (
            "你是群聊工作室的主持人。当前是 intake 对谈，群聊还没开始。只和人类把任务卡写清楚，不要亲自写代码或长文交付。\n"
            f"目标：{card.get('goal') or '（空）'}\n"
            f"约束：{card.get('constraints') or []}\n"
            f"验收：{card.get('dodChecklist') or []}\n"
            f"当前成员（工人入座但不发言）：\n{member_lines}\n"
            f"可预邀的项目 Agent：\n{invite_lines}\n"
            "可用工具：group_read_task_card、group_read_blackboard、group_patch_blackboard、"
            "group_update_task_card、group_request_intake_confirm、group_list_available_agents、"
            "group_invite_member、group_mute_member、group_remove_member。\n"
            "根据人类最近消息追问目标、约束、验收和范围。信息落到任务卡时必须 group_update_task_card，"
            "不要只用 group_patch_blackboard.goal。默认给出 3～7 条 DoD；用户说不要验收就把 dodChecklist 写成空数组。\n"
            "人类或你都可以 group_invite_member 预邀工人入座；他们在人类确认开始前不会发言。"
            "不要 group_assign_floor、不要 group_publish_work_items、不要 group_start_execution。\n"
            "目标已有且你认为密度够了：调用 group_request_intake_confirm，请用户说「开始 / 就这样」或点右侧确认。"
            "空 DoD 允许确认。用户过早说开始：列出缺口，不要 group_request_intake_confirm。Goal 仍空是硬条件。\n"
            "对群可见的发言只写结论和问题，不要把内心推理过程写进正文。不要调用 AskUser 或 FinalAnswerStart。"
        )
    return (
        "你是群聊工作室的主持人，只负责控场、拆解目标和指派发言，不要亲自写代码或长文交付。\n"
        "每次发言先阅读任务卡和黑板。\n"
        f"目标：{card.get('goal') or '（空）'}\n"
        f"约束：{card.get('constraints') or []}\n"
        f"当前成员：\n{member_lines}\n"
        f"可邀请的项目 Agent（尚未进群，邀请时用 agentId 调 group_invite_member）：\n{invite_lines}\n"
        "可用工具：group_read_task_card、group_read_blackboard、group_patch_blackboard、"
        "group_update_task_card、group_list_available_agents、group_invite_member、group_assign_floor、"
        "group_mute_member、group_remove_member、"
        "group_start_execution、group_start_review、group_propose_close。\n"
        "若目标需要多种专业能力，且群里还没有对应工人，先 group_invite_member 拉人（含主持人最多 6 人），"
        "再 group_assign_floor(memberIds=[...]) 一次点多人，不要一人问完再问下一个。\n"
        "每次先读黑板，把已确认决策和未决问题写进 group_patch_blackboard；"
        "不得丢掉未 resolved 的 openQuestions。\n"
        "判断工人是否完成：先看本轮消息里的「最近群消息」和 lastSpokeAt。"
        "时间线上已有交付就不能当未响应，更不能因此静音或换人；黑板 artifacts 为空不等于没交稿。\n"
        "指派后立刻 group_assign_floor；可以一次传入 memberIds。"
        "方案已定后必须调用 group_start_execution，建议同时传 memberId；口头说「进入执行」无效。\n"
        "对群可见的发言只写结论和安排，不要把内心推理过程写进正文。\n"
        "不要调用 AskUser 或 FinalAnswerStart。"
        "纯文本不能代替控场工具：未结束讨论就 group_assign_floor，进入执行必须 group_start_execution。"
    )


async def run_host_discussion(
    group_id: str,
    *,
    agent_factory: AgentFactory | None = None,
) -> str:
    """一次 Host 发言固定工作室的模型连接。"""
    from src.config.runtime_settings import resolve_model_binding, model_binding_scope
    snapshot = get_group_studio_snapshot(group_id)
    if snapshot is None:
        return ""
    with model_binding_scope(resolve_model_binding(snapshot.get("modelConfigId"), validate=False)):
        return await _run_host_discussion(group_id, agent_factory=agent_factory)


async def _run_host_discussion(
    group_id: str,
    *,
    agent_factory: AgentFactory | None = None,
) -> str:
    """给 Host 一轮讨论发言权并消费 reply_stream。成功指派则返回工人 member_id。"""

    snapshot = get_group_studio_snapshot(group_id)
    if snapshot is None:
        return ""
    assert_floor_allowed(str(snapshot.get("status") or ""), role="host")
    assert_serial_run(snapshot.get("currentRunId"))
    host_member = next(
        (
            item for item in (snapshot.get("members") or [])
            if item.get("role") == "host" and item.get("status") != "removed"
        ),
        None,
    )
    if host_member is None:
        emit_envelope(group_id=group_id, event_type="GROUP_ERROR", payload={"code": "host_required", "message": "缺少主持人"})
        return ""
    if agent_factory is None:
        model_error = get_group_studio_model_error(snapshot.get("modelConfigId"))
        if model_error:
            emit_envelope(
                group_id=group_id,
                event_type="GROUP_ERROR",
                payload={"code": MODEL_NOT_CONFIGURED, "message": model_error},
            )
            return ""
    spec, ctx, _tools = build_host_runtime_spec(snapshot, host_member)
    run_id = f"grun_{uuid.uuid4().hex[:16]}"
    ctx.begin_run(run_id)
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
    restating = str(snapshot.get("status") or "") == "awaiting_human"
    intent = "restatement" if restating else "discuss"
    message_id = f"gmsg_{uuid.uuid4().hex[:16]}"
    insert_group_studio_message({
        "id": message_id,
        "groupId": group_id,
        "runId": run_id,
        "speakerId": host_member["id"],
        "role": "host",
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
            "speakerId": host_member["id"],
            "role": "host",
            "intent": intent,
            "visibleInTimeline": True,
        },
    )
    factory = agent_factory or (lambda built_spec: create_main_agent_from_spec(built_spec))
    body_parts: list[str] = []
    thinking_parts: list[str] = []
    outcome = "completed"
    run_status = "completed"
    error_text = ""
    remember_fingerprint(run_id, progress_fingerprint(snapshot))
    register_run_task(group_id, asyncio.current_task())
    agent = None
    try:
        agent = factory(spec)
        prepare_agent_governance(agent, session_id=spec.session_id)
        if not list(getattr(getattr(agent, "state", None), "context", []) or []):
            restore_agent_session_state(agent, spec.session_id)
        last_chars_flush = 0
        next_input: Any = UserMsg(name="user", content=_host_turn_user_message(snapshot))
        nudged = False
        while next_input is not None:
            hard_stop = False
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
                elif event_type == "TOOL_RESULT_END":
                    tool_name = str(data.get("tool_call_name") or "")
                    failed = str(data.get("state") or "").lower() in {"error", "failed", "denied"}
                    if not failed and tool_name in HOST_STOP_TOOLS:
                        hard_stop = True
                        break
                elif event_type == "EXCEED_MAX_ITERS":
                    outcome = "failed"
                    run_status = "failed"
                    error_text = "host_max_iters"
                    hard_stop = True
                    break
                elif event_type == "REPLY_END":
                    reason = str(data.get("finished_reason") or "completed").lower()
                    if reason not in {"completed", ""}:
                        outcome = "failed"
                        run_status = "failed"
                        error_text = reason
                        hard_stop = True
            if hard_stop or ctx.last_stop_tool or ctx.assigned_member_id or restating:
                break
            if not nudged and _host_announced_phase("".join(body_parts)):
                nudged = True
                next_input = UserMsg(
                    name="user",
                    content=(
                        "口头宣布无效。请立刻调用 group_start_execution，"
                        "并传入 memberId 指定谁写最终交付。不要只发文本。"
                    ),
                )
                continue
            break
        if ctx.last_stop_tool:
            outcome = "completed"
            run_status = "completed"
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
            payload={"code": "host_failed", "message": error_text},
        )
    finally:
        if agent is not None:
            persist_agent_session_state(agent, session_id=spec.session_id, agent_id=spec.parent_agent_id)
        drop_run_task(group_id)
    body = "".join(body_parts)
    thinking = "".join(thinking_parts)
    update_group_studio_message_body(message_id, body, thinking)
    note_member_spoke(group_id, str(host_member["id"]), role="host", body=body)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_CLOSE",
        run_id=run_id,
        payload={"messageId": message_id, "body": body, "thinking": thinking, "outcome": outcome},
    )
    interrupted = take_interrupt(group_id)
    if interrupted:
        return ""
    update_group_studio_run_status(run_id, run_status, error_text)
    set_group_studio_current_run_id(group_id, None)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_RUN_ENDED",
        run_id=run_id,
        payload={"runId": run_id, "status": run_status},
    )
    tripped = on_run_finished(
        group_id,
        run_id=run_id,
        run_status=run_status,
        interrupt_source=interrupted,
    )
    if tripped or restating:
        return ""
    if run_status != "completed":
        return ""
    snapshot = get_group_studio_snapshot(group_id, message_limit=1) or {}
    if str(snapshot.get("status") or "") == "closing":
        return ""
    from src.group_studio.workboard import finalize_host_dispatch

    finalize_host_dispatch(ctx, group_id)
    from src.group_studio.speaker_queue import get_queue

    queue = get_queue(group_id)
    return ctx.assigned_member_id or (queue[0] if queue else "") or get_group_studio_pending_speaker(group_id)


HOST_PHASE_ANNOUNCE_MARKERS = (
    "进入执行",
    "执行阶段",
    "开始执行",
    "进入验收",
    "验收阶段",
    "group_start_execution",
    "group_start_review",
)


def _host_announced_phase(body: str) -> bool:
    """主持人只口头切阶段、没调控场工具。"""

    return any(token in body for token in HOST_PHASE_ANNOUNCE_MARKERS)


def _host_turn_user_message(snapshot: dict[str, Any]) -> str:
    card = snapshot.get("taskCard") or {}
    status = str(snapshot.get("status") or "")
    if status == "drafting":
        awaiting = bool(snapshot.get("awaitingIntakeConfirm"))
        return (
            "当前是 intake 对谈，还没有开始群聊。只和人类把任务卡写清楚。"
            "写卡用 group_update_task_card；可以 group_invite_member 预邀工人入座，但不要点名、不要进入执行。"
            f"目标：{card.get('goal') or '（空）'}。"
            f"{'你已提请确认，等待人类点确认或说开始。' if awaiting else '目标仍空：继续问。目标已有且你认为密度够：group_request_intake_confirm（空 DoD 允许）。'}"
            "人类刚要求开始但你尚未提请确认：拦住并列出缺口（Goal 仍是硬条件；约束/范围由你判断）。\n"
            f"{format_turn_context(snapshot)}"
        )
    if status == "awaiting_human":
        return (
            "任务目标或约束已变更。请用复述说明新目标，等待人类确认。"
            "不要指派工人。"
            f"新目标：{card.get('goal') or ''}\n"
            f"{format_turn_context(snapshot)}"
        )
    if status == "executing":
        return (
            "当前已进入执行阶段。不要再口头宣布。"
            "立刻 group_assign_floor 指定谁写最终交付；最近消息里已经交稿则 group_start_review，不要重复指派或静音。"
            f"目标：{card.get('goal') or ''}\n"
            f"{format_turn_context(snapshot)}"
        )
    if status == "reviewing":
        items = snapshot.get("workItems") or []
        pending_review = [
            f"{item.get('id')}:{item.get('title')}"
            for item in items
            if item.get("status") == "done" and not item.get("reviewStatus")
        ]
        reworking = [
            f"{item.get('id')}:{item.get('title')}"
            for item in items
            if item.get("status") in {"open", "claimed"} and item.get("reviewStatus") == "rework"
        ]
        return (
            "当前已进入验收。逐个核对已完成工作项与 DoD："
            "合格的 group_review_work_item(verdict=accept)；不合格的 verdict=rework 并写清缺什么，系统会点名原工人补做。"
            "全部 accepted 且 DoD 齐了才 group_propose_close；不要在还有未验收或退回中的工作项时提议关闭。"
            f"待验收：{pending_review}\n"
            f"退回中：{reworking}\n"
            f"目标：{card.get('goal') or ''}\n"
            f"{format_turn_context(snapshot)}"
        )
    return (
        "请主持本轮讨论。先看可邀请 Agent 是否覆盖目标所需能力；缺人就邀请，再指派发言。"
        "可以一次 group_assign_floor 传入多人，中间不要等自己再问。"
        "若讨论已结束，必须调用 group_start_execution（可带 memberId），不要只发文本。"
        f"目标：{card.get('goal') or ''}\n"
        f"{format_turn_context(snapshot)}"
    )
