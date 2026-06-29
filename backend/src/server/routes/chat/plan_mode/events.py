"""Plan Mode Runtime Event 构造函数。"""

from typing import Any

from src.server.routes.chat.plan_mode.models import PlanModeContext
from src.server.routes.chat.runtime_events import build_runtime_event


def build_plan_event(
    event_type: str,
    *,
    context: PlanModeContext,
    status: str,
    reason: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 Plan Mode Runtime Event。"""

    return build_runtime_event(
        event_type,
        session_id=context.session_id,
        run_id=context.run_id,
        scope="plan",
        source="runtime",
        agent_id=context.agent_id,
        agent_name=context.agent_name,
        status=status,
        reason=reason,
        payload={
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            **(payload or {}),
        },
    )
