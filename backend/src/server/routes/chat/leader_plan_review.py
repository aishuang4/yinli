"""Leader 流中的 Plan 审核暂停和摘要输出。"""

import asyncio
from typing import Any, AsyncGenerator

from src.server.routes.chat.helpers import source_event
from src.server.routes.chat.runtime_events import runtime_trace_source_event


_PLAN_FINAL_ANSWER_CHUNK_DELAY_SECONDS = 0.012


def remember_pending_plan_review(state: Any, runtime_event: dict) -> None:
    """暂存已提交审核的计划，等待工具结果闭合后安全停止 Agent。"""

    state.pending_plan_review = True
    payload = runtime_event.get("payload") if isinstance(runtime_event.get("payload"), dict) else {}
    revision = int(payload.get("revision") or 0)
    state.pending_plan_review_answer = f"计划 Revision {revision} 已生成，等待你的确认。"


def is_pending_plan_review_ready(state: Any, event_type: str, event_data: dict) -> bool:
    """判断 plan_request_review 工具结果是否闭合。"""

    if not state.pending_plan_review or event_type != "TOOL_RESULT_END":
        return False
    tool_call_id = str(event_data.get("tool_call_id") or "")
    tool_name = str(event_data.get("tool_call_name") or "")
    if not tool_name and tool_call_id:
        tool_call = next((item for item in state.assistant_tool_calls if item.id == tool_call_id), None)
        tool_name = tool_call.name if tool_call else ""
    return is_plan_review_tool_name(tool_name)


def is_plan_review_tool_name(tool_name: str) -> bool:
    normalized = tool_name.replace(" ", "").replace("_", "").lower()
    return normalized == "planrequestreview"


async def stream_pending_plan_review_events(
    state: Any,
    *,
    session_id: str,
    stream_scope: str,
    agent_id: str,
    agent_name: str,
) -> AsyncGenerator[str, None]:
    """输出待审核计划摘要并结束当前起草 Run。"""

    plan_answer = state.pending_plan_review_answer
    state.pending_plan_review = False
    state.pending_plan_review_answer = ""
    state.stopped = True
    state.stop_reason = "plan_review_requested"
    if not plan_answer:
        return
    state.final_answer_started = True
    state.assistant_text += plan_answer
    deltas = [plan_answer[index:index + 120] for index in range(0, len(plan_answer), 120)]
    for index, delta in enumerate(deltas):
        yield source_event("FINAL_ANSWER_DELTA", {"delta": delta})
        yield runtime_trace_source_event(
            "AGENT_FINAL_ANSWER_DELTA",
            session_id=session_id,
            scope=stream_scope,
            source="runtime",
            source_event_type="PLAN_REVIEW_REQUESTED",
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            payload={"delta": delta},
            collect=state.runtime_events,
        )
        if index < len(deltas) - 1:
            await asyncio.sleep(_PLAN_FINAL_ANSWER_CHUNK_DELAY_SECONDS)
    yield source_event("ANSWER_READY", {})
