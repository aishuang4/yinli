"""TeamRun 檔案落在 Session root，成員目錄保持私有，並繼承父 Run snapshot。"""

from pathlib import Path
from types import SimpleNamespace

from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, McpRuntimeSpec
from src.config.runtime_paths import session_member_root, session_team_root
from src.runtime_context import RuntimeContext
from src.server.routes.chat.runtime_builder import RuntimeModelConfig
from src.server.routes.chat.team_runtime.member_runtime import _create_team_agent, _member_write_dir
from src.server.routes.chat.team_runtime.store import TeamStore
from src.sessions.records import create_session, freeze_run_snapshot
from src.storage.database import ensure_db


def test_team_run_and_member_dir_stay_under_session_root() -> None:
    ensure_db()
    create_session(session_id="ses-team", agent_id="agent-shared")
    store = TeamStore.create(
        session_id="ses-team",
        chat_run_id="run-team",
        goal="協作",
        leader_agent_id="agent-shared",
        leader_name="Leader",
    )
    assert store.ref.path == session_team_root("ses-team", store.ref.id)
    assert TeamStore.load(store.ref.id).ref.path == store.ref.path
    member = store.spawn_member(name="Researcher", role="research", runtime_prompt="private")
    write_dir = _member_write_dir(SimpleNamespace(session_id="ses-team", session_workspace_dir=""), store, member["id"])
    assert write_dir == str(session_member_root("ses-team", store.ref.id, member["id"]))
    assert write_dir.startswith(str(store.ref.path))


def test_team_agents_inherit_parent_snapshot_without_losing_private_dir(monkeypatch) -> None:
    ensure_db()
    work = create_session(session_id="ses-team-snap", agent_id="agent-shared", working_directory="")
    del work
    snapshot = freeze_run_snapshot(
        run_id="run-team-snap",
        session_id="ses-team-snap",
        working_directory="",
        allowed_roots=[],
        resources={"items": [{"resourceType": "mcp", "resourceId": "maps"}]},
        model_snapshot={},
        approval_snapshot={},
        agent_snapshot={"agentId": "agent-shared"},
        snapshot_id="snap-team",
    )
    captured = {}

    def fake_create(spec, **_kwargs):
        captured["spec"] = spec
        return spec

    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.member_runtime.create_main_agent_from_spec",
        fake_create,
    )
    ctx = SimpleNamespace(
        agent_id="agent-shared",
        session_id="ses-team-snap",
        persist_run=False,
        session_workspace_dir="/external",
        runtime_context=RuntimeContext(
            workspace_id="default",
            project_id="default",
            project_root=Path("."),
            session_id="ses-team-snap",
            run_id="run-team-snap",
            agent_id="agent-shared",
        ),
        runtime_model_config=RuntimeModelConfig(
            model_name="model",
            fallback_names=[],
            model_options=AgentModelOptions(),
        ),
        guardrails=AgentGuardrails(filesystem_scope="/external"),
        skill_paths=[],
        used_skills=[],
    )
    _create_team_agent(
        ctx=ctx,
        actor_id="member_aabbccddeeff",
        actor_name="Member",
        session_id="member-session",
        system_prompt="work",
        runtime_key="team-member-test",
        extra_tools=[],
        tool_allowlist=set(),
        used_skills=[],
        mcp_servers=[
            McpRuntimeSpec(name="maps", transport="sse", url="http://maps.local"),
            McpRuntimeSpec(name="mail", transport="sse", url="http://mail.local"),
        ],
        max_react_iters=8,
        write_scope_dir="/private/member",
    )
    spec = captured["spec"]
    assert spec.write_scope_dir == "/private/member"
    assert spec.workspace_dir == "/external"
    assert [item.name for item in spec.mcp_servers] == ["maps"]
    assert f"snapshot-{snapshot['id']}" in spec.runtime_agent_id
