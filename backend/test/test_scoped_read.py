import asyncio
from pathlib import Path

import pytest

from src.tools.scoped_read import ScopedRead


def test_scoped_read_accepts_project_relative_paths(tmp_path: Path, monkeypatch):
    """Read 支持项目相对路径，避免 Plan Mode 调查阶段因相对路径失败。"""

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "contract.md").write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr("src.tools.scoped_paths.WORKSPACE_DIR", str(tmp_path))

    chunk = asyncio.run(ScopedRead()(file_path="docs/contract.md"))
    text = chunk.content[0].text

    assert chunk.state != "error"
    assert "hello" in text


def test_scoped_read_rejects_absolute_path_outside_scope(tmp_path: Path):
    """Read 不得通过绝对路径读取 scope 外文件。"""

    scope = tmp_path / "project"
    scope.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside filesystem scope"):
        asyncio.run(ScopedRead(scope_dir=str(scope))(file_path=str(outside)))


def test_scoped_read_rejects_symlink_escape(tmp_path: Path):
    """Read 解析真实路径后拒绝通过符号链接越界。"""

    scope = tmp_path / "project"
    scope.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (scope / "link.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="outside filesystem scope"):
        asyncio.run(ScopedRead(scope_dir=str(scope))(file_path="link.txt"))
