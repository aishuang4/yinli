"""Run 终态事实写入前的 Domain activity 收敛屏障。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ActiveDomainBlock:
    """Normalizer 当前仍未产生终态事实的活动 Block。"""

    block_id: str
    kind: str
    aggregate_type: str
    aggregate_id: str


class DomainCompletionBarrier:
    """按叶子/后开先关顺序生成终态领域事件，根 Run 事实由调用方最后写入。"""

    _EVENT_PREFIX = {
        "text": "agent.output",
        "reasoning": "reasoning",
        "thinking": "thinking",
        "tool": "tool.result",
        "skill": "skill",
        "context": "context",
        "approval": "interaction",
        "plan": "plan",
        "artifact": "artifact",
        "error": "error",
        "subagent": "subagent",
        "team": "team",
        "team-member": "team.member",
        "team-task": "team.task",
        "team-activity": "team.activity",
        "team-mail": "team.mail",
    }

    def terminal_events(
        self,
        active: list[ActiveDomainBlock],
        *,
        run_outcome: Literal["completed", "failed", "cancelled"],
    ) -> list[tuple[ActiveDomainBlock, str, dict]]:
        suffix = "completed" if run_outcome == "completed" else "cancelled"
        result: list[tuple[ActiveDomainBlock, str, dict]] = []
        for block in reversed(active):
            prefix = self._EVENT_PREFIX.get(block.kind)
            if prefix is None:
                continue
            result.append((
                block,
                f"{prefix}.{suffix}",
                {"blockId": block.block_id},
            ))
        return result
