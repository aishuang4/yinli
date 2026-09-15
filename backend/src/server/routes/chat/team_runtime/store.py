"""Team Runtime 文件事实源。

本模块定义全新的 TeamRun 文件格式和状态不变量，不读取旧 Team 目录。
"""

import json
import hashlib
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.runtime_paths import TEAM_RUNTIME_ROOT

TERMINAL_TASK_STATUSES = {"succeeded", "failed", "cancelled"}
# 一次 TeamRun 的机械上限：Leader 拆得再细也不能无限开成员 / 开任务，超出在工具层直接拒绝。
MAX_TEAM_MEMBERS = 6
MAX_TEAM_TASKS = 12
# 单个任务失败后自动重派的次数（同一任务换新 session 再跑一次）。
MAX_TASK_RETRIES = 1
# team_submit_result 的正文上限：更长的内容应写文件，result 只留路径 + 摘要，否则综合阶段撞窗口。
MAX_TASK_RESULT_CHARS = 6000
TEAM_RUN_ID_PATTERN = re.compile(r"^trun_[0-9a-f]{32}$")
MEMBER_ID_PATTERN = re.compile(r"^(leader|member_[0-9a-f]{12})$")
TASK_ID_PATTERN = re.compile(r"^task_[0-9a-f]{12}$")
ATTEMPT_ID_PATTERN = re.compile(r"^attempt_[0-9a-f]{12}$")
MAIL_ID_PATTERN = re.compile(r"^mail_[0-9a-f]{12}$")


class TeamStoreError(RuntimeError):
    """Team 文件状态违反运行不变量。"""


class TeamMailboxChangedError(TeamStoreError):
    """综合期间 Mailbox 版本发生变化。"""


@dataclass(frozen=True)
class TeamRunRef:
    """TeamRun 文件引用。"""

    id: str
    path: Path


def utc_now() -> str:
    """生成 UTC ISO 时间。"""

    return datetime.now(timezone.utc).isoformat()


class TeamStore:
    """单个 TeamRun 的原子文件存储。"""

    def __init__(self, ref: TeamRunRef) -> None:
        self.ref = ref
        self._lock = threading.RLock()

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        chat_run_id: str,
        goal: str,
        leader_agent_id: str,
        leader_name: str,
        workspace_id: str = "default",
        project_id: str = "default",
        root: Path | None = None,
    ) -> "TeamStore":
        """创建全新的 TeamRun。"""

        team_run_id = f"trun_{uuid.uuid4().hex}"
        path = (root or TEAM_RUNTIME_ROOT) / team_run_id
        for directory in (
            path / "members",
            path / "tasks",
            path / "mailbox" / "deliveries",
        ):
            directory.mkdir(parents=True, exist_ok=False)

        store = cls(TeamRunRef(id=team_run_id, path=path))
        now = utc_now()
        run = {
            "id": team_run_id,
            "sessionId": session_id,
            "chatRunId": chat_run_id,
            "workspaceId": workspace_id,
            "projectId": project_id,
            "goal": goal,
            "status": "running",
            "phase": "created",
            "leaderId": "leader",
            "eventSeq": 0,
            "maxConcurrency": 3,
            "createdAt": now,
            "updatedAt": now,
            "completedAt": "",
            "failureReason": "",
        }
        leader = {
            "id": "leader",
            "teamRunId": team_run_id,
            "sourceAgentId": leader_agent_id,
            "name": leader_name,
            "role": "leader",
            "status": "created",
            "sessionKey": f"{team_run_id}:leader",
            "runtimePrompt": "",
            "activeTaskId": "",
            "createdAt": now,
            "updatedAt": now,
        }
        store._write_json(path / "run.json", run)
        store._write_json(path / "members" / "leader.json", leader)
        (path / "mailbox" / "messages.jsonl").touch()
        (path / "events.jsonl").touch()
        store._record(
            "run.created",
            actor_id="leader",
            payload={
                "run": store._public_run(run),
                "leader": store._public_member(leader),
            },
        )
        store.write_snapshot()
        return store

    @classmethod
    def load(cls, team_run_id: str, *, root: Path | None = None) -> "TeamStore":
        """只从新运行时目录加载 TeamRun。"""

        if not TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
            raise TeamStoreError(f"TeamRun ID 非法: {team_run_id}")
        path = (root or TEAM_RUNTIME_ROOT) / team_run_id
        if not (path / "run.json").exists():
            raise TeamStoreError(f"TeamRun 不存在: {team_run_id}")
        return cls(TeamRunRef(id=team_run_id, path=path))

    def run(self) -> dict[str, Any]:
        return self._read_json(self.ref.path / "run.json")

    def members(self) -> list[dict[str, Any]]:
        return [self._read_json(path) for path in sorted((self.ref.path / "members").glob("*.json"))]

    def tasks(self) -> list[dict[str, Any]]:
        return [self._read_json(path) for path in sorted((self.ref.path / "tasks").glob("*/task.json"))]

    def mailbox(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.ref.path / "mailbox" / "messages.jsonl")

    def events_after(self, seq: int) -> list[dict[str, Any]]:
        return [event for event in self._read_jsonl(self.ref.path / "events.jsonl") if int(event.get("seq") or 0) > seq]

    def snapshot(self) -> dict[str, Any]:
        run = self.run()
        tasks = self.tasks()
        members = self.members()
        mailbox = self.mailbox()
        return {
            "teamRunId": self.ref.id,
            "run": run,
            "members": [
                {key: value for key, value in member.items() if key != "runtimePrompt"}
                for member in members
            ],
            "tasks": [
                {
                    **task,
                    "blocked": self.is_blocked(task),
                    "attemptCount": len(self.attempts(str(task["id"]))),
                    "briefPreview": self.task_brief(str(task["id"]))[:2_000],
                    "resultPreview": self.task_result(str(task["id"]))[:4_000],
                }
                for task in tasks
            ],
            "mailbox": mailbox[-100:],
            "counts": {
                "members": len(members),
                "tasks": len(tasks),
                "queued": sum(task.get("status") == "queued" for task in tasks),
                "running": sum(task.get("status") == "running" for task in tasks),
                "succeeded": sum(task.get("status") == "succeeded" for task in tasks),
                "failed": sum(task.get("status") == "failed" for task in tasks),
                "mail": len(mailbox),
            },
            "updatedAt": run.get("updatedAt") or utc_now(),
        }

    def write_snapshot(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        self._write_json(self.ref.path / "snapshot.json", snapshot)
        return snapshot

    def set_phase(self, phase: str) -> dict[str, Any]:
        with self._lock:
            run = {**self.run(), "phase": phase, "updatedAt": utc_now()}
            self._write_json(self.ref.path / "run.json", run)
            self._record(
                "run.phase.changed",
                status=phase,
                payload={"run": self._public_run(run)},
            )
            self.write_snapshot()
            return run

    def complete_run(self, *, expected_mailbox_version: int | None = None) -> dict[str, Any]:
        """TeamRun 完成。允许存在已彻底失败的任务（部分综合），但不允许还有未结束的任务。"""

        with self._lock:
            if expected_mailbox_version is not None and len(self.mailbox()) != expected_mailbox_version:
                raise TeamMailboxChangedError("Mailbox 在综合期间发生变化")
            tasks = self.tasks()
            unfinished = [task for task in tasks if task.get("status") not in TERMINAL_TASK_STATUSES]
            if unfinished:
                raise TeamStoreError("仍有任务未结束，TeamRun 不能完成")
            succeeded = [task for task in tasks if task.get("status") == "succeeded"]
            if tasks and not succeeded:
                raise TeamStoreError("没有任何成功任务，TeamRun 不能完成")
            failed = [task for task in tasks if task.get("status") != "succeeded"]
            now = utc_now()
            run = {
                **self.run(),
                "status": "completed",
                "phase": "completed",
                "partial": bool(failed),
                "failedTaskCount": len(failed),
                "completedAt": now,
                "updatedAt": now,
            }
            self._write_json(self.ref.path / "run.json", run)
            self._record(
                "run.completed",
                status="completed",
                payload={"run": self._public_run(run)},
            )
            self.write_snapshot()
            return run

    def fail_run(self, reason: str) -> dict[str, Any]:
        with self._lock:
            for task in self.tasks():
                if task.get("status") not in TERMINAL_TASK_STATUSES:
                    self.cancel_task(
                        str(task["id"]),
                        str(task.get("ownerMemberId") or "leader"),
                        reason,
                    )
            for member in self.members():
                if member.get("status") in {"failed", "stopped"}:
                    continue
                status = "failed" if member.get("status") == "running" else "stopped"
                self.set_member_status(str(member["id"]), status)
            now = utc_now()
            run = {
                **self.run(),
                "status": "failed",
                "phase": "failed",
                "failureReason": reason.strip() or "TeamRun failed",
                "completedAt": now,
                "updatedAt": now,
            }
            self._write_json(self.ref.path / "run.json", run)
            self._record(
                "run.failed",
                status="failed",
                payload={"run": self._public_run(run), "message": run["failureReason"]},
            )
            self.write_snapshot()
            return run

    def cancel_run(self, reason: str = "Chat run cancelled") -> dict[str, Any]:
        """级联取消未结束任务和成员，并持久化 TeamRun 终态。"""

        with self._lock:
            for task in self.tasks():
                if task.get("status") not in TERMINAL_TASK_STATUSES:
                    self.cancel_task(
                        str(task["id"]),
                        str(task.get("ownerMemberId") or "leader"),
                        reason,
                    )
            for member in self.members():
                if member.get("status") not in {"failed", "stopped"}:
                    self.set_member_status(str(member["id"]), "stopped")
            now = utc_now()
            run = {
                **self.run(),
                "status": "cancelled",
                "phase": "cancelled",
                "failureReason": reason.strip() or "Chat run cancelled",
                "completedAt": now,
                "updatedAt": now,
            }
            self._write_json(self.ref.path / "run.json", run)
            self._record(
                "run.cancelled",
                status="cancelled",
                payload={"run": self._public_run(run), "message": run["failureReason"]},
            )
            self.write_snapshot()
            return run

    def spawn_member(
        self,
        *,
        name: str,
        role: str,
        runtime_prompt: str,
        source_agent_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if not runtime_prompt.strip():
                raise TeamStoreError("Member runtime prompt 不能为空")
            existing = [member for member in self.members() if member.get("role") != "leader"]
            if len(existing) >= MAX_TEAM_MEMBERS:
                raise TeamStoreError(
                    f"本轮最多创建 {MAX_TEAM_MEMBERS} 个临时成员，已达上限；请复用已有成员承接任务"
                )
            member_id = f"member_{uuid.uuid4().hex[:12]}"
            now = utc_now()
            member = {
                "id": member_id,
                "teamRunId": self.ref.id,
                "sourceAgentId": source_agent_id,
                "name": name.strip() or member_id,
                "role": role.strip() or "member",
                "status": "idle",
                "sessionKey": f"{self.ref.id}:{member_id}",
                "runtimePrompt": runtime_prompt.strip(),
                "activeTaskId": "",
                "activeAttemptId": "",
                "lastTaskId": "",
                "lastAttemptId": "",
                "attemptCount": 0,
                "createdAt": now,
                "updatedAt": now,
            }
            self._write_json(self._member_path(member_id), member)
            self._record(
                "member.spawned",
                actor_id=member_id,
                status="idle",
                payload={"member": self._public_member(member)},
            )
            self.write_snapshot()
            return member

    def set_member_status(
        self,
        member_id: str,
        status: str,
        *,
        active_task_id: str = "",
        active_attempt_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            member = self.member(member_id)
            previous_task_id = str(member.get("activeTaskId") or "")
            previous_attempt_id = str(member.get("activeAttemptId") or "")
            member = {
                **member,
                "status": status,
                "activeTaskId": active_task_id,
                "activeAttemptId": active_attempt_id,
                "lastTaskId": previous_task_id or str(member.get("lastTaskId") or ""),
                "lastAttemptId": previous_attempt_id or str(member.get("lastAttemptId") or ""),
                "updatedAt": utc_now(),
            }
            self._write_json(self._member_path(member_id), member)
            self._record(
                "member.status.changed",
                actor_id=member_id,
                task_id=active_task_id,
                status=status,
                payload={"member": self._public_member(member)},
            )
            self.write_snapshot()
            return member

    def member(self, member_id: str) -> dict[str, Any]:
        path = self._member_path(member_id)
        if not path.exists():
            raise TeamStoreError(f"Member 不存在: {member_id}")
        return self._read_json(path)

    def create_task(
        self,
        *,
        title: str,
        brief: str,
        created_by: str,
        assigned_member_id: str = "",
        depends_on: list[str] | None = None,
        priority: int = 0,
        origin: str = "leader",
        origin_mail_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if created_by != "user":
                self.member(created_by)
            if assigned_member_id:
                member = self.member(assigned_member_id)
                if member.get("role") == "leader":
                    raise TeamStoreError("Leader 只负责协调，不能承接任务")
            dependencies = list(dict.fromkeys(depends_on or []))
            existing_tasks = self.tasks()
            if len(existing_tasks) >= MAX_TEAM_TASKS:
                raise TeamStoreError(
                    f"本轮最多创建 {MAX_TEAM_TASKS} 个任务，已达上限；请合并任务或调用 team_finish_coordination"
                )
            known_task_ids = {str(task.get("id") or "") for task in existing_tasks}
            unknown = [task_id for task_id in dependencies if task_id not in known_task_ids]
            if unknown:
                raise TeamStoreError(f"任务依赖不存在: {', '.join(unknown)}")
            # 依赖只能指向已存在的任务，新任务不可能被旧任务依赖，因此这里天然无环；
            # 但仍检查依赖链里是否已经存在环（防止外部写入的 task.json 被篡改）。
            if _dependency_cycle(existing_tasks, dependencies):
                raise TeamStoreError("任务依赖形成环，无法创建")

            task_id = f"task_{uuid.uuid4().hex[:12]}"
            task_path = self.ref.path / "tasks" / task_id
            task_path.mkdir(parents=True, exist_ok=False)
            now = utc_now()
            task = {
                "id": task_id,
                "teamRunId": self.ref.id,
                "title": title.strip() or task_id,
                "status": "queued",
                "assignedMemberId": assigned_member_id,
                "ownerMemberId": "",
                "currentAttemptId": "",
                "acceptedAttemptId": "",
                "attemptCount": 0,
                "reviewCount": 0,
                "lastReviewReason": "",
                "origin": origin,
                "originMailId": origin_mail_id,
                "dependsOn": dependencies,
                "priority": int(priority),
                "createdBy": created_by,
                "claimedAt": "",
                "completedAt": "",
                "failureReason": "",
                "retryCount": 0,
                "retryable": True,
                "createdAt": now,
                "updatedAt": now,
            }
            self._write_json(task_path / "task.json", task)
            (task_path / "brief.md").write_text(brief, encoding="utf-8")
            (task_path / "result.md").write_text("", encoding="utf-8")
            self._record(
                "task.created",
                actor_id=created_by,
                task_id=task_id,
                status="queued",
                payload={"task": self._public_task(task, brief_preview=brief)},
            )
            if assigned_member_id and not self.is_blocked(task):
                self.send_mail(
                    sender=created_by,
                    recipient=assigned_member_id,
                    kind="task_assignment",
                    subject=task["title"],
                    content=brief,
                    task_id=task_id,
                )
            self.write_snapshot()
            return task

    def request_task_rework(
        self,
        *,
        task_id: str,
        member_id: str,
        reason: str,
        origin_mail_id: str = "",
    ) -> dict[str, Any]:
        """撤销已验收 Task 并重新排队，保留历史 Attempt。"""

        with self._lock:
            task = self.task(task_id)
            if task.get("status") != "succeeded":
                raise TeamStoreError("只有已完成任务可以发起继续执行")
            assigned = str(task.get("assignedMemberId") or task.get("ownerMemberId") or "")
            if assigned and assigned != member_id:
                raise TeamStoreError("只能让原任务成员继续执行")
            self.member(member_id)
            now = utc_now()
            rework = {
                **task,
                "status": "queued",
                "assignedMemberId": member_id,
                "ownerMemberId": "",
                "currentAttemptId": "",
                "acceptedAttemptId": "",
                "completedAt": "",
                "failureReason": "",
                "lastReviewReason": reason.strip() or "用户要求继续执行",
                "origin": "user_mail",
                "originMailId": origin_mail_id,
                "updatedAt": now,
            }
            self._write_json(self._task_path(task_id) / "task.json", rework)
            self._record(
                "task.rework.requested",
                actor_id="user",
                task_id=task_id,
                status="queued",
                payload={"task": self._public_task(rework), "message": rework["lastReviewReason"]},
            )
            self.write_snapshot()
            return rework

    def task(self, task_id: str) -> dict[str, Any]:
        path = self._task_path(task_id) / "task.json"
        if not path.exists():
            raise TeamStoreError(f"任务不存在: {task_id}")
        return self._read_json(path)

    def task_brief(self, task_id: str) -> str:
        return (self._task_path(task_id) / "brief.md").read_text(encoding="utf-8")

    def task_result(self, task_id: str) -> str:
        return (self._task_path(task_id) / "result.md").read_text(encoding="utf-8")

    def is_blocked(self, task: dict[str, Any]) -> bool:
        if task.get("status") != "queued":
            return False
        return any(self.task(task_id).get("status") != "succeeded" for task_id in task.get("dependsOn") or [])

    def claimable_tasks(self) -> list[dict[str, Any]]:
        tasks = [task for task in self.tasks() if task.get("status") == "queued" and not self.is_blocked(task)]
        return sorted(tasks, key=lambda task: (-int(task.get("priority") or 0), str(task.get("createdAt") or "")))

    def claim_task(self, task_id: str, member_id: str) -> dict[str, Any]:
        with self._lock:
            member = self.member(member_id)
            if member.get("status") != "idle" or member.get("activeTaskId"):
                raise TeamStoreError(f"Member 当前不可领取任务: {member_id}")
            if any(
                task.get("status") == "running" and task.get("ownerMemberId") == member_id
                for task in self.tasks()
            ):
                raise TeamStoreError(f"Member 已有进行中任务: {member_id}")

            task_path = self._task_path(task_id)
            lock_path = task_path / "claim.lock"
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as exc:
                raise TeamStoreError(f"任务已被领取: {task_id}") from exc
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump({"memberId": member_id, "createdAt": utc_now()}, file, ensure_ascii=False)
                task = self.task(task_id)
                if task.get("status") != "queued" or self.is_blocked(task):
                    raise TeamStoreError(f"任务当前不可领取: {task_id}")
                assigned = str(task.get("assignedMemberId") or "")
                if assigned and assigned != member_id:
                    raise TeamStoreError(f"任务已分配给其他 Member: {assigned}")
                now = utc_now()
                task = {
                    **task,
                    "status": "running",
                    "ownerMemberId": member_id,
                    "currentAttemptId": f"attempt_{uuid.uuid4().hex[:12]}",
                    "claimedAt": now,
                    "updatedAt": now,
                }
                self._write_json(task_path / "task.json", task)
                attempt_id = str(task["currentAttemptId"])
                attempt_path = task_path / "attempts" / attempt_id
                attempt_path.mkdir(parents=True, exist_ok=False)
                attempt = {
                    "id": attempt_id,
                    "teamRunId": self.ref.id,
                    "taskId": task_id,
                    "memberId": member_id,
                    "ordinal": int(task.get("attemptCount") or 0) + 1,
                    "status": "running",
                    "trigger": "initial" if not task.get("attemptCount") else "retry",
                    "triggerMailId": str(task.get("originMailId") or ""),
                    "instruction": "",
                    "resultPath": "result.md",
                    "resultHash": "",
                    "startedAt": now,
                    "submittedAt": "",
                    "reviewedAt": "",
                    "failureReason": "",
                }
                self._write_json(attempt_path / "attempt.json", attempt)
                (attempt_path / "instruction.md").write_text("", encoding="utf-8")
                (attempt_path / "result.md").write_text("", encoding="utf-8")
                task["attemptCount"] = int(task.get("attemptCount") or 0) + 1
                self._write_json(task_path / "task.json", task)
                member = self.member(member_id)
                running_member = {
                    **member,
                    "status": "running",
                    "activeTaskId": task_id,
                    "activeAttemptId": attempt_id,
                    "attemptCount": int(member.get("attemptCount") or 0) + 1,
                    "updatedAt": now,
                }
                self._write_json(self._member_path(member_id), running_member)
                self._record(
                    "attempt.created",
                    actor_id=member_id,
                    task_id=task_id,
                    status="created",
                    payload={"attempt": self._public_attempt(attempt)},
                )
                self._record(
                    "attempt.started",
                    actor_id=member_id,
                    task_id=task_id,
                    status="running",
                    payload={"attempt": self._public_attempt(attempt)},
                )
                self._record(
                    "member.status.changed",
                    actor_id=member_id,
                    task_id=task_id,
                    status="running",
                    payload={"member": self._public_member(running_member)},
                )
                self._record(
                    "task.claimed",
                    actor_id=member_id,
                    task_id=task_id,
                    status="running",
                    payload={"task": self._public_task(task)},
                )
                self.write_snapshot()
                return task
            except Exception:
                lock_path.unlink(missing_ok=True)
                raise

    def complete_task(self, task_id: str, member_id: str, result: str) -> dict[str, Any]:
        with self._lock:
            normalized = result.strip()
            if not normalized:
                raise TeamStoreError("任务结果不能为空")
            if len(normalized) > MAX_TASK_RESULT_CHARS:
                raise TeamStoreError(
                    f"任务结果超过 {MAX_TASK_RESULT_CHARS} 字（当前 {len(normalized)}）。"
                    "请把完整内容写入工作目录文件，result 只保留文件路径和不超过上限的摘要"
                )
            task = self.task(task_id)
            if task.get("status") != "running" or task.get("ownerMemberId") != member_id:
                raise TeamStoreError("只能由当前 owner 完成 running 任务")
            unread = self.unread_inbox(member_id)
            if unread:
                raise TeamStoreError("Mailbox 存在未读消息，请先调用 team_read_mailbox 再提交结果")
            now = utc_now()
            attempt_id = str(task.get("currentAttemptId") or "")
            attempt = self.attempt(attempt_id) if attempt_id else None
            if attempt is None:
                raise TeamStoreError("当前任务缺少 active Attempt")
            attempt_path = self._attempt_path(task_id, attempt_id)
            (attempt_path / "result.md").write_text(normalized, encoding="utf-8")
            (self._task_path(task_id) / "result.md").write_text(normalized, encoding="utf-8")
            submitted = {
                **attempt,
                "status": "submitted",
                "submittedAt": now,
                "resultHash": f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}",
            }
            self._write_json(attempt_path / "attempt.json", submitted)
            self._record(
                "attempt.submitted",
                actor_id=member_id,
                task_id=task_id,
                status="submitted",
                payload={"attempt": self._public_attempt(submitted)},
            )
            accepted = {**submitted, "status": "accepted", "reviewedAt": now}
            self._write_json(attempt_path / "attempt.json", accepted)
            self._record(
                "attempt.accepted",
                actor_id="leader",
                task_id=task_id,
                status="accepted",
                payload={"attempt": self._public_attempt(accepted)},
            )
            task = {
                **task,
                "status": "succeeded",
                "acceptedAttemptId": attempt_id,
                "currentAttemptId": "",
                "completedAt": now,
                "updatedAt": now,
            }
            self._write_json(self._task_path(task_id) / "task.json", task)
            (self._task_path(task_id) / "claim.lock").unlink(missing_ok=True)
            self._record(
                "task.completed",
                actor_id=member_id,
                task_id=task_id,
                status="succeeded",
                payload={
                    "task": self._public_task(task, result_preview=normalized),
                    "resultPreview": normalized[:1000],
                },
            )
            self.send_mail(
                sender=member_id,
                recipient="leader",
                kind="result",
                subject=task["title"],
                content=normalized[:4000],
                task_id=task_id,
            )
            self._emit_unlocked(task_id)
            self.write_snapshot()
            return task

    def fail_task(self, task_id: str, member_id: str, reason: str, *, retryable: bool = True) -> dict[str, Any]:
        """任务失败。`retryable=False` 表示不再自动重派（例如依赖任务已彻底失败）。"""

        with self._lock:
            task = self.task(task_id)
            if task.get("status") not in {"queued", "running"}:
                return task
            now = utc_now()
            attempt_id = str(task.get("currentAttemptId") or "")
            if attempt_id:
                attempt_path = self._attempt_path(task_id, attempt_id)
                attempt = self.attempt(attempt_id)
                attempt = {**attempt, "status": "failed", "failureReason": reason.strip() or "Task failed"}
                self._write_json(attempt_path / "attempt.json", attempt)
                self._record(
                    "attempt.failed",
                    actor_id=member_id,
                    task_id=task_id,
                    status="failed",
                    payload={"attempt": self._public_attempt(attempt), "message": attempt["failureReason"]},
                )
            task = {
                **task,
                "status": "failed",
                "ownerMemberId": member_id or str(task.get("ownerMemberId") or ""),
                "currentAttemptId": "",
                "failureReason": reason.strip() or "Task failed",
                "retryable": bool(task.get("retryable", True)) and retryable,
                "completedAt": now,
                "updatedAt": now,
            }
            self._write_json(self._task_path(task_id) / "task.json", task)
            (self._task_path(task_id) / "claim.lock").unlink(missing_ok=True)
            self._record(
                "task.failed",
                actor_id=member_id,
                task_id=task_id,
                status="failed",
                payload={
                    "task": self._public_task(task),
                    "message": task["failureReason"],
                },
            )
            self.write_snapshot()
            return task

    def can_retry_task(self, task: dict[str, Any]) -> bool:
        return (
            task.get("status") == "failed"
            and bool(task.get("retryable", True))
            and int(task.get("retryCount") or 0) < MAX_TASK_RETRIES
        )

    def retry_failed_task(self, task_id: str) -> dict[str, Any]:
        """把失败任务重新排队自动重派：保留历史 Attempt，把失败原因带给下一次尝试。"""

        with self._lock:
            task = self.task(task_id)
            if not self.can_retry_task(task):
                raise TeamStoreError("任务不可重试")
            now = utc_now()
            failed_member = str(task.get("ownerMemberId") or "")
            requeued = {
                **task,
                "status": "queued",
                "ownerMemberId": "",
                "currentAttemptId": "",
                "completedAt": "",
                "retryCount": int(task.get("retryCount") or 0) + 1,
                "lastReviewReason": f"上一次尝试失败：{task.get('failureReason') or '未知原因'}",
                "updatedAt": now,
            }
            self._write_json(self._task_path(task_id) / "task.json", requeued)
            # 失败成员回到 idle 才能被重新分派；它的下一次 session 是全新实例。
            if failed_member and failed_member != "leader":
                member = self.member(failed_member)
                if member.get("status") == "failed":
                    self.set_member_status(failed_member, "idle")
            self._record(
                "task.retry.scheduled",
                actor_id="leader",
                task_id=task_id,
                status="queued",
                payload={
                    "task": self._public_task(requeued),
                    "message": requeued["lastReviewReason"],
                    "retryCount": requeued["retryCount"],
                },
            )
            self.write_snapshot()
            return requeued

    def cancel_task(self, task_id: str, member_id: str, reason: str) -> dict[str, Any]:
        """把尚未结束的任务落为 cancelled。"""

        with self._lock:
            task = self.task(task_id)
            if task.get("status") in TERMINAL_TASK_STATUSES:
                return task
            now = utc_now()
            attempt_id = str(task.get("currentAttemptId") or "")
            if attempt_id:
                attempt_path = self._attempt_path(task_id, attempt_id)
                attempt = self.attempt(attempt_id)
                attempt = {**attempt, "status": "cancelled", "failureReason": reason.strip() or "Task cancelled"}
                self._write_json(attempt_path / "attempt.json", attempt)
                self._record(
                    "attempt.cancelled",
                    actor_id=member_id,
                    task_id=task_id,
                    status="cancelled",
                    payload={"attempt": self._public_attempt(attempt), "message": attempt["failureReason"]},
                )
            task = {
                **task,
                "status": "cancelled",
                "ownerMemberId": member_id if member_id != "leader" else str(task.get("ownerMemberId") or ""),
                "currentAttemptId": "",
                "failureReason": reason.strip() or "Task cancelled",
                "completedAt": now,
                "updatedAt": now,
            }
            self._write_json(self._task_path(task_id) / "task.json", task)
            (self._task_path(task_id) / "claim.lock").unlink(missing_ok=True)
            self._record(
                "task.cancelled",
                actor_id=member_id,
                task_id=task_id,
                status="cancelled",
                payload={
                    "task": self._public_task(task),
                    "message": task["failureReason"],
                },
            )
            self.write_snapshot()
            return task

    def send_mail(
        self,
        *,
        sender: str,
        recipient: str,
        content: str,
        kind: str = "message",
        subject: str = "",
        task_id: str = "",
        dispatch_mode: str = "context",
    ) -> dict[str, Any]:
        with self._lock:
            normalized = content.strip()
            if not normalized:
                raise TeamStoreError("Mailbox 消息不能为空")
            if sender == "user" and self.run().get("status") != "running":
                raise TeamStoreError("TeamRun 当前不可接收用户消息")
            if sender != "user":
                self.member(sender)
            if recipient != "user":
                self.member(recipient)
            now = utc_now()
            message = {
                "id": f"mail_{uuid.uuid4().hex[:12]}",
                "teamRunId": self.ref.id,
                "from": sender,
                "to": recipient,
                "kind": kind,
                "subject": subject,
                "content": normalized,
                "taskId": task_id,
                "dispatchMode": dispatch_mode,
                "createdAt": now,
            }
            self._append_jsonl(self.ref.path / "mailbox" / "messages.jsonl", message)
            self._write_json(self.ref.path / "mailbox" / "deliveries" / f"{message['id']}.json", {
                "messageId": message["id"],
                "recipient": recipient,
                "status": "delivered",
                "deliveredAt": now,
                "readAt": "",
                "actedAt": "",
            })
            self._record(
                "mail.sent",
                actor_id=sender,
                task_id=task_id,
                status="delivered",
                payload={"message": self._public_mail(message)},
            )
            self.write_snapshot()
            return message

    def broadcast_mail(
        self,
        *,
        sender: str,
        content: str,
        kind: str = "message",
        subject: str = "",
        task_id: str = "",
        dispatch_mode: str = "context",
    ) -> list[dict[str, Any]]:
        """把广播展开为每个目标成员一条独立持久化消息。"""

        recipients = [
            str(member["id"])
            for member in self.members()
            if sender == "user" or str(member["id"]) != sender
        ]
        if not recipients:
            raise TeamStoreError("广播没有可用收件人")
        return [
            self.send_mail(
                sender=sender,
                recipient=recipient,
                content=content,
                kind=kind,
                subject=subject,
                task_id=task_id,
                dispatch_mode=dispatch_mode,
            )
            for recipient in recipients
        ]

    def inbox(self, member_id: str) -> list[dict[str, Any]]:
        self.member(member_id)
        return [mail for mail in self.mailbox() if mail.get("to") == member_id or mail.get("from") == member_id]

    def unread_inbox(self, member_id: str) -> list[dict[str, Any]]:
        """返回尚未被成员 session 读取的入站消息。"""

        with self._lock:
            self.member(member_id)
            unread: list[dict[str, Any]] = []
            for mail in self.mailbox():
                if mail.get("to") != member_id:
                    continue
                delivery_path = self._delivery_path(str(mail["id"]))
                delivery = self._read_json(delivery_path)
                if not delivery.get("readAt"):
                    unread.append(mail)
            return unread

    def read_inbox(self, member_id: str) -> list[dict[str, Any]]:
        """读取成员会话并原子写入入站消息的 read receipt。"""

        with self._lock:
            messages = self.inbox(member_id)
            now = utc_now()
            read_ids: list[str] = []
            for mail in messages:
                if mail.get("to") != member_id:
                    continue
                delivery_path = self._delivery_path(str(mail["id"]))
                delivery = self._read_json(delivery_path)
                if delivery.get("readAt"):
                    continue
                self._write_json(delivery_path, {
                    **delivery,
                    "status": "read",
                    "readAt": now,
                })
                read_ids.append(str(mail["id"]))
            if read_ids:
                self._record(
                    "mail.read",
                    actor_id=member_id,
                    status="read",
                    payload={"messageIds": read_ids},
                )
                self.write_snapshot()
            return messages

    def record_member_event(
        self,
        event_type: str,
        *,
        member_id: str,
        task_id: str = "",
        status: str = "running",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            event = self._record(
                event_type,
                actor_id=member_id,
                task_id=task_id,
                status=status,
                payload=payload,
            )
            self.write_snapshot()
            return event

    def _emit_unlocked(self, completed_task_id: str) -> None:
        for task in self.tasks():
            if task.get("status") != "queued" or completed_task_id not in (task.get("dependsOn") or []):
                continue
            if self.is_blocked(task):
                continue
            self._record(
                "task.unlocked",
                actor_id="leader",
                task_id=str(task["id"]),
                status="queued",
                payload={
                    "task": self._public_task(task),
                    "unlockedByTaskId": completed_task_id,
                },
            )
            assigned = str(task.get("assignedMemberId") or "")
            if assigned:
                self.send_mail(
                    sender="leader",
                    recipient=assigned,
                    kind="task_assignment",
                    subject=str(task.get("title") or task["id"]),
                    content=self.task_brief(str(task["id"])),
                    task_id=str(task["id"]),
                )

    def _record(
        self,
        event_type: str,
        *,
        actor_id: str = "",
        task_id: str = "",
        status: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self.run()
        seq = int(run.get("eventSeq") or 0) + 1
        run = {**run, "eventSeq": seq, "updatedAt": utc_now()}
        self._write_json(self.ref.path / "run.json", run)
        event = {
            "id": f"tevt_{uuid.uuid4().hex}",
            "seq": seq,
            "teamRunId": self.ref.id,
            "chatRunId": str(run.get("chatRunId") or ""),
            "type": event_type,
            "actorId": actor_id,
            "taskId": task_id,
            "status": status,
            "phase": str(run.get("phase") or ""),
            "timestamp": utc_now(),
            "dataVersion": 1,
            "counts": self._event_counts(),
            "updatedAt": run["updatedAt"],
            "payload": payload or {},
        }
        self._append_jsonl(self.ref.path / "events.jsonl", event)
        return event

    def _event_counts(self) -> dict[str, int]:
        """生成适合实时投影的小型 Team 计数摘要。"""

        members = self.members()
        tasks = self.tasks()
        return {
            "members": len(members),
            "tasks": len(tasks),
            "queued": sum(task.get("status") == "queued" for task in tasks),
            "running": sum(task.get("status") == "running" for task in tasks),
            "succeeded": sum(task.get("status") == "succeeded" for task in tasks),
            "failed": sum(task.get("status") == "failed" for task in tasks),
            "cancelled": sum(task.get("status") == "cancelled" for task in tasks),
            "mail": len(self.mailbox()),
        }

    @staticmethod
    def _public_run(run: dict[str, Any]) -> dict[str, Any]:
        """裁剪 TeamRun 展示字段，避免把完整用户目标重复写入事件。"""

        return {
            key: run.get(key)
            for key in (
                "id",
                "chatRunId",
                "projectId",
                "status",
                "phase",
                "leaderId",
                "maxConcurrency",
                "createdAt",
                "updatedAt",
                "completedAt",
                "failureReason",
                "partial",
                "failedTaskCount",
            )
        }

    @staticmethod
    def _public_member(member: dict[str, Any]) -> dict[str, Any]:
        """裁剪 Member 展示字段，禁止 runtimePrompt 进入 Chat Snapshot。"""

        return {
            key: member.get(key)
            for key in (
                "id",
                "sourceAgentId",
                "name",
                "role",
                "status",
                "activeTaskId",
                "activeAttemptId",
                "lastTaskId",
                "lastAttemptId",
                "attemptCount",
                "createdAt",
                "updatedAt",
            )
        }

    @staticmethod
    def _public_task(
        task: dict[str, Any],
        *,
        brief_preview: str = "",
        result_preview: str = "",
    ) -> dict[str, Any]:
        """裁剪 Task 展示字段，并限制 Markdown 预览大小。"""

        result = {
            key: task.get(key)
            for key in (
                "id",
                "title",
                "status",
                "assignedMemberId",
                "ownerMemberId",
                "dependsOn",
                "priority",
                "createdBy",
                "claimedAt",
                "completedAt",
                "failureReason",
                "currentAttemptId",
                "acceptedAttemptId",
                "attemptCount",
                "reviewCount",
                "lastReviewReason",
                "retryCount",
                "retryable",
                "origin",
                "originMailId",
                "createdAt",
                "updatedAt",
            )
        }
        if brief_preview:
            result["briefPreview"] = brief_preview[:2_000]
        if result_preview:
            result["resultPreview"] = result_preview[:4_000]
        return result

    def attempts(self, task_id: str) -> list[dict[str, Any]]:
        """读取 Task 的所有 Attempt，旧 Task 没有目录时返回空列表。"""

        attempts_path = self._task_path(task_id) / "attempts"
        if not attempts_path.exists():
            return []
        return [self._read_json(path) for path in sorted(attempts_path.glob("*/attempt.json"))]

    def attempt(self, attempt_id: str) -> dict[str, Any] | None:
        """按 Attempt ID 读取记录；不存在时返回 None。"""

        if not ATTEMPT_ID_PATTERN.fullmatch(attempt_id):
            raise TeamStoreError(f"Attempt ID 非法: {attempt_id}")
        for path in self.ref.path.joinpath("tasks").glob(f"*/attempts/{attempt_id}/attempt.json"):
            return self._read_json(path)
        return None

    @staticmethod
    def _public_mail(message: dict[str, Any]) -> dict[str, Any]:
        """裁剪 Mailbox 展示字段，完整正文只保留在 Team 文件事实源。"""

        return {
            "id": message.get("id"),
            "teamRunId": message.get("teamRunId"),
            "from": message.get("from"),
            "to": message.get("to"),
            "kind": message.get("kind"),
            "subject": message.get("subject"),
            "content": str(message.get("content") or "")[:4_000],
            "taskId": message.get("taskId"),
            "dispatchMode": message.get("dispatchMode") or "context",
            "createdAt": message.get("createdAt"),
        }

    def _member_path(self, member_id: str) -> Path:
        if not MEMBER_ID_PATTERN.fullmatch(member_id):
            raise TeamStoreError(f"Member ID 非法: {member_id}")
        return self.ref.path / "members" / f"{member_id}.json"

    def _task_path(self, task_id: str) -> Path:
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise TeamStoreError(f"Task ID 非法: {task_id}")
        return self.ref.path / "tasks" / task_id

    def _attempt_path(self, task_id: str, attempt_id: str) -> Path:
        if not ATTEMPT_ID_PATTERN.fullmatch(attempt_id):
            raise TeamStoreError(f"Attempt ID 非法: {attempt_id}")
        return self._task_path(task_id) / "attempts" / attempt_id

    @staticmethod
    def _public_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
        """裁剪 Attempt 展示字段，完整结果保留在 Attempt 文件中。"""

        return {
            key: attempt.get(key)
            for key in (
                "id",
                "teamRunId",
                "taskId",
                "memberId",
                "ordinal",
                "status",
                "trigger",
                "triggerMailId",
                "resultHash",
                "startedAt",
                "submittedAt",
                "reviewedAt",
                "failureReason",
            )
        }

    def _delivery_path(self, message_id: str) -> Path:
        if not MAIL_ID_PATTERN.fullmatch(message_id):
            raise TeamStoreError(f"Mail ID 非法: {message_id}")
        return self.ref.path / "mailbox" / "deliveries" / f"{message_id}.json"

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TeamEventPublisher:
    """按 seq 转发持久化 Team event，不构造展示快照。"""

    def __init__(self, store: TeamStore) -> None:
        self.store = store
        self.cursor = 0

    def mark_existing_seen(self) -> None:
        events = self.store.events_after(0)
        self.cursor = max((int(event.get("seq") or 0) for event in events), default=0)

    def drain(self) -> list[dict[str, Any]]:
        events = self.store.events_after(self.cursor)
        if events:
            self.cursor = max(int(event.get("seq") or 0) for event in events)
        return events


def _dependency_cycle(tasks: list[dict[str, Any]], new_dependencies: list[str]) -> bool:
    """检查已有任务图加上"新任务依赖 new_dependencies"后是否存在环。"""

    graph = {str(task.get("id") or ""): [str(dep) for dep in (task.get("dependsOn") or [])] for task in tasks}
    graph["__new__"] = list(new_dependencies)
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        mark = state.get(node, 0)
        if mark == 1:
            return True
        if mark == 2:
            return False
        state[node] = 1
        for dep in graph.get(node, []):
            if visit(dep):
                return True
        state[node] = 2
        return False

    return any(visit(node) for node in list(graph))
