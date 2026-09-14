"""Agent 智能创建的文本事件解析和 Project 绑定回归测试。"""

from datetime import datetime, timezone

import pytest

from src.server.routes import agent_builder
from src.server.routes.agent_builder import (
    BuilderAgentDraft,
    BuilderApplyRequest,
    BuilderPlanResponse,
    _event_text,
)
from src.server.routes.agents import AgentResponse


def test_event_text_reads_incremental_text() -> None:
    assert _event_text({"type": "TEXT_BLOCK_DELTA", "delta": '{"agents": ['}) == '{"agents": ['


def test_event_text_reads_complete_text_message_content() -> None:
    assert _event_text({
        "type": "TEXT_MESSAGE_CONTENT",
        "content": [{"type": "text", "text": '{"agents": []}'}],
    }) == '{"agents": []}'


def test_event_text_ignores_non_text_events() -> None:
    assert _event_text({"type": "THINKING_BLOCK_DELTA", "delta": "..."}) == ""


@pytest.mark.anyio
async def test_apply_builder_binds_agents_to_current_project(monkeypatch: pytest.MonkeyPatch) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    created = AgentResponse.model_validate({
        "id": "generated-agent",
        "name": "生成 Agent",
        "avatar": "",
        "description": "用于测试",
        "system_prompt": "prompt",
        "modelConfig": {},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
        "tags": [],
        "planConfig": {},
        "guardrails": {},
        "status": "idle",
        "created_at": created_at,
        "updated_at": created_at,
        "workspaceId": "workspace-1",
        "role": "chat",
        "jobToolkit": {},
    })
    create_calls: list[object] = []

    async def fake_create_agent(data: object) -> AgentResponse:
        create_calls.append(data)
        return created

    monkeypatch.setattr(agent_builder, "get_project", lambda project_id: {
        "id": project_id,
        "workspaceId": "workspace-1",
    })
    monkeypatch.setattr(agent_builder, "create_agent", fake_create_agent)

    response = await agent_builder.apply_agents(BuilderApplyRequest(
        workspaceId="workspace-1",
        projectId="project-1",
        plan=BuilderPlanResponse(
            requirement="测试",
            agents=[BuilderAgentDraft(name="生成 Agent")],
        ),
    ))

    assert response.created_agents[0].id == "generated-agent"
    assert len(create_calls) == 1
    assert getattr(create_calls[0], "workspace_id") == "workspace-1"
