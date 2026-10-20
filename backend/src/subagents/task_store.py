"""Subagent attached task 的文件事实源。

单个 invocation 的定义快照、状态和最终结果保存在 `<session-root>/subagents/<invocation>`。
结果引用是 Session 相对路径，不依赖 Project root。
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.runtime_paths import session_root, sessions_root
from src.subagents.models import SubagentDefinition


TERMINAL_SUBAGENT_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
SUBAGENT_INVOCATION_ID_PATTERN = re.compile(r"^subagent-[0-9a-f]{32}$")


class SubagentTaskStoreError(RuntimeError):
    """Subagent 文件任务不存在或违反状态不变量。"""


def utc_now() -> str:
    """生成 UTC ISO 时间。"""

    return datetime.now(timezone.utc).isoformat()


class SubagentTaskStore:
    """管理一个 Session 下的 attached Subagent task directories。"""

    def __init__(self, *, session_id: str, project_root: Path | None = None) -> None:
        """檔案只放在 `<session-root>/subagents`。project_root 不再決定路徑。"""

        self.session_id = session_id
        self.project_root = Path(project_root).expanduser().resolve() if project_root else Path()
        self.session_root = session_root(session_id)
        self.root = self.session_root / "subagents"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(
        self,
        *,
        invocation_id: str,
        run_id: str,
        tool_call_id: str,
        parent_scope: str,
        parent_agent_id: str,
        parent_agent_name: str,
        definition: SubagentDefinition,
        task: str,
        expected_output: str,
        context: str,
        constraints: list[str],
        parent_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建不可变 task snapshot 和初始 queued 状态。"""

        task_dir = self._task_dir(invocation_id)
        with self._lock:
            task_dir.mkdir(parents=False, exist_ok=False)
            now = utc_now()
            snapshot = {
                "invocationId": invocation_id,
                "runId": run_id,
                "toolCallId": tool_call_id,
                "parent": {
                    "scope": parent_scope,
                    "agentId": parent_agent_id,
                    "agentName": parent_agent_name,
                },
                "definition": {
                    "name": definition.name,
                    "scope": definition.scope,
                    "path": definition.path,
                    "contentHash": definition.content_hash,
                    "model": definition.model,
                    "tools": definition.tools,
                    "disallowedTools": definition.disallowed_tools,
                    "permissionMode": definition.permission_mode,
                    "maxTurns": definition.max_turns,
                    "skills": definition.skills,
                },
                "task": task,
                "expectedOutput": expected_output,
                "context": context,
                "constraints": constraints,
                "parentSnapshot": parent_snapshot,
                "createdAt": now,
            }
            status = {
                "invocationId": invocation_id,
                "runId": run_id,
                "subagentName": definition.name,
                "taskPreview": _trim_text(task, 500),
                "status": "queued",
                "deliveryStatus": "none",
                "resultRef": self._result_ref(invocation_id),
                "resultPreview": "",
                "error": "",
                "durationMs": 0,
                "createdAt": now,
                "startedAt": "",
                "completedAt": "",
                "deliveredAt": "",
                "consumedAt": "",
                "updatedAt": now,
            }
            self._write_json(task_dir / "task.json", snapshot)
            self._write_text(task_dir / "result.md", "")
            self._write_json(task_dir / "status.json", status)
            (task_dir / "transcript.jsonl").touch()
            return status

    def status(self, invocation_id: str) -> dict[str, Any]:
        """读取任务当前状态。"""

        path = self._task_dir(invocation_id) / "status.json"
        if not path.is_file():
            raise SubagentTaskStoreError(f"Subagent task 不存在: {invocation_id}")
        return self._read_json(path)

    def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """列出当前 Session 中属于指定父 Run 的任务。"""

        tasks: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("subagent-*/status.json")):
            try:
                status = self._read_json(path)
            except (OSError, json.JSONDecodeError, SubagentTaskStoreError):
                continue
            if str(status.get("runId") or "") == run_id:
                tasks.append(status)
        return tasks

    def set_status(
        self,
        invocation_id: str,
        status: str,
        *,
        error: str = "",
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        """原子更新执行状态；终态必须在结果文件已经落盘后写入。"""

        with self._lock:
            current = self.status(invocation_id)
            now = utc_now()
            updated = {
                **current,
                "status": status,
                "error": error,
                "updatedAt": now,
            }
            if status == "running" and not current.get("startedAt"):
                updated["startedAt"] = now
            if status in TERMINAL_SUBAGENT_STATUSES:
                updated["completedAt"] = current.get("completedAt") or now
                if current.get("deliveryStatus") == "none":
                    updated["deliveryStatus"] = "pending"
            if duration_ms is not None:
                updated["durationMs"] = max(0, int(duration_ms))
            self._write_json(self._task_dir(invocation_id) / "status.json", updated)
            return updated

    def write_result(
        self,
        invocation_id: str,
        content: str,
        *,
        error: str = "",
    ) -> dict[str, Any]:
        """先原子写最终结果，再更新可展示摘要。"""

        with self._lock:
            normalized = content.strip()
            if not normalized and error:
                normalized = f"Subagent failed.\n\n{error.strip()}"
            result_path = self._task_dir(invocation_id) / "result.md"
            self._write_text(result_path, normalized + ("\n" if normalized else ""))
            from src.sessions.files import catalog_output

            catalog_output(
                session_id=self.session_id,
                run_id=invocation_id,
                path=result_path,
                kind="managed",
            )
            current = self.status(invocation_id)
            updated = {
                **current,
                "resultPreview": _trim_text(normalized, 1200),
                "error": error,
                "updatedAt": utc_now(),
            }
            self._write_json(self._task_dir(invocation_id) / "status.json", updated)
            return updated

    def read_result(self, invocation_id: str) -> tuple[str, dict[str, Any]]:
        """读取终态结果并把 delivery 从 pending 推进到 delivered。"""

        with self._lock:
            current = self.status(invocation_id)
            if str(current.get("status") or "") not in TERMINAL_SUBAGENT_STATUSES:
                raise SubagentTaskStoreError(f"Subagent task 尚未结束: {invocation_id}")
            content = (self._task_dir(invocation_id) / "result.md").read_text(encoding="utf-8")
            if not content.strip():
                raise SubagentTaskStoreError(f"Subagent result 为空: {invocation_id}")
            now = utc_now()
            updated = {
                **current,
                "deliveryStatus": "delivered",
                "deliveredAt": current.get("deliveredAt") or now,
                "updatedAt": now,
            }
            self._write_json(self._task_dir(invocation_id) / "status.json", updated)
            return content, updated

    def mark_consumed(self, invocation_ids: list[str]) -> list[dict[str, Any]]:
        """父 Agent收到结果后完成一次 acting，再确认这些结果已消费。"""

        consumed: list[dict[str, Any]] = []
        with self._lock:
            for invocation_id in invocation_ids:
                current = self.status(invocation_id)
                if current.get("deliveryStatus") not in {"delivered", "consumed"}:
                    continue
                now = utc_now()
                updated = {
                    **current,
                    "deliveryStatus": "consumed",
                    "consumedAt": current.get("consumedAt") or now,
                    "updatedAt": now,
                }
                self._write_json(self._task_dir(invocation_id) / "status.json", updated)
                consumed.append(updated)
        return consumed

    def parent_snapshot(self, invocation_id: str) -> dict[str, Any] | None:
        """讀取建立當下寫入的父 snapshot 子集，不重新解析 grant。"""

        snapshot = self._read_json(self._task_dir(invocation_id) / "task.json")
        value = snapshot.get("parentSnapshot")
        return value if isinstance(value, dict) else None

    def result_path(self, invocation_id: str) -> Path:
        """返回受控的 result.md 绝对路径。"""

        return self._task_dir(invocation_id) / "result.md"

    def append_transcript(
        self,
        invocation_id: str,
        *,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        """把 child 过程追加到 invocation 自己的 JSONL，不进入父 Agent context。"""

        with self._lock:
            path = self._task_dir(invocation_id) / "transcript.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "kind": kind,
                    "payload": payload,
                    "createdAt": utc_now(),
                }, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _result_ref(self, invocation_id: str) -> str:
        return f"subagents/{invocation_id}/result.md"

    def _task_dir(self, invocation_id: str) -> Path:
        if not SUBAGENT_INVOCATION_ID_PATTERN.fullmatch(str(invocation_id or "")):
            raise SubagentTaskStoreError(f"Subagent invocationId 非法: {invocation_id}")
        path = (self.root / invocation_id).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise SubagentTaskStoreError("Subagent task path 超出 Session root")
        return path

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise SubagentTaskStoreError(f"Subagent JSON 必须是对象: {path.name}")
        return value

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(value, encoding="utf-8")
        os.replace(temp, path)


def _trim_text(value: str, limit: int) -> str:
    normalized = str(value or "").strip()
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def reconcile_interrupted_file_tasks(project_roots: list[Path] | None = None) -> int:
    """收口 Session root 與歷史 Project 目錄裡尚未結束的檔案任務。"""

    reconciled = 0
    seen: set[Path] = set()
    candidates = [sessions_root()]
    for raw_root in project_roots or []:
        project_root = raw_root.expanduser().resolve()
        if project_root in seen or not project_root.is_dir():
            continue
        seen.add(project_root)
        candidates.append(project_root / ".agent-workstation" / "sessions")
    for base in candidates:
        if not base.is_dir():
            continue
        for status_path in base.glob("*/subagents/subagent-*/status.json"):
            if _interrupt_status_file(status_path):
                reconciled += 1
    return reconciled


def _interrupt_status_file(status_path: Path) -> bool:
    try:
        status = SubagentTaskStore._read_json(status_path)
    except (OSError, json.JSONDecodeError, SubagentTaskStoreError):
        return False
    if str(status.get("status") or "") not in {"queued", "running"}:
        return False
    invocation_id = str(status.get("invocationId") or "")
    if not SUBAGENT_INVOCATION_ID_PATTERN.fullmatch(invocation_id):
        return False
    message = "Backend restarted before the attached Subagent task reached a terminal result."
    task_dir = status_path.parent
    try:
        SubagentTaskStore._write_text(task_dir / "result.md", f"Subagent failed.\n\n{message}\n")
        now = utc_now()
        updated = {
            **status,
            "status": "interrupted",
            "error": message,
            "deliveryStatus": "pending",
            "resultPreview": message,
            "completedAt": status.get("completedAt") or now,
            "updatedAt": now,
        }
        SubagentTaskStore._write_json(status_path, updated)
        from src.storage.database import update_subagent_invocation_delivery

        update_subagent_invocation_delivery(
            invocation_id,
            delivery_status="pending",
            result_ref=str(updated.get("resultRef") or f"subagents/{invocation_id}/result.md"),
        )
    except (OSError, SubagentTaskStoreError):
        return False
    return True
