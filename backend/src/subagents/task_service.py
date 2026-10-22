"""Subagent 持久任务服务。

本服务不持有 live Agent；它只负责 invocation 的事实状态与 transcript。
"""
from __future__ import annotations


from typing import Any

from src.storage.records.trace_records import (
    append_subagent_transcript_item,
    create_subagent_invocation,
    get_subagent_invocation,
    update_subagent_invocation,
)
from src.subagents.task_models import can_transition_subagent_task


class InvalidSubagentTaskTransition(ValueError):
    """调用方尝试覆盖终态或执行非法状态迁移。"""


class SubagentTaskService:
    """管理单个 invocation 的创建、迁移和 transcript 事实记录。"""

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建 queued task，执行者必须显式转为 running。"""

        task = create_subagent_invocation({**data, "status": "queued"})
        self._append_state_transcript(task, from_status="", to_status="queued")
        return task

    def transition(
        self,
        invocation_id: str,
        *,
        target_status: str,
        result_preview: str = "",
        error: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """验证并持久化一次状态迁移。"""

        current = get_subagent_invocation(invocation_id)
        if current is None:
            raise KeyError(f"Unknown subagent invocation: {invocation_id}")
        current_status = str(current["status"])
        if not can_transition_subagent_task(current_status, target_status):
            raise InvalidSubagentTaskTransition(
                f"Invalid subagent task transition: {current_status} -> {target_status}",
            )
        updated = update_subagent_invocation(
            invocation_id,
            status=target_status,
            result_preview=result_preview,
            error=error,
        )
        if updated is None:
            raise KeyError(f"Unknown subagent invocation: {invocation_id}")
        self._append_state_transcript(updated, from_status=current_status, to_status=target_status, reason=reason)
        return updated

    @staticmethod
    def _append_state_transcript(
        task: dict[str, Any],
        *,
        from_status: str,
        to_status: str,
        reason: str = "",
    ) -> None:
        append_subagent_transcript_item(
            invocation_id=str(task["id"]),
            kind="task.status_changed",
            payload={
                "from": from_status,
                "to": to_status,
                "reason": reason,
            },
        )
