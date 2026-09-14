"""extra_scopes 允许 catalog 根，拒绝 USER_DATA_ROOT 本身。"""

from pathlib import Path

import pytest

from src.config import runtime_paths
from src.tools.scoped_paths import resolve_workspace_path


def test_extra_scope_allows_absolute_catalog_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    catalog = tmp_path / "workspaces" / "default" / ".agents"
    catalog.mkdir(parents=True)
    project.mkdir()
    target = catalog / "profiles"
    target.mkdir()
    file = target / "a.md"
    file.write_text("ok", encoding="utf-8")
    resolved = resolve_workspace_path(
        str(file),
        base_dir=str(project),
        scope_dir=str(project),
        extra_scopes=[str(catalog)],
    )
    assert Path(resolved) == file.resolve()


def test_rejects_user_data_root_and_models_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_paths, "USER_DATA_ROOT", tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    models = tmp_path / "models.json"
    models.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Forbidden extra"):
        resolve_workspace_path(
            str(models),
            base_dir=str(project),
            scope_dir=str(project),
            extra_scopes=[str(tmp_path)],
        )
    with pytest.raises(ValueError, match="outside filesystem scope"):
        resolve_workspace_path(
            str(models),
            base_dir=str(project),
            scope_dir=str(project),
            extra_scopes=[],
        )


def test_symlink_pointing_outside_extra_scope_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    catalog = tmp_path / "workspaces" / "default" / ".agents"
    outside = tmp_path / "secret.md"
    catalog.mkdir(parents=True)
    project.mkdir()
    outside.write_text("nope", encoding="utf-8")
    link = catalog / "leak.md"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="outside filesystem scope"):
        resolve_workspace_path(
            str(link),
            base_dir=str(project),
            scope_dir=str(project),
            extra_scopes=[str(catalog)],
        )
