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

    chunk = asyncio.run(ScopedGlob()(pattern="**/*.md", path=str(tmp_path)))
    text = chunk.content[0].text

    assert "docs/contract.md" in text
    assert "node_modules/ignored.md" not in text
    assert ".git/ignored.md" not in text
    assert "data/ignored.md" not in text
    assert "sessions/ignored.md" not in text
