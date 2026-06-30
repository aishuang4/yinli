"""Plan Mode Runtime Event 构造函数。"""

from typing import Any

from pydantic import BaseModel, Field

from src.server.routes.chat.plan_mode.models import PlanModeContext
from src.server.routes.chat.runtime_events import build_runtime_event


class PlanDomainEvent(BaseModel):
    """Plan Mode 内部领域事件。"""

    type: str
    status: str
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


def build_plan_domain_event(
    event_type: str,
    *,
    status: str,
    reason: str = "",
    payload: dict[str, Any] | None = None,
) -> PlanDomainEvent:
    """构造 Plan Mode 内部领域事件。"""

    return PlanDomainEvent(
        type=event_type,
        status=status,
        reason=reason,
        payload=payload or {},
    )


def plan_domain_event_to_runtime_event(
    event: PlanDomainEvent,
    *,
    context: PlanModeContext,
) -> dict[str, Any]:
    """将 Plan 内部领域事件转换为跨层 RuntimeEvent。"""

    return build_runtime_event(
        event.type,
        session_id=context.session_id,
        run_id=context.run_id,
        scope="plan",
        source="runtime",
        agent_id=context.agent_id,
        agent_name=context.agent_name,
        status=event.status,
        reason=event.reason,
        payload={
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            **event.payload,
        },
    )


def build_plan_event(
    event_type: str,
    *,
    context: PlanModeContext,
    status: str,
    reason: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 Plan Mode Runtime Event。"""

    return plan_domain_event_to_runtime_event(
        build_plan_domain_event(
            event_type,
            status=status,
            reason=reason,
            payload=payload,
        ),
        context=context,
    )
