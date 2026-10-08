"""HITL 人工审批 — pending interaction 的创建/清理/序列化与恢复事件构建。"""

import json
import uuid

from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.permission import PermissionBehavior, PermissionRule

from src.server.routes.chat.models import (
    PENDING_AGENTS,
    PENDING_INTERACTIONS,
    ChatResumeRequest,
    ChatSkillRun,
    ChatToolRun,
    PendingInteraction,
)
from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.trace_recorder import _record_run_event

_DEFAULT_AUTOMATIC_DENIAL_MESSAGE = "该工具已自动拒绝。"


def build_automatic_confirm_resume(
    *,
    event_type: str,
    reply_id: str,
    tool_calls: list[dict],
    confirmed_by_id: dict[str, bool] | None = None,
    default_confirmed: bool = False,
    denial_message: str = "",
) -> UserConfirmResultEvent | ExternalExecutionResultEvent:
    """构造自动批准/拒绝的 AgentScope 恢复事件。

    Team Member 与 scheduled Leader 只共享事件形状，不共享决策。
    """

    blocks = [_as_tool_call_block(item) for item in tool_calls]
    message = denial_message.strip() or _DEFAULT_AUTOMATIC_DENIAL_MESSAGE
    if event_type == "REQUIRE_USER_CONFIRM":
        return UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=(confirmed_by_id or {}).get(block.id, default_confirmed),
                    tool_call=block,
                    rules=None,
                )
                for block in blocks
            ],
        )
    return ExternalExecutionResultEvent(
        reply_id=reply_id,
        execution_results=[
            ToolResultBlock(
                id=block.id,
                name=block.name,
                output=[TextBlock(text=message)],
                state=ToolResultState.DENIED,
            )
            for block in blocks
        ],
    )


def _as_tool_call_block(item: dict) -> ToolCallBlock:
    """把 HITL tool_call 字典规范成 ToolCallBlock。"""

    payload = dict(item)
    raw_input = payload.get("input")
    if isinstance(raw_input, dict):
        payload["input"] = json.dumps(raw_input, ensure_ascii=False)
    elif raw_input is None:
        payload["input"] = "{}"
    return ToolCallBlock.model_validate(payload)


def _create_pending_interaction(
    *,
    interaction_type: str,
    agent: object,
    session_id: str,
    agent_id: str,
    agent_name: str,
    model_name: str,
    reply_id: str,
    tool_calls: list[dict],
    message: str,
    assistant_text: str,
    skill_runs: list[ChatSkillRun],
    reply_generation: int = 0,
    resume_tool_calls: list[dict] | None = None,
    assistant_tool_calls: list[ChatToolRun] | None = None,
    scope: str = "single",
    chat_data: dict | None = None,
    plan_mode_manager: object | None = None,
    runtime_event_sources: list[object] | None = None,
    run_id: str | None = None,
    persist: bool = True,
) -> PendingInteraction:
    """Create and cache an in-process human interaction."""
    interaction = PendingInteraction(
        id=uuid.uuid4().hex,
        type=interaction_type,
        session_id=session_id,
        agent_id=agent_id,
        agent_name=agent_name,
        model_name=model_name,
        reply_id=reply_id,
        run_id=run_id or current_chat_background_run_id(),
        tool_calls=tool_calls,
        resume_tool_calls=tool_calls if resume_tool_calls is None else resume_tool_calls,
        message=message,
        assistant_text=assistant_text,
        reply_generation=reply_generation,
        assistant_tool_calls=assistant_tool_calls or [],
        skill_runs=skill_runs,
        scope=scope,
        chat_data=chat_data,
        plan_mode_manager=plan_mode_manager,
        runtime_event_sources=runtime_event_sources or [],
    )
    PENDING_INTERACTIONS[interaction.id] = interaction
    PENDING_AGENTS[interaction.id] = agent
    _record_run_event(
        session_id=interaction.session_id,
        event_type="HITL_REQUIRED",
        persist=persist,
        scope=interaction.scope,
        agent_id=interaction.agent_id,
        agent_name=interaction.agent_name,
        status="waiting",
        title=f"{interaction.scope.title()} human approval required",
        payload=_hitl_payload(interaction),
    )
    return interaction


def _merge_hitl_tool_calls(
    event_tool_calls: list[dict],
    active_tool_calls: list[ChatToolRun],
) -> list[dict]:
    """合并同一模型回复中仍需审批并回传结果的工具调用。"""

    merged = [dict(tool_call) for tool_call in event_tool_calls]
    seen_ids = {str(tool_call.get("id") or "") for tool_call in merged}
    for tool_call in active_tool_calls:
        if tool_call.status not in {"calling", "running"}:
            continue
        if not tool_call.id or tool_call.id in seen_ids:
            continue
        merged.append({
            "type": "tool_call",
            "id": tool_call.id,
            "name": tool_call.name or "tool",
            "input": tool_call.args,
            "state": "asking",
        })
        seen_ids.add(tool_call.id)
    return merged


def _clear_pending_interaction(interaction_id: str) -> None:
    """Remove a pending interaction and its runtime Agent reference."""
    PENDING_INTERACTIONS.pop(interaction_id, None)
    PENDING_AGENTS.pop(interaction_id, None)


def _hitl_payload(interaction: PendingInteraction) -> dict:
    """Build frontend payload for a pending human interaction."""
    return {
        "interactionId": interaction.id,
        "type": interaction.type,
        "sessionId": interaction.session_id,
        "agentId": interaction.agent_id,
        "agentName": interaction.agent_name,
        "replyId": interaction.reply_id,
        "runId": interaction.run_id,
        "toolCalls": interaction.tool_calls,
        "scope": interaction.scope,
        "createdAt": interaction.created_at,
    }


def _build_resume_event(
    interaction: PendingInteraction,
    data: ChatResumeRequest,
) -> UserConfirmResultEvent | ExternalExecutionResultEvent:
    """Build AgentScope resume event from the user's decision."""
    resume_tool_calls = (
        interaction.tool_calls
        if interaction.resume_tool_calls is None
        else interaction.resume_tool_calls
    )
    if interaction.type == "REQUIRE_USER_CONFIRM":
        confirmed = data.action == "approve"
        remembered_tool_names: set[str] = set()
        confirm_results: list[ConfirmResult] = []
        for tool_call in resume_tool_calls:
            tool_name = str(tool_call.get("name") or "").strip()
            rules = None
            if confirmed and data.remember_approval and tool_name and tool_name not in remembered_tool_names:
                rules = _session_allow_rules([tool_call])
                remembered_tool_names.add(tool_name)
            confirm_results.append(ConfirmResult(
                confirmed=confirmed,
                tool_call=ToolCallBlock.model_validate(tool_call),
                rules=rules,
            ))
        return UserConfirmResultEvent(
            reply_id=interaction.reply_id,
            confirm_results=confirm_results,
        )

    if interaction.type == "REQUIRE_EXTERNAL_EXECUTION":
        return ExternalExecutionResultEvent(
            reply_id=interaction.reply_id,
            execution_results=[
                ToolResultBlock(
                    id=str(tool_call.get("id", "")),
                    name=str(tool_call.get("name", "tool")),
                    output=[
                        TextBlock(text=(
                            data.external_results.get(str(tool_call.get("id", "")), "")
                            if data.action == "approve"
                            else "用户拒绝或取消了这次外部执行/补充信息请求。"
                        )),
                    ],
                    state=ToolResultState.SUCCESS if data.action == "approve" else ToolResultState.DENIED,
                )
                for tool_call in resume_tool_calls
            ],
        )

    raise ValueError(f"Unsupported interaction type: {interaction.type}")


def _session_allow_rules(tool_calls: list[dict]) -> list[PermissionRule]:
    """构建当前会话有效的同类工具允许规则。"""

    tool_names = {
        str(tool_call.get("name") or "").strip()
        for tool_call in tool_calls
        if str(tool_call.get("name") or "").strip()
    }
    return [
        PermissionRule(
            tool_name=tool_name,
            rule_content=None,
            behavior=PermissionBehavior.ALLOW,
            source="chat_session_approval",
        )
        for tool_name in sorted(tool_names)
    ]
