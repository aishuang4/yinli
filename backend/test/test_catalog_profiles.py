"""profile Markdown codec。"""

from pathlib import Path

from src.catalog.agent_models import AgentResponse
from src.catalog.profiles import dump_profile_markdown, parse_profile_markdown, write_profile_file
from src.catalog.system_defaults import build_default_agent


def _parse(text: str, path: Path, workspace_id: str = "default"):
    return parse_profile_markdown(text, path=path, workspace_id=workspace_id)


def test_uuid_and_short_id_round_trip(tmp_path: Path) -> None:
    uuid = "3f2a0c8e-7b41-4d9a-9c1e-2ab34def5678"
    for agent_id, name in ((uuid, "UUID 助手"), ("researcher", "研究助手")):
        agent = AgentResponse.model_validate({
            "id": agent_id,
            "name": name,
            "avatar": "",
            "description": "desc",
            "system_prompt": "你是助手。",
            "modelConfig": {"maxReactIters": 12},
            "mcpTools": ["mcp-1"],
            "knowledgeTools": [],
            "skills": ["word-documents"],
            "tags": ["a"],
            "guardrails": {},
            "status": "idle",
            "created_at": "2026-09-10T00:00:00+00:00",
            "updated_at": "2026-09-10T00:00:00+00:00",
            "workspaceId": "ws-a",
            "role": "chat",
            "jobToolkit": {},
        })
        path = tmp_path / f"{agent_id}.md"
        written = write_profile_file(path, agent)
        parsed = _parse(path.read_text(encoding="utf-8"), path, "ws-a")
        assert parsed.agent is not None
        assert parsed.agent.id == agent_id
        assert parsed.agent.system_prompt == "你是助手。"
        assert parsed.agent.skills == ["word-documents"]
        assert parsed.agent.mcp_tools == ["mcp-1"]
        assert parsed.agent.model_config_data.max_react_iters == 12
        assert written.id == agent_id


def test_filename_mismatch_is_error(tmp_path: Path) -> None:
    text = dump_profile_markdown(AgentResponse.model_validate({
        "id": "researcher",
        "name": "研究",
        "avatar": "",
        "description": "",
        "system_prompt": "hi",
        "modelConfig": {},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
        "tags": [],
        "guardrails": {},
        "created_at": "2026-09-10T00:00:00+00:00",
        "updated_at": "2026-09-10T00:00:00+00:00",
        "workspaceId": "default",
        "role": "chat",
        "jobToolkit": {},
    }))
    parsed = _parse(text, tmp_path / "other.md")
    assert parsed.agent is None
    assert any("文件名与 id 不一致" in item for item in parsed.errors)


def test_default_cannot_be_job(tmp_path: Path) -> None:
    agent = build_default_agent()
    dumped = dump_profile_markdown(agent.model_copy(update={"role": "job"}))
    parsed = _parse(dumped, tmp_path / "default.md")
    assert parsed.agent is None
    assert any("default 不能改为 job" in item for item in parsed.errors)
