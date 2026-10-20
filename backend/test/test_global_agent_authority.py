"""全局 Agent 權威目錄。多個 Session 引用同一個 ID，不複製正文。"""

from src.catalog.agent_models import AgentResponse
from src.catalog.loader import get_registered_agent, invalidate_catalog, save_agent
from src.catalog.paths import profile_path
from src.config.runtime_paths import global_agents_root, session_root
from src.sessions.records import create_session


def _agent(agent_id: str, prompt: str) -> AgentResponse:
    return AgentResponse.model_validate({
        "id": agent_id,
        "name": "共用助手",
        "avatar": "",
        "description": "desc",
        "system_prompt": prompt,
        "modelConfig": {"maxReactIters": 8},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
        "tags": [],
        "guardrails": {},
        "status": "idle",
        "created_at": "2026-10-19T00:00:00+00:00",
        "updated_at": "2026-10-19T00:00:00+00:00",
        "workspaceId": "default",
        "role": "chat",
        "jobToolkit": {},
    })


def test_sessions_share_one_global_agent_id() -> None:
    invalidate_catalog()
    saved = save_agent(_agent("shared-agent", "同一份正文"))
    global_path = global_agents_root() / "shared-agent.md"
    assert global_path.is_file()
    assert saved.source_path == str(global_path.resolve())
    assert not profile_path("default", "shared-agent").exists()
    first = create_session(session_id="sesagent1")
    second = create_session(session_id="sesagent2")
    assert get_registered_agent("shared-agent").id == "shared-agent"
    assert get_registered_agent("shared-agent").source_path == saved.source_path
    assert not (session_root(first["id"]) / "agents").exists()
    assert not (session_root(second["id"]) / "agents").exists()


def test_legacy_profile_is_read_only_adapter() -> None:
    invalidate_catalog()
    legacy = profile_path("default", "legacy-agent")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "---\nid: legacy-agent\nname: 舊助手\nrole: chat\ndescription: old\n---\n\n舊正文\n",
        encoding="utf-8",
    )
    invalidate_catalog()
    loaded = get_registered_agent("legacy-agent")
    assert loaded is not None
    assert loaded.system_prompt == "舊正文"
    save_agent(_agent("legacy-agent", "新全局正文"))
    invalidate_catalog()
    current = get_registered_agent("legacy-agent")
    assert current is not None
    assert current.system_prompt == "新全局正文"
    assert "舊正文" in legacy.read_text(encoding="utf-8")
    assert "新全局正文" in (global_agents_root() / "legacy-agent.md").read_text(encoding="utf-8")
