"""Run snapshot 的凍結、恢復與脫敏摘要。"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from src.sessions.candidates import select_run_candidates
from src.sessions.errors import SessionError
from src.sessions.records import freeze_run_snapshot, get_run_snapshot, list_allowed_roots

_CURRENT: ContextVar[dict[str, Any] | None] = ContextVar("yinli_run_snapshot", default=None)
_SECRET_KEYS = {"secret", "token", "apikey", "api_key", "authorization", "content", "body", "markdown"}


def begin_run(
    session_id: str,
    run_id: str,
    *,
    agent_id: str,
    permission_mode: str = "manual",
    model_snapshot: dict[str, Any] | None = None,
    approval_snapshot: dict[str, Any] | None = None,
    start_model,
    fail_before_commit=None,
) -> dict[str, Any]:
    """先凍結 snapshot，成功後才允許啟動模型。"""

    from src.runtime.session_context import build_session_context

    ctx = build_session_context(session_id, agent_id=agent_id, run_id=run_id)
    from src.sessions.legacy_gate import require_migrated_session

    require_migrated_session(session_id)
    assert_new_run_resources(session_id, agent_id=agent_id, permission_mode=permission_mode)
    if fail_before_commit is not None:
        fail_before_commit()
    resources = select_run_candidates(session_id, agent_id=agent_id, permission_mode=permission_mode)
    snapshot = freeze_run_snapshot(
        run_id=run_id,
        session_id=session_id,
        working_directory=str(ctx.working_directory or ""),
        allowed_roots=list_allowed_roots(session_id),
        resources={"items": resources},
        model_snapshot=model_snapshot or {},
        approval_snapshot=approval_snapshot or {"mode": permission_mode},
        agent_snapshot={"agentId": agent_id},
    )
    if get_run_snapshot(run_id) is None:
        raise SessionError("snapshot_required", "沒有 snapshot 不能啟動模型")
    bind_run_snapshot(run_id)
    return start_model(snapshot)


def assert_new_run_resources(
    session_id: str,
    *,
    agent_id: str | None = None,
    permission_mode: str = "manual",
) -> None:
    """新 Run 在模型呼叫前確認 pin 仍存在且啟用。既有 snapshot 不在這裡改寫。"""

    from src.sessions.records import list_resource_grants
    from src.sessions.resources import _lookup

    for grant in list_resource_grants(session_id):
        if not grant["enabled"]:
            continue
        override = grant.get("configOverride") or {}
        if not bool(override.get("pinned")):
            continue
        record = _lookup(str(grant["resourceType"]), str(grant["resourceId"]))
        if record is None or not record["enabled"]:
            raise SessionError("resource_disabled", "全局資源已停用，新 Run 不能啟動")
    select_run_candidates(session_id, agent_id=agent_id, permission_mode=permission_mode)


def bind_run_snapshot(run_id: str) -> dict[str, Any] | None:
    snapshot = get_run_snapshot(run_id) if run_id else None
    _CURRENT.set(snapshot)
    return snapshot


def frozen_resources_for_run(run_id: str) -> dict[str, Any] | None:
    """恢復、HITL、壓縮與快取重建都讀這一份，不重新解析 grant。"""

    return get_run_snapshot(run_id)


def current_snapshot_audit() -> dict[str, Any]:
    snapshot = _CURRENT.get()
    if snapshot is None:
        return {}
    return snapshot_audit_summary(snapshot)


def snapshot_audit_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    """給 Trace 與 UI 的摘要。不含 secret 與正文。"""

    resources = snapshot.get("resources") or {}
    items = resources.get("items") if isinstance(resources, dict) else []
    return {
        "runId": snapshot.get("runId") or "",
        "snapshotId": snapshot.get("id") or "",
        "agentId": (snapshot.get("agentSnapshot") or {}).get("agentId") or "",
        "directoryGrantVersion": snapshot.get("directoryGrantVersion"),
        "resourceGrantVersion": snapshot.get("resourceGrantVersion"),
        "workingDirectory": snapshot.get("workingDirectory") or "",
        "skills": [item["resourceId"] for item in items or [] if item.get("resourceType") == "skill"],
        "tools": [
            item["resourceId"]
            for item in items or []
            if item.get("resourceType") in {"mcp", "http_tool", "knowledge"}
        ],
    }
