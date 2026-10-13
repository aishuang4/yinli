"""catalog 路径约定。"""

from pathlib import Path

import pytest

from src.catalog import paths as catalog_paths


def test_workspace_catalog_root_uses_plural_workspaces(tmp_path) -> None:
    from src.config import runtime_paths

    root = catalog_paths.workspace_catalog_root("default")
    assert root == runtime_paths.tenant_root() / "workspaces" / "default" / ".agents"
    assert catalog_paths.profiles_dir("default") == root / "profiles"
    assert catalog_paths.jobs_dir("default") == root / "jobs"
    assert catalog_paths.project_jobs_dir(tmp_path) == tmp_path / ".agents" / "jobs"


def test_system_overlay_is_not_a_workspace_id() -> None:
    with pytest.raises(ValueError):
        catalog_paths.workspace_catalog_root("_system")
    assert catalog_paths.system_profiles_dir().name == "profiles"


def test_rejects_illegal_ids() -> None:
    with pytest.raises(ValueError):
        catalog_paths.validate_agent_id("bad id")
    with pytest.raises(ValueError):
        catalog_paths.validate_workspace_id("..")
    assert catalog_paths.validate_agent_id("a" * 64)
    with pytest.raises(ValueError):
        catalog_paths.validate_agent_id("a" * 65)


def test_atomic_write_replaces(tmp_path: Path) -> None:
    target = tmp_path / "file.md"
    catalog_paths.atomic_write_text(target, "one\n")
    catalog_paths.atomic_write_text(target, "two\n")
    assert target.read_text() == "two\n"
    assert not list(tmp_path.glob(".*.tmp"))
