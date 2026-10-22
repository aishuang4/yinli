"""群聊工作室 PR 1：flag、空壳路由、隔离墙、include_default_tools。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, RuntimeToolScope
from src.group_studio.errors import GROUP_STUDIO_DISABLED, HOST_REQUIRED
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID, GroupStudioFromChatRequest
from src.group_studio.settings import (
    get_group_studio_settings_public,
    is_group_studio_enabled,
    set_group_studio_settings,
)
from src.server.routes import group_studio, settings as settings_routes
from src.server.routes.chat.runtime_builder import (
    MainAgentRuntimeSpec,
    RuntimeModelConfig,
    build_main_agent_runtime_spec,
)
from src.storage.engine import database
from src.storage.engine.database import ensure_db

BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例使用独立 SQLite。"""

    yield
    database.forget_db(database.db_path())


@pytest.fixture()
def client(isolated_db):
    ensure_db()
    app = FastAPI()
    app.include_router(group_studio.router, prefix="/api/group-studio")
    app.include_router(settings_routes.router, prefix="/api/settings")
    with TestClient(app) as test_client:
        yield test_client


def test_flag_defaults_enabled(isolated_db):
    ensure_db()

    assert is_group_studio_enabled() is True
    public = get_group_studio_settings_public()
    assert public["enabled"] is True
    assert public["maxMembers"] == 6
    assert public["maxParallelWorkers"] == 1
    assert public["maxTurns"] == 40


def test_settings_readable_by_default(client):
    response = client.get("/api/settings/group-studio")

    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_settings_put_enables_and_clamps_parallel(client):
    response = client.put("/api/settings/group-studio", json={
        "enabled": True,
        "maxMembers": 9,
        "maxParallelWorkers": 3,
        "maxTurns": 12,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["maxMembers"] == 6
    assert body["maxParallelWorkers"] == 1
    assert body["maxTurns"] == 12
    assert is_group_studio_enabled() is True


def test_from_chat_404_when_disabled(client):
    set_group_studio_settings(enabled=False)
    response = client.post("/api/group-studio/from-chat", json={
        "projectSlug": "default",
        "chatSessionId": "sess-1",
        "agentId": "default",
    })

    assert response.status_code == 404
    assert response.json()["detail"] == GROUP_STUDIO_DISABLED


def test_get_by_chat_404_when_disabled(client):
    set_group_studio_settings(enabled=False)
    response = client.get("/api/group-studio/by-chat/sess-1")

    assert response.status_code == 404
    assert response.json()["detail"] == GROUP_STUDIO_DISABLED


def test_pause_404_when_studio_missing(client):
    set_group_studio_settings(enabled=True)
    response = client.post("/api/group-studio/gstu_x/pause", json={"projectSlug": "default"})

    assert response.status_code == 404
    assert response.json()["detail"] == "not_found"


def test_main_registers_group_studio_routes():
    from src.server.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/group-studio/from-chat" in paths
    assert "/api/group-studio" in paths
    assert "/api/settings/group-studio" in paths


def test_list_studios_is_independent_of_chat_catalog(client):
    set_group_studio_settings(enabled=True)
    created = client.post("/api/group-studio/from-chat", json={
        "projectSlug": "default",
        "chatSessionId": "sess-list",
        "agentId": "default",
        "draftText": "宇树研究",
    })
    assert created.status_code == 200, created.text
    listed = client.get("/api/group-studio", params={"projectSlug": "default"})
    assert listed.status_code == 200
    ids = [item["id"] for item in listed.json()["studios"]]
    assert created.json()["id"] in ids


def test_host_sentinel_constant():
    assert GROUP_STUDIO_HOST_AGENT_ID == "group-studio-host"


def test_from_chat_snapshot_omits_v1_board_fields(client):
    set_group_studio_settings(enabled=True)
    created = client.post("/api/group-studio/from-chat", json={
        "projectSlug": "default",
        "chatSessionId": "sess-slim",
        "agentId": "default",
    })
    assert created.status_code == 200, created.text
    body = created.json()
    assert "pin" in body
    assert "workItems" not in body
    assert "blackboard" not in body
    assert "awaitingIntakeConfirm" not in body


def test_host_sentinel_cannot_join(client):
    set_group_studio_settings(enabled=True)
    created = client.post("/api/group-studio/from-chat", json={
        "projectSlug": "default",
        "chatSessionId": "sess-host",
        "agentId": "default",
    })
    assert created.status_code == 200, created.text
    response = client.post(f"/api/group-studio/{created.json()['id']}/members", json={
        "projectSlug": "default",
        "agentId": GROUP_STUDIO_HOST_AGENT_ID,
    })
    assert response.status_code == 409
    assert response.json()["detail"] == HOST_REQUIRED


def test_from_chat_request_accepts_camel_case():
    body = GroupStudioFromChatRequest.model_validate({
        "projectSlug": "p1",
        "chatSessionId": "s1",
        "agentId": "default",
        "draftText": "写方案",
        "filePaths": ["a.md"],
    })
    assert body.project_slug == "p1"
    assert body.chat_session_id == "s1"
    assert body.draft_text == "写方案"


def test_main_agent_spec_defaults_include_default_tools():
    spec = build_main_agent_runtime_spec(
        agent_id="agent",
        agent_name="Agent",
        session_id="session",
        system_prompt="prompt",
        model_config=RuntimeModelConfig(
            model_name="model",
            fallback_names=[],
            model_options=AgentModelOptions(),
        ),
        deep_thinking=False,
        used_skills=[],
        guardrails=AgentGuardrails(),
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=RuntimeToolScope(),
        guardrail_cache_key="guardrails",
        include_default_tools=False,
    )

    assert isinstance(spec, MainAgentRuntimeSpec)
    assert spec.include_default_tools is False

    default_spec = build_main_agent_runtime_spec(
        agent_id="agent",
        agent_name="Agent",
        session_id="session",
        system_prompt="prompt",
        model_config=RuntimeModelConfig(
            model_name="model",
            fallback_names=[],
            model_options=AgentModelOptions(),
        ),
        deep_thinking=False,
        used_skills=[],
        guardrails=AgentGuardrails(),
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=RuntimeToolScope(),
        guardrail_cache_key="guardrails",
    )
    assert default_spec.include_default_tools is True


def _python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


def test_chat_handlers_and_team_runtime_do_not_import_group_studio():
    roots = [
        BACKEND_SRC / "server" / "routes" / "chat" / "handlers",
        BACKEND_SRC / "server" / "routes" / "chat" / "team_runtime",
    ]
    offenders: list[str] = []
    for root in roots:
        for path in _python_files(root):
            text = path.read_text(encoding="utf-8")
            if "src.group_studio" in text or "routes.group_studio" in text:
                offenders.append(str(path.relative_to(BACKEND_SRC)))
    assert offenders == []


def test_group_studio_does_not_import_chat_hitl_or_leader_stream():
    roots = [
        BACKEND_SRC / "group_studio",
        BACKEND_SRC / "server" / "routes" / "group_studio.py",
    ]
    forbidden = (
        "leader_stream",
        "PENDING_INTERACTIONS",
        "src.server.routes.chat.hitl",
        "team_runtime",
    )
    offenders: list[str] = []
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        else:
            files.extend(_python_files(root))
    for path in files:
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(BACKEND_SRC)))
    assert offenders == []
