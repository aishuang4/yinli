"""SessionContext 只能經公開建構入口建立。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.runtime.session_context import build_global_context, build_session_context
from src.sessions.errors import SessionError
from src.sessions.records import add_allowed_root, create_session
from src.sessions.service import archive_session
from src.tenancy.context import TenantContext, use_tenant

BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
BUILDER = BACKEND_SRC / "runtime" / "session_context.py"


def test_session_context_has_one_constructor() -> None:
    offenders: list[str] = []
    for path in BACKEND_SRC.rglob("*.py"):
        if path.resolve() == BUILDER.resolve() or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
                "SessionContext",
                "GlobalContext",
            }:
                offenders.append(f"{path.relative_to(BACKEND_SRC)}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and node.module == "src.server.routes.workspaces":
                if path.relative_to(BACKEND_SRC).as_posix().startswith(("runtime/session_context.py", "sessions/")):
                    offenders.append(f"{path.relative_to(BACKEND_SRC)} imports workspace routes")
    assert offenders == []
    text = BUILDER.read_text(encoding="utf-8")
    assert "src.server.routes.workspaces" not in text
    assert "def build_session_context" in text
    assert "def build_global_context" in text


def test_build_session_context_rejects_missing_archived_and_bad_directories(tmp_path) -> None:
    with pytest.raises(SessionError) as missing:
        build_session_context("missing-session")
    assert missing.value.code == "session_not_found"

    archived = create_session(session_id="sesctxarch")
    archive_session(archived["id"])
    with pytest.raises(SessionError) as archived_error:
        build_session_context(archived["id"])
    assert archived_error.value.code == "session_archived"

    absent = tmp_path / "missing-dir"
    session = create_session(session_id="sesctxdir", working_directory=str(absent))
    with pytest.raises(SessionError) as unavailable:
        build_session_context(session["id"])
    assert unavailable.value.code == "directory_unavailable"

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    escaped = create_session(session_id="sesctxlink", working_directory=str(linked))
    # 目錄存在且連結目標仍是目錄時可以解析；把連結改成越出父目錄的檔案連結才拒絕。
    file_link = tmp_path / "file-link"
    target = tmp_path / "note.txt"
    target.write_text("x", encoding="utf-8")
    file_link.symlink_to(target)
    from src.storage.database import _connect, _lock

    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE chat_sessions SET working_directory = ? WHERE id = ?",
            (str(file_link), escaped["id"]),
        )
        conn.commit()
    with pytest.raises(SessionError) as not_dir:
        build_session_context(escaped["id"])
    assert not_dir.value.code == "directory_unavailable"


def test_build_session_context_uses_record_and_rejects_other_tenant(tmp_path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    session = create_session(session_id="sesctxok", working_directory=str(work), agent_id="agent-1")
    add_allowed_root(session["id"], str(extra), access_mode="read", source="user")
    context = build_session_context(session["id"], run_id="run-1")
    assert context.session_id == session["id"]
    assert context.working_directory == work.resolve()
    assert context.allowed_roots == (extra.resolve(),)
    assert context.agent_id == "agent-1"
    assert context.run_id == "run-1"
    assert context.session_root.name == session["id"]
    global_context = build_global_context()
    assert global_context.tenant_id == context.tenant_id

    other = TenantContext.hosted("usr_" + "cd" * 16)
    owner = TenantContext.hosted("usr_" + "ef" * 16)
    with use_tenant(other):
        with pytest.raises(SessionError) as mismatch:
            build_session_context(session["id"])
        assert mismatch.value.code == "session_not_found"
        foreign = other.data_root / "projects"
        foreign.mkdir(parents=True, exist_ok=True)
    with use_tenant(owner):
        foreign_session = create_session(session_id="sesctxforeign", working_directory=str(foreign))
        with pytest.raises(SessionError) as foreign_dir:
            build_session_context(foreign_session["id"])
        assert foreign_dir.value.code == "tenant_mismatch"
