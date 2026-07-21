"""默认 Agent 持久配置回归测试。"""

from __future__ import annotations

import asyncio

import pytest

from src.server.routes import agents as agents_routes


def _agent(agent_id: str, workspace_id: str) -> agents_routes.AgentResponse:
    """构造测试 Agent。"""

    return agents_routes.AgentResponse.model_validate({
        "id": agent_id,
        "name": agent_id,
        "avatar": "",
        "description": "",
        "system_prompt": "prompt",
        "modelConfig": {},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
        "tags": [],
        "guardrails": {},
        "status": "idle",
        "created_at": "2026-07-21T00:00:00+00:00",
        "updated_at": "2026-07-21T00:00:00+00:00",
        "workspaceId": workspace_id,
    })


def test_default_agent_is_visible_in_every_workspace(monkeypatch) -> None:
    """系统默认 Agent 不依赖 Project binding，并在任意 Workspace 可配置。"""

    monkeypatch.setattr(agents_routes, "_agents", {
        "default": _agent("default", "default"),
        "workspace-agent": _agent("workspace-agent", "workspace-b"),
        "other-agent": _agent("other-agent", "workspace-a"),
    })

    agents = asyncio.run(agents_routes.list_agents(workspace_id="workspace-b"))

    assert [agent.id for agent in agents] == ["default", "workspace-agent"]


def test_default_agent_cannot_be_deleted(monkeypatch) -> None:
    """默认 Agent 可更新但不能删除回退为不可配置的虚拟对象。"""

    default_agent = _agent("default", "default")
    monkeypatch.setattr(agents_routes, "_agents", {"default": default_agent})

    with pytest.raises(Exception) as exc_info:
        asyncio.run(agents_routes.delete_agent("default"))

    assert getattr(exc_info.value, "status_code", None) == 403
    assert agents_routes._agents["default"] is default_agent


def test_existing_default_agent_gets_final_delivery_prompt_once(monkeypatch) -> None:
    """升级历史默认 Agent 时保留用户提示词，并补齐最终交付规则。"""

    default_agent = _agent("default", "default")
    monkeypatch.setattr(agents_routes, "_agents", {"default": default_agent})
    monkeypatch.setattr(agents_routes, "upsert_agent", lambda _data: None)
    monkeypatch.setattr(agents_routes, "invalidate_agent", lambda _agent_id: None)

    agents_routes._ensure_default_agent()
    upgraded = agents_routes._agents["default"]

    assert upgraded.system_prompt.startswith("prompt")
    assert "完成用户任务后，先用一句话明确说明已经完成了什么" in upgraded.system_prompt
    assert agents_routes.DEFAULT_AGENT_PROMPT_VERSION_TAG in upgraded.tags

    agents_routes._ensure_default_agent()
    assert upgraded.system_prompt == agents_routes._agents["default"].system_prompt
