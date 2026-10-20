"""Session 資源 grant、Run 候選與 snapshot。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.runtime.agent_spec import build_runtime_agent_cache_key
from src.agents.runtime_types import RuntimeToolScope
from src.server.main import app
from src.server.routes.chat.trace_recorder import _compression_payload
from src.sessions.errors import SessionError
from src.sessions.records import create_session, get_run_snapshot
from src.sessions.resources import put_session_resources
from src.sessions.run_binding import (
    assert_new_run_resources,
    begin_run,
    bind_run_snapshot,
    current_snapshot_audit,
    frozen_resources_for_run,
    snapshot_audit_summary,
)
from src.skills.catalog import current_skill_context
from src.skills.policy import set_skill_policy
from src.skills.service import catalog_for
from src.tenancy.context import TenantContext, use_tenant


def _skill(root: Path, name: str, body: str = "正文") -> str:
    package = root / "skills" / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 說明\n---\n{body}\n",
        encoding="utf-8",
    )
    catalog = catalog_for(refresh=True)
    match = next(item for item in catalog.candidates if item.name == name)
    return match.id


def _session(skill_id: str, *, pinned: bool = True) -> dict:
    session = create_session(session_id=f"ses{skill_id[-6:]}", agent_id="default")
    put_session_resources(session["id"], [{
        "resourceType": "skill",
        "resourceId": skill_id,
        "enabled": True,
        "configOverride": {"pinned": pinned},
    }])
    return session


def test_resource_api_rejects_missing_disabled_and_cross_tenant() -> None:
    from src.config.runtime_paths import tenant_root

    skill_id = _skill(tenant_root(), "report")
    session = create_session(session_id="sesres", agent_id="default")
    set_skill_policy(current_skill_context().policy_path, skill_id, availableToAllAgents=False)
    with pytest.raises(SessionError) as ineligible:
        put_session_resources(session["id"], [{
            "resourceType": "skill",
            "resourceId": skill_id,
            "configOverride": {},
        }])
    assert ineligible.value.code == "agent_ineligible"
    put_session_resources(session["id"], [{
        "resourceType": "skill",
        "resourceId": skill_id,
        "configOverride": {"pinned": True},
    }])
    set_skill_policy(current_skill_context().policy_path, skill_id, enabled=False)
    with pytest.raises(SessionError) as disabled:
        put_session_resources(session["id"], [{
            "resourceType": "skill",
            "resourceId": skill_id,
            "configOverride": {"pinned": True},
        }])
    assert disabled.value.code == "resource_disabled"
    with pytest.raises(SessionError) as missing:
        put_session_resources(session["id"], [{
            "resourceType": "skill",
            "resourceId": "skill-missing",
            "configOverride": {"pinned": True},
        }])
    assert missing.value.code == "resource_not_found"
    other = TenantContext.hosted("usr_" + "ab" * 16)
    with use_tenant(other):
        foreign_session = create_session(session_id="sesforeign", agent_id="default")
        with pytest.raises(SessionError) as foreign:
            put_session_resources(foreign_session["id"], [{
                "resourceType": "skill",
                "resourceId": "../" + other.tenant_id + "/skills/report",
                "configOverride": {"pinned": True},
            }])
    assert foreign.value.code == "cross_tenant_resource"
    with TestClient(app) as client:
        response = client.get(f"/api/sessions/{session['id']}/resources")
    assert response.status_code == 200


def _pin_and_expect(resource_type, tool_id, directory, body) -> None:
    from src.config.runtime_paths import global_mcp_root, global_tools_root
    from src.sessions.candidates import select_run_candidates

    root = global_mcp_root() if directory == "mcp" else global_tools_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{tool_id}.yaml").write_text(body, encoding="utf-8")
    session = create_session(session_id=f"ses{tool_id}", agent_id="default")
    put_session_resources(session["id"], [{
        "resourceType": resource_type,
        "resourceId": tool_id,
        "configOverride": {"pinned": True},
    }])
    found = select_run_candidates(session["id"])
    assert {"resourceType": resource_type, "resourceId": tool_id, "source": "session"} in found


@pytest.mark.parametrize(
    ("resource_type", "tool_id", "directory", "body"),
    [
        ("mcp", "remote", "mcp", "id: remote\nname: Remote\nurl: https://example.com/mcp\ntransport: sse\nenabled: true\n"),
        ("http_tool", "hook", "tools", "id: hook\nname: Hook\nurl: https://example.com/api\nenabled: true\n"),
        ("knowledge", "kb", "tools", "id: kb\nname: KB\nurl: https://example.com/kb\nenabled: true\n"),
    ],
)
def test_resource_type_matrix(resource_type, tool_id, directory, body) -> None:
    _pin_and_expect(resource_type, tool_id, directory, body)


def test_candidate_order_is_stable() -> None:
    from src.config.runtime_paths import tenant_root
    from src.sessions.candidates import select_run_candidates

    skill_id = _skill(tenant_root(), "pinned-report")
    set_skill_policy(current_skill_context().policy_path, skill_id, availableToAllAgents=False)
    session = _session(skill_id)
    first = select_run_candidates(session["id"], permission_mode="manual")
    second = select_run_candidates(session["id"], permission_mode="manual")
    assert first == second
    assert {"resourceType": "skill", "resourceId": skill_id, "source": "session"} in first
    readonly = select_run_candidates(session["id"], permission_mode="read_only")
    assert all(item["resourceType"] != "http_tool" for item in readonly)
    assert any(item["resourceId"] == skill_id for item in readonly)


def test_unpinned_disabled_grant_does_not_block_a_new_run() -> None:
    from src.config.runtime_paths import tenant_root

    skill_id = _skill(tenant_root(), "unchecked-report")
    session = _session(skill_id, pinned=False)
    set_skill_policy(current_skill_context().policy_path, skill_id, enabled=False)
    assert_new_run_resources(session["id"])
    from src.config.runtime_paths import tenant_root

    skill_id = _skill(tenant_root(), "frozen-report")
    session = _session(skill_id)
    started: list[str] = []

    def explode() -> None:
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError):
        begin_run(
            session["id"],
            "run-fail",
            agent_id="default",
            start_model=lambda snapshot: started.append(snapshot["id"]),
            fail_before_commit=explode,
        )
    assert started == []
    assert get_run_snapshot("run-fail") is None

    begin_run(session["id"], "run-ok", agent_id="default", start_model=lambda snapshot: started.append(snapshot["id"]))
    original = frozen_resources_for_run("run-ok")
    set_skill_policy(current_skill_context().policy_path, skill_id, enabled=False)
    with pytest.raises(SessionError) as blocked:
        assert_new_run_resources(session["id"])
    assert blocked.value.code == "resource_disabled"
    assert frozen_resources_for_run("run-ok") == original
    bind_run_snapshot("run-ok")
    summary = current_snapshot_audit()
    assert summary["skills"] == [skill_id]
    assert "secret" not in summary
    assert snapshot_audit_summary({**original, "resources": {"items": original["resources"]["items"], "token": "sk-secret"}})["skills"] == [skill_id]
    payload = _compression_payload({"index": 1})
    assert payload["snapshotId"] == original["id"]
    assert "sk-secret" not in str(payload)


def test_cache_key_uses_snapshot_id() -> None:
    scope = RuntimeToolScope()
    common = dict(
        agent_id="default",
        session_id="ses-cache",
        system_prompt="ignored",
        model_name="m",
        deep_thinking=False,
        fallback_names=[],
        used_skills=[],
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=scope,
        guardrail_cache_key="g",
    )
    left = build_runtime_agent_cache_key(**common, run_snapshot_id="snap-a")
    right = build_runtime_agent_cache_key(**common, run_snapshot_id="snap-b")
    assert left != right
    assert "snap-a" in left
