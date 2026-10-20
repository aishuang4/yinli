"""Session 目錄授權與 Agent filesystem scope 的交集。"""

from pathlib import Path

import pytest

from src.sessions.errors import SessionError
from src.sessions.filesystem import AllowedDirectory, authorize_path, intersect_filesystem


def test_empty_scope_uses_only_session_grants(tmp_path, monkeypatch):
    granted = tmp_path / "granted"
    granted.mkdir()
    decoy = tmp_path / "decoy-project"
    decoy.mkdir()
    monkeypatch.setattr("src.config.runtime_paths.default_project_root", lambda: decoy)

    grant = intersect_filesystem("", [AllowedDirectory(granted, "read_write")])

    assert grant.write_roots == (granted.resolve(),)
    assert decoy.resolve() not in grant.read_roots
    assert Path.cwd().resolve() not in grant.read_roots


def test_disjoint_agent_scope_is_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    other = tmp_path / "other"
    allowed.mkdir()
    other.mkdir()
    with pytest.raises(SessionError) as exc:
        intersect_filesystem(str(other), [AllowedDirectory(allowed, "read_write")])
    assert exc.value.code == "filesystem_scope_disjoint"


def test_intersection_keeps_the_narrower_directory(tmp_path):
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    wide = intersect_filesystem(str(root), [AllowedDirectory(nested, "read_write")])
    narrow = intersect_filesystem(str(nested), [AllowedDirectory(root, "read")])
    assert wide.write_roots == (nested.resolve(),)
    assert narrow.read_roots == (nested.resolve(),)
    assert narrow.write_roots == ()


def test_readonly_grant_rejects_writes_and_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("no", encoding="utf-8")
    link = root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    grant = intersect_filesystem(str(root), [AllowedDirectory(root, "read")])
    with pytest.raises(SessionError) as readonly:
        authorize_path(root / "note.txt", grant, write=True)
    assert readonly.value.code == "filesystem_readonly"
    with pytest.raises(SessionError) as escaped:
        authorize_path(link / "secret.txt", grant, write=False)
    assert escaped.value.code == "filesystem_scope_denied"
    assert authorize_path(root, grant, write=False) == root.resolve()
