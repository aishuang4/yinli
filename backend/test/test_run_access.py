"""Run 資源讀取契約。"""

from __future__ import annotations

import pytest

from src.sessions.errors import SessionError
from src.sessions.records import create_session
from src.sessions.resources import put_session_resources
from src.sessions.run_access import RunResourceAccess, candidate_resource_access, run_resource_access
from src.sessions.run_access import _from_items
from src.sessions.run_binding import begin_run
from src.skills.service import catalog_for


def test_resource_type_mapping_and_filter_ids() -> None:
    access = _from_items("ses", "agent-1", [
        {"resourceType": "skill", "resourceId": "skill-a"},
        {"resourceType": "mcp", "resourceId": "mcp-a"},
        {"resourceType": "http_tool", "resourceId": "http-a"},
        {"resourceType": "knowledge", "resourceId": "know-a"},
    ])
    assert access.skill_ids == frozenset({"skill-a"})
    assert access.mcp_server_ids == frozenset({"mcp-a"})
    assert access.http_tool_ids == frozenset({"http-a", "know-a"})
    assert access.filter_ids("skill", ["missing", "skill-a", "skill-a"]) == ["skill-a"]
    assert access.allows("agent", "agent-1") is True
    assert access.allows("agent", "other") is False
    assert access.allows("unknown", "skill-a") is False
    ordered = RunResourceAccess(
        "ses",
        "agent-1",
        frozenset({"b", "a"}),
        frozenset(),
        frozenset(),
    )
    assert ordered.filter_ids("skill", ["b", "a", "b", "c"]) == ["b", "a"]


def test_missing_snapshot_raises_snapshot_required() -> None:
    with pytest.raises(SessionError) as exc_info:
        run_resource_access("run-missing")
    assert exc_info.value.code == "snapshot_required"


def test_candidate_and_run_access_match(tmp_path) -> None:
    from src.config.runtime_paths import tenant_root

    package = tenant_root() / "skills" / "access-report"
    package.mkdir(parents=True, exist_ok=True)
    (package / "SKILL.md").write_text("---\nname: access-report\ndescription: 說明\n---\n正文\n", encoding="utf-8")
    skill_id = next(item.id for item in catalog_for(refresh=True).candidates if item.name == "access-report")
    session = create_session(title="對照", agent_id="default")
    put_session_resources(session["id"], [{
        "resourceType": "skill",
        "resourceId": skill_id,
        "enabled": True,
        "configOverride": {"pinned": True},
    }])
    preview = candidate_resource_access(session["id"], agent_id="default", permission_mode="manual")
    begin_run(session["id"], "run-access", agent_id="default", permission_mode="manual", start_model=lambda item: item)
    frozen = run_resource_access("run-access")
    assert preview == frozen
    assert skill_id in frozen.skill_ids
