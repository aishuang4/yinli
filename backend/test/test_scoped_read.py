import asyncio
from pathlib import Path

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
