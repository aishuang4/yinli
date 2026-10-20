"""catalog 扫描、冲突与系统 overlay。"""

from pathlib import Path

from src.catalog.loader import (
    get_registered_agent,
    invalidate_catalog,
    list_workspace_agents,
    save_agent,
)
from src.catalog.agent_models import AgentResponse
from src.catalog.paths import profile_path
from src.config.runtime_paths import global_agents_root
from src.catalog.system_defaults import utc_now


def _user_agent(agent_id: str, workspace_id: str, prompt: str = "p") -> AgentResponse:
    return AgentResponse.model_validate({
        "id": agent_id,
        "name": agent_id,
        "avatar": "",
        "description": "",
        "system_prompt": prompt,
        "modelConfig": {},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
        "tags": [],
        "guardrails": {},
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "workspaceId": workspace_id,
        "role": "chat",
        "jobToolkit": {},
    })


def test_save_and_list_by_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path)
    invalidate_catalog()
    save_agent(_user_agent("alpha", "ws-a"))
    save_agent(_user_agent("beta", "ws-b"))
    ids_a = [item.id for item in list_workspace_agents("ws-a")]
    ids_b = [item.id for item in list_workspace_agents("ws-b")]
    assert ids_a == ids_b
    assert "alpha" in ids_a and "beta" in ids_a
    assert "default" in ids_a
    assert get_registered_agent("alpha").system_prompt == "p"


def test_id_conflict_keeps_first(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path)
    invalidate_catalog()
    first = save_agent(_user_agent("dup", "ws-a", "one"))
    other = profile_path("ws-b", "dup")
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text((global_agents_root() / "dup.md").read_text(encoding="utf-8").replace("ws-a", "ws-b"), encoding="utf-8")
    invalidate_catalog()
    loaded = get_registered_agent("dup")
    assert loaded is not None
    assert loaded.source_path == first.source_path


def test_symlink_outside_catalog_is_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path)
    invalidate_catalog()
    save_agent(_user_agent("ok", "default"))
    outside = tmp_path / "outside.md"
    outside.write_text("leak", encoding="utf-8")
    link = global_agents_root() / "leak.md"
    link.symlink_to(outside)
    invalidate_catalog()
    from src.catalog.loader import ensure_loaded

    assert get_registered_agent("leak") is None
    snapshot = ensure_loaded()
    assert any("根外" in item.message for item in snapshot.errors)


def test_seed_catalog_profiles_readonly(tmp_path) -> None:
    from src.agents.factory import _seed_catalog_profiles_readonly

    catalog = tmp_path / "catalog"
    profiles = catalog / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "writer.md").write_text("hello", encoding="utf-8")
    host = tmp_path / "sandbox"
    _seed_catalog_profiles_readonly(host, [str(catalog)])
    copied = host / ".agents" / "profiles" / "writer.md"
    assert copied.read_text(encoding="utf-8") == "hello"
