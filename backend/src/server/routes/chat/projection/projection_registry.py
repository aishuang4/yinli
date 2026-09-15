"""隔离 per-kind projector 的注册表与候选 Block mutation。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from .domain_events import DomainEvent
from .frame_schema import BlockOutcome, DisplaySnapshot


@dataclass(frozen=True, slots=True)
class ProjectionMutation:
    """Projector 输出的无 seq 候选变更，统一由 writer 分配 seq。"""

    op: Literal["open", "delta", "close", "done"]
    id: str = ""
    parent: str | None = None
    kind: str = ""
    data_version: int = 1
    patch: Any = None
    outcome: BlockOutcome | None = None


Projector = Callable[[DomainEvent, DisplaySnapshot], Iterable[ProjectionMutation]]


class ProjectionRegistry:
    """按 Domain Event type 精确路由，projector 之间不互相 import。"""

    def __init__(self) -> None:
        self._projectors: dict[str, Projector] = {}

    def register(self, event_types: Iterable[str], projector: Projector) -> None:
        for event_type in event_types:
            if event_type in self._projectors:
                raise ValueError(f"Domain Event projector 重复注册: {event_type}")
            self._projectors[event_type] = projector

    def project(self, event: DomainEvent, snapshot: DisplaySnapshot) -> list[ProjectionMutation]:
        projector = self._projectors.get(event.type)
        if projector is None:
            return []
        return list(projector(event, snapshot))


def default_projection_registry() -> ProjectionRegistry:
    """构建 Phase 1 的 message/text/tool 离线 projector 集合。"""

    from .projectors.message import EVENT_TYPES as MESSAGE_EVENTS, project as project_message
    from .projectors.text import EVENT_TYPES as TEXT_EVENTS, project as project_text
    from .projectors.tool import EVENT_TYPES as TOOL_EVENTS, project as project_tool
    from .projectors.approval import EVENT_TYPES as APPROVAL_EVENTS, project as project_approval
    from .projectors.artifact import EVENT_TYPES as ARTIFACT_EVENTS, project as project_artifact
    from .projectors.error import EVENT_TYPES as ERROR_EVENTS, project as project_error
    from .projectors.plan import EVENT_TYPES as PLAN_EVENTS, project as project_plan
    from .projectors.reasoning import EVENT_TYPES as REASONING_EVENTS, project as project_reasoning
    from .projectors.skill import EVENT_TYPES as SKILL_EVENTS, project as project_skill
    from .projectors.subagent import EVENT_TYPES as SUBAGENT_EVENTS, project as project_subagent
    from .projectors.team import EVENT_TYPES as TEAM_EVENTS, project as project_team
    from .projectors.thinking import EVENT_TYPES as THINKING_EVENTS, project as project_thinking
    from .projectors.context import EVENT_TYPES as CONTEXT_EVENTS, project as project_context

    registry = ProjectionRegistry()
    registry.register(MESSAGE_EVENTS, project_message)
    registry.register(TEXT_EVENTS, project_text)
    registry.register(TOOL_EVENTS, project_tool)
    registry.register(REASONING_EVENTS, project_reasoning)
    registry.register(THINKING_EVENTS, project_thinking)
    registry.register(SKILL_EVENTS, project_skill)
    registry.register(APPROVAL_EVENTS, project_approval)
    registry.register(PLAN_EVENTS, project_plan)
    registry.register(ARTIFACT_EVENTS, project_artifact)
    registry.register(ERROR_EVENTS, project_error)
    registry.register(SUBAGENT_EVENTS, project_subagent)
    registry.register(TEAM_EVENTS, project_team)
    registry.register(CONTEXT_EVENTS, project_context)
    return registry
