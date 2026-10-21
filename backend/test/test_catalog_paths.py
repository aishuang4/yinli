"""catalog 路径约定。"""

from pathlib import Path

import pytest

from src.catalog import paths as catalog_paths


def test_agent_catalog_lives_under_agents_root(tmp_path) -> None:
    from src.config import runtime_paths

    del tmp_path
    root = runtime_paths.agents_root()
    assert catalog_paths.profiles_dir() == root / "profiles"
    assert catalog_paths.jobs_dir() == root / "jobs"
    assert catalog_paths.system_overlay_root() == root / "_system"


def test_system_overlay_profiles_stay_under_agents() -> None:
    assert catalog_paths.system_profiles_dir() == catalog_paths.system_overlay_root() / "profiles"
    assert catalog_paths.system_profiles_dir().name == "profiles"


def test_rejects_illegal_ids() -> None:
    with pytest.raises(ValueError):
        catalog_paths.validate_agent_id("bad id")
    assert catalog_paths.validate_agent_id("a" * 64)
    with pytest.raises(ValueError):
        catalog_paths.validate_agent_id("a" * 65)


def test_atomic_write_replaces(tmp_path: Path) -> None:
    target = tmp_path / "file.md"
    catalog_paths.atomic_write_text(target, "one\n")
    catalog_paths.atomic_write_text(target, "two\n")
    assert target.read_text() == "two\n"
    assert not list(tmp_path.glob(".*.tmp"))
