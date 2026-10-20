"""Session 檔案、Artifact、Trace、時間線、通知與刪除恢復。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from src.config.runtime_paths import session_artifacts_root
from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.skill_evolution import (
    SkillEvolutionGateDecision,
    SkillEvolutionRecordInput,
    WorkflowEvidence,
    persist_skill_evolution_record,
)
from src.memory.timeline import search_timeline
from src.notifications.inbox import notification_click_target
from src.project_files import (
    delete_project_session_files,
    resolve_project_session_artifact,
)
from src.sessions.deletion_files import read_manifest
from src.sessions.file_paths import classify_reference
from src.sessions.files import catalog_output, list_session_artifacts
from src.sessions.records import create_session, get_session
from src.sessions.service import archive_session, hard_delete_session
from src.storage import database
from src.tenancy.context import TenantContext, use_tenant


def test_session_paths_cover_legacy_escape_symlink_external_and_windows(tmp_path: Path) -> None:
    session_id = "session-paths"
    artifact = session_artifacts_root(session_id) / "reports" / "note.docx"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"doc")
    legacy = resolve_project_session_artifact(
        project_root=tmp_path,
        session_id=session_id,
        relative_path=".agent-workstation/sessions/session-paths/reports/note.docx",
    )
    windows = resolve_project_session_artifact(
        project_root=tmp_path,
        session_id=session_id,
        relative_path="artifacts\\reports\\note.docx",
    )
    assert legacy == artifact
    assert windows == artifact
    with pytest.raises(HTTPException):
        resolve_project_session_artifact(
            project_root=tmp_path,
            session_id=session_id,
            relative_path="..\\..\\secret.docx",
        )
    with pytest.raises(HTTPException):
        resolve_project_session_artifact(
            project_root=tmp_path,
            session_id=session_id,
            relative_path="C:\\Windows\\note.docx",
        )
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    link = session_artifacts_root(session_id) / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(HTTPException):
        resolve_project_session_artifact(
            project_root=tmp_path,
            session_id=session_id,
            relative_path="artifacts/link.txt",
        )
    external = classify_reference(session_id, str(outside), working_directory=str(tmp_path))
    assert external["kind"] == "external"
    assert delete_project_session_files(project_root=tmp_path, session_id=session_id) >= 1
    assert outside.read_text(encoding="utf-8") == "keep"
    assert not artifact.exists()


def test_shared_external_directory_does_not_mix_artifact_catalog(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    left = shared / "left.txt"
    right = shared / "right.txt"
    left.write_text("left", encoding="utf-8")
    right.write_text("right", encoding="utf-8")
    catalog_output(session_id="session-left", run_id="run-left", path=left, kind="external")
    catalog_output(session_id="session-right", run_id="run-right", path=right, kind="external")
    left_paths = {item["path"] for item in list_session_artifacts("session-left")}
    right_paths = {item["path"] for item in list_session_artifacts("session-right")}
    assert str(left) in left_paths
    assert str(right) not in left_paths
    assert str(right) in right_paths
    assert str(left) not in right_paths


def test_timeline_stays_in_session_and_habits_stay_global(tmp_path: Path) -> None:
    create_session(session_id="timeline-a", agent_id="agent-1")
    create_session(session_id="timeline-b", agent_id="agent-1")
    with database._lock, database._connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_messages (
                id, session_id, role, content, images, skill_calls, created_at, sort_order
            ) VALUES ('msg-a', 'timeline-a', 'user', '只屬於 A', '[]', '[]', 't', 0)
            """
        )
        conn.commit()
    found = search_timeline(session_id="timeline-a", surface="chat", query_text="只屬於")
    assert [item["sessionId"] for item in found["messages"]] == ["timeline-a"]
    hidden = search_timeline(session_id="timeline-b", surface="chat", query_text="只屬於")
    assert hidden["messages"] == []
    habits = MarkdownMemoryStore(tmp_path)._habits_path("user-1")
    professional = MarkdownMemoryStore(tmp_path)._professional_path("agent-1")
    assert "timeline-a" not in habits.parts
    assert "timeline-a" not in professional.parts
    record = persist_skill_evolution_record(SkillEvolutionRecordInput(
        session_id="timeline-a",
        source_run_id="run-a",
        agent_id="agent-1",
        router_gate=SkillEvolutionGateDecision(shouldEvaluate=False, reason="skip"),
        workflow_evidence=WorkflowEvidence(),
    ))
    with database._lock, database._connect() as conn:
        stored = conn.execute(
            "SELECT session_id, source_run_id FROM skill_evolution_records WHERE id = ?",
            (record["id"],),
        ).fetchone()
    assert stored["session_id"] == "timeline-a"
    assert stored["source_run_id"] == "run-a"

    tenant_a = TenantContext.hosted("usr_" + "a7" * 16)
    tenant_b = TenantContext.hosted("usr_" + "b7" * 16)
    with use_tenant(tenant_a):
        create_session(session_id="tenant-session", agent_id="agent-1")
        with database._lock, database._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (
                    id, session_id, role, content, images, skill_calls, created_at, sort_order
                ) VALUES ('msg-tenant', 'tenant-session', 'user', '租戶原文', '[]', '[]', 't', 0)
                """
            )
            conn.commit()
    with use_tenant(tenant_b):
        assert search_timeline(session_id="tenant-session", surface="chat")["messages"] == []


def test_notification_click_covers_deleted_and_archived_sessions() -> None:
    create_session(session_id="note-active", agent_id="agent-1")
    create_session(session_id="note-archived", agent_id="agent-1")
    archive_session("note-archived")
    active = database.insert_user_notification({
        "kind": "job_completed",
        "title": "完成",
        "owner_session_id": "note-active",
        "job_id": "job-1",
        "firing_id": "fire-1",
    })
    archived = database.insert_user_notification({
        "kind": "job_completed",
        "title": "歸檔",
        "owner_session_id": "note-archived",
    })
    missing = database.insert_user_notification({
        "kind": "job_completed",
        "title": "已刪",
        "owner_session_id": "note-missing",
    })
    assert notification_click_target(active)["action"] == "openSession"
    assert notification_click_target(archived) == {
        "action": "openSession",
        "sessionId": "note-archived",
        "status": "archived",
    }
    assert notification_click_target(missing)["reason"] == "deleted"


def test_failed_session_delete_restores_rows_and_keeps_external_directory(tmp_path: Path) -> None:
    external = tmp_path / "work"
    external.mkdir()
    (external / "keep.txt").write_text("user", encoding="utf-8")
    session = create_session(session_id="ses-restore", working_directory=str(external))
    internal = session_artifacts_root(session["id"])
    internal.mkdir(parents=True)
    (internal / "managed.txt").write_text("managed", encoding="utf-8")
    catalog_output(session_id=session["id"], run_id="run-1", path=internal / "managed.txt", kind="managed")
    with pytest.raises(OSError, match="injected"):
        hard_delete_session(session["id"], fail_after="files")
    assert get_session(session["id"]) is not None
    assert (internal / "managed.txt").read_text(encoding="utf-8") == "managed"
    assert (external / "keep.txt").read_text(encoding="utf-8") == "user"
    manifest = read_manifest(session["id"])
    assert manifest["workingDirectory"] == str(external)
    hard_delete_session(session["id"])
    assert get_session(session["id"]) is None
    assert not internal.exists()
    assert (external / "keep.txt").exists()
