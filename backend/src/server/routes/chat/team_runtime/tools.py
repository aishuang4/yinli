"""Team Runtime 领域工具。

工具只操作新 TeamStore，不暴露旧 Team 字段或事件。
"""

import json
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.server.routes.chat.team_runtime.store import MAX_TEAM_MEMBERS, MAX_TEAM_TASKS, TeamStore, TeamStoreError


class TeamTool(ToolBase):
    """自动允许的 Team 文件状态工具。"""

    is_mcp = False
    is_read_only = False
    is_concurrency_safe = False
    is_external_tool = False
    is_state_injected = False

    def __init__(self, store: TeamStore, actor_id: str) -> None:
        self.store = store
        self.actor_id = actor_id

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Team domain state operation.",
            decision_reason="Team Runtime internal tool",
        )

    def success(self, payload: dict[str, Any]) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )

    def error(self, error: Exception | str) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps({"error": str(error)}, ensure_ascii=False))],
            state=ToolResultState.ERROR,
            is_last=True,
        )


class SpawnMember(TeamTool):
    """Leader 动态创建本轮临时成员。"""

    name = "team_spawn_member"
    description = (
        f"Spawn one temporary isolated member for the current TeamRun. "
        f"At most {MAX_TEAM_MEMBERS} members; extra calls return an error. "
        "You may spawn multiple members in one turn."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "role": {"type": "string"},
            "runtimePrompt": {"type": "string", "description": "Dynamic prompt for this TeamRun only."},
            "sourceAgentId": {"type": "string"},
        },
        "required": ["name", "role", "runtimePrompt"],
    }

    async def __call__(
        self,
        name: str,
        role: str,
        runtimePrompt: str,
        sourceAgentId: str = "",
    ) -> ToolChunk:
        try:
            return self.success({"member": self.store.spawn_member(
                name=name,
                role=role,
                runtime_prompt=runtimePrompt,
                source_agent_id=sourceAgentId,
            )})
        except TeamStoreError as exc:
            return self.error(exc)


class ListMembers(TeamTool):
    name = "team_list_members"
    description = "List current temporary TeamRun members."
    input_schema = {"type": "object", "properties": {}, "required": []}
    is_read_only = True

    async def __call__(self) -> ToolChunk:
        return self.success({"members": self.store.members()})


class CreateTask(TeamTool):
    """Leader 创建共享任务。"""

    name = "team_create_task"
    description = (
        f"Create a shared task. Dependencies must use exact task IDs returned earlier in this TeamRun. "
        f"At most {MAX_TEAM_TASKS} tasks. Prefer one task per requested role; "
        "after the last required task, call team_finish_coordination in the same turn."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "brief": {"type": "string"},
            "assignedMemberId": {"type": "string"},
            "dependsOn": {"type": "array", "items": {"type": "string"}},
            "priority": {"type": "integer"},
        },
        "required": ["title", "brief"],
    }

    async def __call__(
        self,
        title: str,
        brief: str,
        assignedMemberId: str = "",
        dependsOn: list[str] | None = None,
        priority: int = 0,
    ) -> ToolChunk:
        try:
            return self.success({"task": self.store.create_task(
                title=title,
                brief=brief,
                created_by=self.actor_id,
                assigned_member_id=assignedMemberId,
                depends_on=dependsOn,
                priority=priority,
            )})
        except TeamStoreError as exc:
            return self.error(exc)


class ReadTasks(TeamTool):
    name = "team_read_tasks"
    description = "Read the complete shared Task List."
    input_schema = {
        "type": "object",
        "properties": {"includeContent": {"type": "boolean"}},
        "required": [],
    }
    is_read_only = True

    async def __call__(self, includeContent: bool = False) -> ToolChunk:
        tasks = self.store.tasks()
        if includeContent:
            tasks = [{
                **task,
                "brief": self.store.task_brief(str(task["id"])),
                "result": self.store.task_result(str(task["id"])),
            } for task in tasks]
        return self.success({"tasks": tasks})


class SendMail(TeamTool):
    name = "team_send_mail"
    description = "Send a persisted mailbox message to one TeamRun member, or use to=broadcast for all other members."
    input_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "content": {"type": "string"},
            "kind": {"type": "string"},
            "subject": {"type": "string"},
            "taskId": {"type": "string"},
        },
        "required": ["to", "content"],
    }

    async def __call__(
        self,
        to: str,
        content: str,
        kind: str = "message",
        subject: str = "",
        taskId: str = "",
    ) -> ToolChunk:
        try:
            if to in {"broadcast", "*"}:
                return self.success({"messages": self.store.broadcast_mail(
                    sender=self.actor_id,
                    content=content,
                    kind=kind,
                    subject=subject,
                    task_id=taskId,
                )})
            return self.success({"message": self.store.send_mail(
                sender=self.actor_id,
                recipient=to,
                content=content,
                kind=kind,
                subject=subject,
                task_id=taskId,
            )})
        except TeamStoreError as exc:
            return self.error(exc)


class ReadMailbox(TeamTool):
    name = "team_read_mailbox"
    description = "Read mailbox messages and acknowledge all inbound messages for the current member."
    input_schema = {"type": "object", "properties": {}, "required": []}
    is_read_only = False

    async def __call__(self) -> ToolChunk:
        return self.success({"messages": self.store.read_inbox(self.actor_id)})


class FinishCoordination(TeamTool):
    """Leader 显式结束建队和任务拆分阶段。"""

    name = "team_finish_coordination"
    description = (
        "Finish coordination after at least one temporary member and one task exist. "
        "Call this immediately after the last required task is created; do not keep spawning or polling."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}

    async def __call__(self) -> ToolChunk:
        members = [member for member in self.store.members() if member.get("id") != "leader"]
        tasks = self.store.tasks()
        if not members:
            return self.error("必须至少创建一个 Member")
        if not tasks:
            return self.error("必须至少创建一个共享任务")
        return self.success({"run": self.store.set_phase("executing")})


class SubmitResult(TeamTool):
    """Member 提交当前任务的唯一成功出口。"""

    name = "team_submit_result"
    description = (
        "Persist the complete non-empty result for the current owned task. "
        "Unread mailbox messages block submission, so call team_read_mailbox immediately before this tool."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "taskId": {"type": "string"},
            "result": {"type": "string"},
        },
        "required": ["taskId", "result"],
    }

    async def __call__(self, taskId: str, result: str) -> ToolChunk:
        try:
            return self.success({"task": self.store.complete_task(taskId, self.actor_id, result)})
        except TeamStoreError as exc:
            return self.error(exc)


def leader_tools(store: TeamStore) -> list[ToolBase]:
    """Leader 协调工具集。"""

    return [
        SpawnMember(store, "leader"),
        ListMembers(store, "leader"),
        CreateTask(store, "leader"),
        ReadTasks(store, "leader"),
        SendMail(store, "leader"),
        ReadMailbox(store, "leader"),
        FinishCoordination(store, "leader"),
    ]


def member_tools(store: TeamStore, member_id: str) -> list[ToolBase]:
    """Member 协作工具集。"""

    return [
        ReadTasks(store, member_id),
        ReadMailbox(store, member_id),
        SendMail(store, member_id),
        SubmitResult(store, member_id),
    ]
