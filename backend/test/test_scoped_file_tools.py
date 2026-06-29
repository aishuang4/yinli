import asyncio
from pathlib import Path

from src.tools.scoped_edit import ScopedEdit
from src.tools.scoped_grep import ScopedGrep
from src.tools.scoped_write import ScopedWrite


def test_scoped_write_accepts_project_relative_paths(tmp_path: Path, monkeypatch):
    """Write 支持项目相对路径并写入项目根内文件。"""

    monkeypatch.setattr("src.tools.scoped_paths.WORKSPACE_DIR", str(tmp_path))

    chunk = asyncio.run(ScopedWrite()(file_path="docs/out.txt", content="hello\n"))

    assert chunk.state != "error"
    assert (tmp_path / "docs" / "out.txt").read_text(encoding="utf-8") == "hello\n"


def test_scoped_edit_accepts_project_relative_paths(tmp_path: Path, monkeypatch):
    """Edit 支持项目相对路径并修改项目根内文件。"""

    docs = tmp_path / "docs"
    docs.mkdir()
    target = docs / "out.txt"
    target.write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr("src.tools.scoped_paths.WORKSPACE_DIR", str(tmp_path))

    chunk = asyncio.run(ScopedEdit()(
        file_path="docs/out.txt",
        old_string="hello",
        new_string="hi",
    ))

    assert chunk.state != "error"
    assert target.read_text(encoding="utf-8") == "hi\n"


def test_scoped_grep_accepts_project_relative_paths(tmp_path: Path, monkeypatch):
    """Grep 支持项目相对 path，不依赖进程 cwd。"""

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "out.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr("src.tools.scoped_paths.WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr("src.tools.scoped_grep.WORKSPACE_DIR", str(tmp_path))

    chunk = asyncio.run(ScopedGrep()(pattern="needle", path="docs"))

    assert chunk.state != "error"
    assert "out.txt" in chunk.content[0].text
