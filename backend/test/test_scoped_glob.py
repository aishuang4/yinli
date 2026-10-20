import asyncio
from pathlib import Path

from src.tools.scoped_glob import ScopedGlob


def test_scoped_glob_skips_heavy_project_directories(tmp_path: Path):
    """Glob 默认跳过常见重目录，避免宽泛模式拖慢 Chat 流。"""

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "contract.md").write_text("# contract")
    for directory in ["node_modules", ".git", "data", "sessions"]:
        target = tmp_path / directory
        target.mkdir()
        (target / "ignored.md").write_text("# ignored")

    chunk = asyncio.run(ScopedGlob(scope_dir=str(tmp_path))(
        pattern="**/*.md",
        path=str(tmp_path),
    ))
    text = chunk.content[0].text

    assert "docs/contract.md" in text
    assert "node_modules/ignored.md" not in text
    assert ".git/ignored.md" not in text
    assert "data/ignored.md" not in text
    assert "sessions/ignored.md" not in text


def test_scoped_glob_accepts_project_relative_base_path(tmp_path: Path):
    """Glob 的 path 相对路径按项目根解析。"""

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "contract.md").write_text("# contract")

    chunk = asyncio.run(ScopedGlob(base_dir=str(tmp_path), scope_dir=str(tmp_path))(
        pattern="*.md",
        path="docs",
    ))

    assert chunk.state != "error"
    assert "contract.md" in chunk.content[0].text


def test_scoped_glob_falls_back_from_session_workspace_to_project_scope(tmp_path: Path):
    """Glob 的显式 Project 相对目录应在 Session cwd 缺失时回退。"""

    project_root = tmp_path / "project"
    session_root = project_root / ".agent-workstation" / "sessions" / "session-a"
    backend = project_root / "backend"
    session_root.mkdir(parents=True)
    backend.mkdir()
    (backend / "service.py").write_text("pass\n", encoding="utf-8")

    chunk = asyncio.run(ScopedGlob(
        base_dir=str(session_root),
        scope_dir=str(project_root),
    )(pattern="*.py", path="backend"))

    assert chunk.state != "error"
    assert "service.py" in chunk.content[0].text
