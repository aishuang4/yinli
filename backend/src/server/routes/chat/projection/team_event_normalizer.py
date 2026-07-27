"""Team outbox 事实到 Chat Domain Event 的专用归一化规则。"""

from __future__ import annotations

from typing import Any

from .domain_events import DomainEvent


class TeamEventNormalizerMixin:
    """依赖宿主提供公共 Block append/open/delta/close 原语。"""

    def _team_event(self, data: dict[str, Any]) -> list[DomainEvent]:
        """把单条 Team outbox 事实翻译为规范化 Team Block 领域事件。"""

        source_type = str(data.get("type") or "")
        team_id = str(data.get("teamRunId") or self.run_id)
        team_block_id = f"team:{team_id}"
        actor_id = str(data.get("actorId") or "")
        task_id = str(data.get("taskId") or "")
        status = str(data.get("status") or "")
        phase = str(data.get("phase") or "")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        produced: list[DomainEvent] = []

        if source_type == "run.created":
            run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
            initial = {
                "teamRunId": team_id,
                **run,
                "counts": data.get("counts") or {},
                "updatedAt": data.get("updatedAt") or run.get("updatedAt") or data.get("timestamp"),
            }
            if team_block_id not in self._active:
                produced.extend(self._start_block(
                    "team", team_block_id, "team", initial, aggregate_id=team_id,
                ))
            leader = payload.get("leader") if isinstance(payload.get("leader"), dict) else {}
            leader_id = str(leader.get("id") or actor_id or "leader")
            if leader:
                produced.extend(self._start_team_member(team_id, leader_id, leader, team_block_id))
            return produced

        if team_block_id not in self._active:
            return []

        root_patch: dict[str, Any] = {
            "counts": data.get("counts") or {},
            "updatedAt": data.get("updatedAt") or data.get("timestamp"),
        }
        if source_type.startswith("run."):
            run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
            root_patch.update(run)
            if status:
                root_patch["status"] = status
        produced.append(self._delta_block(
            "team", team_id, "team", root_patch,
            block_id=team_block_id, event_suffix="updated",
        ))

        if source_type == "run.phase.changed":
            return produced
        if source_type in {"run.completed", "run.failed", "run.cancelled"}:
            outcome = "failed" if source_type == "run.failed" else (
                "cancelled" if source_type == "run.cancelled" else "completed"
            )
            produced.extend(self._close_team_tree(team_block_id, outcome))
            return produced

        if source_type == "member.spawned":
            member = payload.get("member") if isinstance(payload.get("member"), dict) else {}
            produced.extend(self._start_team_member(team_id, actor_id, member, team_block_id))
            return produced
        if source_type == "member.status.changed":
            member_block_id = self._team_member_block_id(team_id, actor_id)
            member = payload.get("member") if isinstance(payload.get("member"), dict) else {}
            if member_block_id not in self._active:
                produced.extend(self._start_team_member(team_id, actor_id, member, team_block_id))
            elif status in {"failed", "stopped"}:
                outcome = "failed" if status == "failed" else "completed"
                produced.append(self._close_block(
                    "team.member", actor_id, member_block_id, outcome, member,
                ))
            else:
                produced.append(self._delta_block(
                    "team.member", actor_id, "team-member", member,
                    block_id=member_block_id, event_suffix="updated",
                ))
            return produced

        if source_type == "task.created":
            task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
            task_block_id = self._team_task_block_id(team_id, task_id)
            if task_block_id not in self._active:
                produced.extend(self._start_block(
                    "team.task",
                    task_block_id,
                    "team-task",
                    {"teamRunId": team_id, **task},
                    aggregate_id=task_id,
                    parent_id=team_block_id,
                ))
            return produced
        if source_type in {"task.claimed", "task.unlocked"}:
            task_block_id = self._team_task_block_id(team_id, task_id)
            task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
            if task_block_id in self._active:
                produced.append(self._delta_block(
                    "team.task", task_id, "team-task", task,
                    block_id=task_block_id, event_suffix="updated",
                ))
            return produced
        if source_type in {"task.completed", "task.failed", "task.cancelled"}:
            task_block_id = self._team_task_block_id(team_id, task_id)
            self._terminal_team_tasks.add(task_block_id)
            task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
            outcome = "failed" if source_type == "task.failed" else (
                "cancelled" if source_type == "task.cancelled" else "completed"
            )
            produced.extend(self._close_team_descendants(
                task_block_id,
                outcome,
                exclude_kinds={"tool"},
            ))
            if task_block_id in self._active:
                if self._open_team_descendants(task_block_id, kind="tool"):
                    self._pending_team_task_terminals[task_block_id] = (
                        task_id,
                        outcome,
                        task,
                    )
                    produced.append(self._delta_block(
                        "team.task",
                        task_id,
                        "team-task",
                        task,
                        block_id=task_block_id,
                        event_suffix="updated",
                    ))
                else:
                    produced.append(self._close_block(
                        "team.task", task_id, task_block_id, outcome, task,
                    ))
            return produced

        if source_type in {"member.output.delta", "member.thinking.delta"}:
            task_block_id = self._team_task_block_id(team_id, task_id) if task_id else ""
            if task_block_id in self._terminal_team_tasks:
                return produced
            delta = str(payload.get("delta") or "")
            if not delta:
                return produced
            activity_type = "thinking" if source_type == "member.thinking.delta" else "output"
            activity_scope = task_id or f"{actor_id}:{phase or 'runtime'}"
            activity_id = f"{activity_scope}:{activity_type}"
            activity_block_id = f"team-activity:{team_id}:{activity_id}"
            if activity_block_id not in self._active:
                parent_id = self._team_task_block_id(team_id, task_id) if task_id else team_block_id
                if parent_id not in self._active:
                    parent_id = team_block_id
                produced.extend(self._start_block(
                    "team.activity",
                    activity_block_id,
                    "team-activity",
                    {
                        "teamRunId": team_id,
                        "memberId": actor_id,
                        "taskId": task_id,
                        "teamPhase": phase,
                        "activityType": activity_type,
                        "content": "",
                        "status": "running",
                    },
                    aggregate_id=activity_id,
                    parent_id=parent_id,
                ))
            produced.append(self._delta_block(
                "team.activity", activity_id, "team-activity", {"contentDelta": delta},
                block_id=activity_block_id,
            ))
            return produced

        if source_type.startswith("member.tool."):
            tool_call_id = str(payload.get("toolCallId") or "")
            tool_block_id = f"tool:team:{team_id}:{tool_call_id}"
            task_block_id = self._team_task_block_id(team_id, task_id) if task_id else ""
            if task_block_id in self._terminal_team_tasks and tool_block_id not in self._active:
                return produced
            produced.extend(self._team_tool_event(
                source_type=source_type,
                team_id=team_id,
                team_block_id=team_block_id,
                actor_id=actor_id,
                task_id=task_id,
                payload=payload,
            ))
            produced.extend(self._close_pending_team_task(task_id, team_id))
            return produced

        if source_type in {"member.iteration.recovery", "member.iteration.exhausted"}:
            activity_id = str(data.get("id") or f"{actor_id}:{task_id}:iteration")
            activity_block_id = f"team-activity:{team_id}:{activity_id}"
            parent_id = self._team_task_block_id(team_id, task_id) if task_id else team_block_id
            if parent_id not in self._active:
                parent_id = team_block_id
            initial = {
                "teamRunId": team_id,
                "memberId": actor_id,
                "taskId": task_id,
                "activityType": "iteration",
                "status": status,
                **payload,
            }
            produced.extend(self._start_block(
                "team.activity", activity_block_id, "team-activity", initial,
                aggregate_id=activity_id, parent_id=parent_id,
            ))
            outcome = "failed" if source_type.endswith("exhausted") else "completed"
            produced.append(self._close_block(
                "team.activity", activity_id, activity_block_id, outcome, {},
            ))
            return produced

        if source_type == "mail.sent":
            message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
            message_id = str(message.get("id") or "")
            if message_id:
                mail_block_id = f"team-mail:{team_id}:{message_id}"
                if mail_block_id not in self._active:
                    produced.extend(self._start_block(
                        "team.mail",
                        mail_block_id,
                        "team-mail",
                        {**message, "deliveryStatus": status or "delivered"},
                        aggregate_id=message_id,
                        parent_id=team_block_id,
                    ))
            return produced
        if source_type in {"mail.read", "mail.acted"}:
            delivery_status = "acted" if source_type == "mail.acted" else "read"
            for message_id in payload.get("messageIds") or []:
                mail_block_id = f"team-mail:{team_id}:{message_id}"
                if mail_block_id in self._active:
                    produced.append(self._delta_block(
                        "team.mail",
                        str(message_id),
                        "team-mail",
                        {"deliveryStatus": delivery_status, "updatedAt": data.get("updatedAt")},
                        block_id=mail_block_id,
                        event_suffix="updated",
                    ))
            return produced
        return produced
