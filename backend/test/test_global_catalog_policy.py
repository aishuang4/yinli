"""Skill policy 與 MCP / HTTP 定義只屬於 tenant 全局目錄。"""

from src.catalog.mcp_files import discover_mcp_files
from src.catalog.http_files import discover_http_files
from src.skills.catalog import build_skill_catalog, stable_skill_id
from src.skills.models import SkillContext
from src.skills.policy import read_policy, set_skill_policy


def _manual(root, name: str, body: str) -> None:
    package = root / "skills" / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 說明\n---\n{body}\n",
        encoding="utf-8",
    )


def test_skill_policy_and_id_ignore_workspace(tmp_path) -> None:
    tenant = tmp_path / "tenant"
    _manual(tenant, "report", "第一版")
    left = SkillContext(tenant_root=tenant, tenant_id="local", workspace_id="ws-a")
    right = SkillContext(tenant_root=tenant, tenant_id="local", workspace_id="ws-b")
    assert left.policy_path == tenant / "policies" / "skills-policy.json"
    assert right.policy_path == left.policy_path
    first = build_skill_catalog(left)
    second = build_skill_catalog(right)
    assert first.candidates[0].id == second.candidates[0].id
    assert "workspace:" not in first.candidates[0].root.owner_key
    set_skill_policy(left.policy_path, first.candidates[0].id, enabled=False, availableToAllAgents=False)
    assert read_policy(right.policy_path).overrides[first.candidates[0].id].enabled is False
    (tenant / "skills" / "report" / "SKILL.md").write_text(
        "---\nname: report\ndescription: 說明\n---\n第二版\n",
        encoding="utf-8",
    )
    refreshed = build_skill_catalog(right, refresh=True)
    assert refreshed.candidates[0].id == first.candidates[0].id
    assert refreshed.candidates[0].content_hash != first.candidates[0].content_hash
    assert stable_skill_id(first.candidates[0].root, "report") == first.candidates[0].id


def test_conflict_mapping_merges_same_body_and_preserves_conflicts(tmp_path) -> None:
    from src.session_migration.mapping import apply_mapping_to_grants, map_same_id_files, materialize_merged_files

    same_a = tmp_path / "left" / "agent.md"
    same_b = tmp_path / "right" / "agent.md"
    differ = tmp_path / "other" / "agent.md"
    for path, body in ((same_a, "same"), (same_b, "same"), (differ, "other")):
        path.parent.mkdir()
        path.write_text(body, encoding="utf-8")
    mapping = map_same_id_files([same_a, same_b])
    assert mapping["merged"][0]["sources"] == [str(same_a), str(same_b)]
    assert mapping["conflicts"] == []
    blocked = map_same_id_files([same_a, differ])
    assert blocked["merged"] == []
    assert blocked["conflicts"][0]["action"] == "block"
    destination = tmp_path / "agents" / "agent.md"
    destination.parent.mkdir()
    destination.write_text("kept", encoding="utf-8")
    materialize_merged_files(blocked, destination.parent)
    assert destination.read_text(encoding="utf-8") == "kept"
    grants = apply_mapping_to_grants(
        [{"resourceId": "agent"}],
        map_same_id_files([same_a, same_b]),
    )
    assert grants[0]["resolution"] == "mapped"
    unresolved = apply_mapping_to_grants([{"resourceId": "agent"}], blocked)
    assert unresolved[0]["resolution"] == "unresolved"
    assert unresolved[0]["resourceId"] == "agent"


def test_plaintext_tokens_do_not_enter_session_report_or_external_dir(tmp_path) -> None:
    from src.config import runtime_paths
    from src.session_migration.backup import write_backup
    from src.session_migration.redaction import contains_plaintext_secret
    from src.sessions.records import create_session, freeze_run_snapshot

    session = create_session(session_id="sessecret")
    root = runtime_paths.session_root(session["id"])
    root.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (external / "notes.txt").write_text("business", encoding="utf-8")
    snapshot = freeze_run_snapshot(
        run_id="run-secret",
        session_id=session["id"],
        working_directory=str(external),
        allowed_roots=[],
        resources={"mcp": {"id": "remote", "secretRef": "secret:mcp:remote:auth"}},
        model_snapshot={"id": "m"},
        approval_snapshot={"mode": "manual"},
    )
    assert "sk-" not in str(snapshot)
    tenant = runtime_paths.tenant_root()
    secret = tenant / "skills" / "hidden"
    secret.mkdir(parents=True)
    (secret / "SKILL.md").write_text("apiKey: sk-supersecretvalue\n", encoding="utf-8")
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    report = tmp_path / "report"
    write_backup(conn, tenant_root=tenant, destination=report)
    for folder in (root, report, external):
        for path in folder.rglob("*"):
            if path.is_file():
                assert not contains_plaintext_secret(path.read_text(encoding="utf-8", errors="ignore"))


def test_zero_session_user_can_manage_global_catalog() -> None:
    from fastapi.testclient import TestClient

    from src.server.main import app
    from src.storage.database import list_chat_sessions

    assert list_chat_sessions() == []
    with TestClient(app) as client:
        responses = [
            client.get("/api/agents"),
            client.get("/api/skills"),
            client.get("/api/mcp"),
            client.get("/api/tools/http"),
            client.get("/api/settings/models"),
        ]
        created = client.post("/api/agents", json={"name": "零会话助手", "systemPrompt": "hello"})
    assert [item.status_code for item in responses] == [200, 200, 200, 200, 200]
    assert created.status_code == 201
    assert list_chat_sessions() == []


def test_global_agent_seed_is_idempotent_and_does_not_insert_project() -> None:
    from src.catalog.global_seed import seed_global_agents
    from src.storage.database import _connect, _lock, ensure_db

    ensure_db()
    with _lock, _connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    first = seed_global_agents()
    second = seed_global_agents()
    assert first == second
    assert "default" in first
    assert "system-websearch-job" in first
    with _lock, _connect() as conn:
        after = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert after == before


def test_external_mcp_and_http_definitions_stay_out_of_catalog(tmp_path, monkeypatch) -> None:
    from src.config import runtime_paths

    monkeypatch.setattr(runtime_paths, "USER_DATA_ROOT", tmp_path / "tenant")
    external = tmp_path / "repo"
    (external / ".agents" / "mcp").mkdir(parents=True)
    (external / ".agents" / "tools").mkdir(parents=True)
    (external / ".agents" / "mcp" / "local.yaml").write_text(
        "id: local\nname: Local\nurl: https://example.com/mcp\ntransport: sse\n",
        encoding="utf-8",
    )
    (external / ".agents" / "tools" / "local-http.yaml").write_text(
        "id: local-http\nname: Local\nurl: https://example.com/api\n",
        encoding="utf-8",
    )
    global_mcp = runtime_paths.global_mcp_root()
    global_mcp.mkdir(parents=True)
    (global_mcp / "remote.yaml").write_text(
        "id: remote\nname: Remote\nurl: https://example.com/remote\ntransport: sse\n",
        encoding="utf-8",
    )
    assert [item.id for item in discover_mcp_files(project_root=external)] == ["remote"]
    assert discover_http_files(project_root=external) == []
