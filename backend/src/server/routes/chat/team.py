"""AgentScope-style Team worker 编排。

本模块仍运行在工作台自己的 SQLite Team 配置之上，但语义向 AgentScope
TeamCreate / AgentCreate / TeamSay 靠拢：
- Leader 负责 dispatch。
- Worker 在独立 ReAct 循环中执行。
- Worker 完成后的最终输出视为发送给 Leader 的 TeamSay report。
- Leader 只基于 worker reports 汇总最终答案。
"""

import asyncio
from typing import AsyncGenerator

from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.models import (
    ChatMessage,
    ChatSkillRun,
    ChatTeamActivity,
    ChatTeamMemberPayload,
)
from src.server.routes.chat.runtime_events import runtime_event_sse
from src.server.routes.chat.team_assignment_planner import (
    TeamWorkerAssignment,
    plan_team_worker_assignments,
)
from src.server.routes.chat.team_scheduler import (
    TeamSchedulerState,
    decide_worker_schedule,
)
from src.server.routes.chat.team_state import (
    _expand_selected_workers_with_dependencies,
    _find_team_activity,
    _member_dependency_ids,
    _member_names,
    _replace_team_activity,
)
from src.server.routes.chat.team_worker_runtime import (
    TeamWorkerRuntimeRequest,
    run_team_worker,
)
from src.server.routes.chat.team_worker_selector import (
    WORKER_SELECTION_PARSE_FAILED_REASON,
    filter_team_members_for_runtime,
    select_team_workers_for_route,
)


async def _run_team_workers(
    *,
    data: ChatMessage,
    session_id: str,
    team_activities: list[ChatTeamActivity],
    model_name: str,
    leader_system_prompt: str,
    skill_runs: list[ChatSkillRun],
) -> AsyncGenerator[str, None]:
    """Run worker Agents concurrently and merge their SSE events."""
    runtime_members = await _select_runtime_team_members(
        data=data,
        model_name=model_name,
    )
    skipped_events = _mark_unselected_workers_skipped(
        all_members=data.team_members,
        runtime_members=runtime_members,
        team_activities=team_activities,
        session_id=session_id,
    )
    for skipped_event in skipped_events:
        yield skipped_event
    data = data.model_copy(update={"team_members": runtime_members})

    workers = [
        member
        for member in data.team_members
        if member.role != "leader"
        and member.agent_id != data.agent_id
        and (
            not (activity := _find_team_activity(team_activities, member.agent_id))
            or activity.status not in {"completed", "waiting"}
        )
    ]
    if not workers:
        yield _sse_event("TEAM_ACTIVITY", {
            "activities": [item.model_dump(by_alias=True) for item in team_activities],
        })
        return

    assignments = await plan_team_worker_assignments(
        message=data.message,
        team_name=data.team_name,
        team_members=data.team_members,
        model_name=model_name,
    )
    workers = _apply_worker_assignments(
        workers=workers,
        assignments=assignments,
        team_activities=team_activities,
    )
    yield runtime_event_sse(
        "TEAM_ASSIGNMENT_PLANNED",
        session_id=session_id,
        scope="team",
        status="completed",
        payload={"workerCount": len(assignments)},
    )

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    pause_event = asyncio.Event()

    for event in _initialize_worker_states(
        workers=workers,
        team_activities=team_activities,
        session_id=session_id,
    ):
        yield event

    yield _sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in team_activities],
    })

    pending: dict[str, ChatTeamMemberPayload] = {member.agent_id: member for member in workers}
    running: dict[asyncio.Task[None], str] = {}
    scheduler_state = TeamSchedulerState(
        pending=pending,
        all_worker_ids={member.agent_id for member in workers},
    )

    def start_ready_workers() -> None:
        ready: list[ChatTeamMemberPayload] = []
        for member in list(pending.values()):
            decision = decide_worker_schedule(member=member, workers=workers, state=scheduler_state)
            if decision.status == "invalid_dependency":
                pending.pop(member.agent_id, None)
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "failed",
                            "detail": "依赖配置无效：找不到指定的上游 Worker。",
                            "wait_reason": "依赖配置无效：找不到指定的上游 Worker。",
                        }),
                    )
                    queue.put_nowait(runtime_event_sse(
                        "TEAM_WORKER_FAILED",
                        session_id=session_id,
                        scope="worker",
                        agent_id=member.agent_id,
                        agent_name=member.name,
                        team_activity_id=activity.id,
                        status="failed",
                        reason="invalid_dependency",
                        payload={"dependsOn": decision.dependencies},
                    ))
                scheduler_state.failed_workers.add(member.agent_id)
                continue
            if decision.status == "upstream_failed":
                pending.pop(member.agent_id, None)
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "failed",
                            "detail": f"上游 Worker 失败，跳过执行：{', '.join(_member_names(decision.dependencies, workers))}",
                            "wait_reason": f"上游 Worker 失败：{', '.join(_member_names(decision.dependencies, workers))}",
                        }),
                    )
                    queue.put_nowait(runtime_event_sse(
                        "TEAM_WORKER_SKIPPED",
                        session_id=session_id,
                        scope="worker",
                        agent_id=member.agent_id,
                        agent_name=member.name,
                        team_activity_id=activity.id,
                        status="failed",
                        reason="upstream_failed",
                        payload={"dependsOn": decision.dependencies},
                    ))
                scheduler_state.failed_workers.add(member.agent_id)
                continue
            if decision.status == "waiting":
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "pending",
                            "detail": f"等待依赖 Worker 完成：{', '.join(_member_names(decision.dependencies, workers))}",
                            "wait_reason": f"等待依赖 Worker 完成：{', '.join(_member_names(decision.dependencies, workers))}",
                        }),
                    )
                    queue.put_nowait(runtime_event_sse(
                        "TEAM_WORKER_BLOCKED_BY_DEPENDENCY",
                        session_id=session_id,
                        scope="worker",
                        agent_id=member.agent_id,
                        agent_name=member.name,
                        team_activity_id=activity.id,
                        status="pending",
                        reason="waiting_dependency",
                        payload={"dependsOn": decision.dependencies},
                    ))
                continue
            ready.append(member)

        for member in ready:
            pending.pop(member.agent_id, None)
            decision = decide_worker_schedule(member=member, workers=workers, state=scheduler_state)
            dependency_outputs = decision.dependency_outputs
            activity = _find_team_activity(team_activities, member.agent_id)
            if activity:
                _replace_team_activity(
                    team_activities,
                    activity.model_copy(update={
                        "status": "running",
                        "wait_reason": "",
                        "dependency_output_count": len(dependency_outputs),
                        "detail": (
                            f"依赖已满足，开始执行。已接收 {len(dependency_outputs)} 个上游输出。"
                            if dependency_outputs
                            else activity.detail
                        ),
                    }),
                )
                queue.put_nowait(runtime_event_sse(
                    "TEAM_WORKER_STARTED",
                    session_id=session_id,
                    scope="worker",
                    agent_id=member.agent_id,
                    agent_name=member.name,
                    team_activity_id=activity.id,
                    status="running",
                    payload={
                        "dependencyOutputCount": len(dependency_outputs),
                        "executionMode": member.execution_mode,
                    },
                ))
            task = asyncio.create_task(run_team_worker(TeamWorkerRuntimeRequest(
                data=data,
                session_id=session_id,
                team_activities=team_activities,
                model_name=model_name,
                leader_system_prompt=leader_system_prompt,
                skill_runs=skill_runs,
                member=member,
                dependency_outputs=dependency_outputs,
                queue=queue,
                pause_event=pause_event,
            )))
            running[task] = member.agent_id

    start_ready_workers()
    await queue.put(_sse_event("TEAM_ACTIVITY", {
        "activities": [item.model_dump(by_alias=True) for item in team_activities],
    }))

    while running or pending:
        if not running and pending:
            for member in list(pending.values()):
                activity = _find_team_activity(team_activities, member.agent_id)
                if activity:
                    _replace_team_activity(
                        team_activities,
                        activity.model_copy(update={
                            "status": "failed",
                            "detail": "依赖配置形成循环或上游 Worker 失败，无法继续调度。",
                            "wait_reason": "依赖配置形成循环或上游 Worker 失败。",
                        }),
                    )
                scheduler_state.failed_workers.add(member.agent_id)
            pending.clear()
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })
            break

        item = await queue.get()
        if item is None:
            done_tasks = [task for task in running if task.done()]
            for task in done_tasks:
                worker_id = running.pop(task)
                activity = _find_team_activity(team_activities, worker_id)
                if activity and activity.status == "completed":
                    scheduler_state.completed_outputs[worker_id] = {
                        "agentId": activity.agent_id,
                        "name": activity.agent_name,
                        "output": activity.output,
                    }
                elif activity and activity.status in {"failed", "waiting"}:
                    scheduler_state.failed_workers.add(worker_id)
            if pause_event.is_set():
                for task in running:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*running.keys(), return_exceptions=True)
                while not queue.empty():
                    queued = queue.get_nowait()
                    if queued is not None:
                        yield queued
                return
            start_ready_workers()
            yield _sse_event("TEAM_ACTIVITY", {
                "activities": [item.model_dump(by_alias=True) for item in team_activities],
            })
            continue
        yield item

    await asyncio.gather(*running.keys(), return_exceptions=True)


async def _select_runtime_team_members(
    *,
    data: ChatMessage,
    model_name: str,
) -> list[ChatTeamMemberPayload]:
    """在 Team Runtime 内选择本轮实际参与的成员。"""

    try:
        selected_member_ids, reason = await select_team_workers_for_route(
            message=data.message,
            agent_id=data.agent_id or "",
            team_name=data.team_name,
            team_members=data.team_members,
            model_name=model_name,
            selector_prompt=data.worker_selector_prompt,
        )
    except Exception:
        return data.team_members

    if not selected_member_ids and reason == WORKER_SELECTION_PARSE_FAILED_REASON:
        return data.team_members

    return filter_team_members_for_runtime(
        members=data.team_members,
        selected_member_ids=selected_member_ids,
        leader_agent_id=data.agent_id or "",
        keep_all_when_empty=False,
    )


def _mark_unselected_workers_skipped(
    *,
    all_members: list[ChatTeamMemberPayload],
    runtime_members: list[ChatTeamMemberPayload],
    team_activities: list[ChatTeamActivity],
    session_id: str,
) -> list[str]:
    """把未被本轮 Team Runtime 选中的 Worker 标记为跳过。"""

    runtime_ids = {member.agent_id for member in runtime_members}
    events: list[str] = []
    for member in all_members:
        if member.role == "leader" or member.agent_id in runtime_ids:
            continue
        activity = _find_team_activity(team_activities, member.agent_id)
        if not activity or activity.status in {"completed", "waiting"}:
            continue
        _replace_team_activity(
            team_activities,
            activity.model_copy(update={
                "status": "skipped",
                "detail": "Team Runtime 判断本轮任务不需要该 Worker 参与。",
                "skipped_reason": "Team Runtime 判断本轮任务不需要该 Worker 参与。",
                "wait_reason": "",
            }),
        )
        events.append(runtime_event_sse(
            "TEAM_WORKER_SKIPPED",
            session_id=session_id,
            scope="worker",
            agent_id=member.agent_id,
            agent_name=member.name,
            team_activity_id=activity.id,
            status="completed",
            reason="not_selected_for_task",
            payload={"detail": "Team Runtime 判断本轮任务不需要该 Worker 参与。"},
        ))
    return events


def _apply_worker_assignments(
    *,
    workers: list[ChatTeamMemberPayload],
    assignments: dict[str, TeamWorkerAssignment],
    team_activities: list[ChatTeamActivity],
) -> list[ChatTeamMemberPayload]:
    """把本轮分派写入 Team 活动，供 UI 和 Worker prompt 共用。"""

    updated_workers: list[ChatTeamMemberPayload] = []
    for member in workers:
        assignment = assignments.get(member.agent_id)
        if not assignment:
            updated_workers.append(member)
            continue
        member_dependencies = list(assignment.depends_on or member.depends_on)
        updated_member = member.model_copy(update={"depends_on": member_dependencies})
        updated_workers.append(updated_member)
        activity = _find_team_activity(team_activities, member.agent_id)
        if not activity:
            continue

        detail_parts = [assignment.task]
        if assignment.expected_output:
            detail_parts.append(f"期望输出：{assignment.expected_output}")
        if assignment.constraints:
            detail_parts.append(f"约束：{assignment.constraints}")

        _replace_team_activity(
            team_activities,
            activity.model_copy(update={
                "task": assignment.task,
                "assignment": assignment.task,
                "expected_output": assignment.expected_output,
                "constraints": assignment.constraints,
                "detail": "\n".join(detail_parts),
                "depends_on": member_dependencies,
            }),
        )
    return updated_workers



def _initialize_worker_states(
    *,
    workers: list[ChatTeamMemberPayload],
    team_activities: list[ChatTeamActivity],
    session_id: str,
) -> list[str]:
    """标记初始 Worker 队列状态，并返回对应 Runtime Event。"""

    worker_ids = {worker.agent_id for worker in workers}
    events: list[str] = []
    for member in workers:
        activity = _find_team_activity(team_activities, member.agent_id)
        if not activity:
            continue
        waiting_for = [
            dep
            for dep in _member_dependency_ids(member, workers)
            if dep in worker_ids
        ]
        wait_detail = (
            f"等待依赖 Worker 完成：{', '.join(_member_names(waiting_for, workers))}"
            if waiting_for
            else ""
        )
        _replace_team_activity(
            team_activities,
            activity.model_copy(update={
                "status": "pending" if waiting_for else "running",
                "execution_mode": member.execution_mode,
                "wait_reason": wait_detail,
                "detail": wait_detail or activity.detail,
            }),
        )
        events.append(runtime_event_sse(
            "TEAM_WORKER_BLOCKED_BY_DEPENDENCY" if waiting_for else "TEAM_WORKER_QUEUED",
            session_id=session_id,
            scope="worker",
            agent_id=member.agent_id,
            agent_name=member.name,
            team_activity_id=activity.id,
            status="pending" if waiting_for else "running",
            reason="waiting_dependency" if waiting_for else "",
            payload={
                "executionMode": member.execution_mode,
                "dependsOn": waiting_for,
            },
        ))
    return events
