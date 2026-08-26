"""系统默认 WebSearch Job Agent 回归测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.agents.system_agents import DEFAULT_WEBSEARCH_JOB_AGENT_ID
from src.project_resources import ProjectResourceAccess
from src.scheduler.firing_toolkit import assemble_scheduled_firing_toolkit_spec
from src.server.routes import agents as agents_routes
from src.storage import database
from src.tools import scheduled_job_tools


def test_default_websearch_job_agent_config_is_exact() -> None:
    """内置配置使用稳定 ID、指定模型和两个必注入网络工具。"""

    agent = agents_routes._build_default_websearch_job_agent()

    assert agent.id == DEFAULT_WEBSEARCH_JOB_AGENT_ID
    assert agent.name == "WebSearch"
    assert agent.description == "能够联网搜索的agent"
    assert agent.system_prompt == "请根据用户需要完成定时任务"
    assert agent.model_config_data.primary == "deepseek-v4-flash"
    assert agent.model_config_data.fallback == []
    assert agent.role == "job"
    assert [item.model_dump() for item in agent.job_toolkit.required] == [
        {"kind": "builtin", "id": "WebFetch"},
        {"kind": "builtin", "id": "HttpRequest"},
    ]
    assert agent.job_toolkit.optional == []
    assert agent.guardrails.network_enabled is True
    assert agent.guardrails.human_approval is False
    assert agent.guardrails.tool_permission_profile == "trusted_tools"
    assert agent.guardrails.tool_permission_mode == "bypass"
    assert agent.system_managed is True
    assert agent.model_dump(by_alias=True)["systemManaged"] is True


def test_ensure_default_websearch_job_agent_is_idempotent(monkeypatch) -> None:
    """历史库缺失时只补一次，不按名称重复创建。"""

    saved: list[dict] = []
    monkeypatch.setattr(agents_routes, "_agents", {})
    monkeypatch.setattr(agents_routes, "upsert_agent", saved.append)

    agents_routes._ensure_default_websearch_job_agent()
    created = agents_routes._agents[DEFAULT_WEBSEARCH_JOB_AGENT_ID]
    agents_routes._ensure_default_websearch_job_agent()

    assert len(saved) == 1
    assert agents_routes._agents[DEFAULT_WEBSEARCH_JOB_AGENT_ID] is created


def test_load_agents_backfills_system_job_agent_in_existing_database(
    tmp_path,
    monkeypatch,
) -> None:
    """真实 SQLite 加载链路保留历史 Agent，并补齐两个系统 Agent。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    monkeypatch.setattr(agents_routes, "_agents", {})
    monkeypatch.setattr(agents_routes, "migrate_json_list_file", lambda *_args: None)
    database.upsert_agent({
        "id": "existing-agent",
        "name": "Existing",
        "system_prompt": "prompt",
        "workspaceId": "default",
    })

    agents_routes._load_agents()

    assert set(agents_routes._agents) == {
        "existing-agent",
        "default",
        DEFAULT_WEBSEARCH_JOB_AGENT_ID,
    }
    assert {item["id"] for item in database.list_agents()} == set(agents_routes._agents)


def test_system_agents_are_visible_in_every_workspace(monkeypatch) -> None:
    """两个系统 Agent 跨 Workspace 可见，普通 Agent 仍按归属过滤。"""

    default_agent = agents_routes._build_default_agent()
    websearch_agent = agents_routes._build_default_websearch_job_agent()
    local_agent = websearch_agent.model_copy(update={
        "id": "workspace-job",
        "workspace_id": "workspace-b",
    })
    other_agent = websearch_agent.model_copy(update={
        "id": "other-job",
        "workspace_id": "workspace-a",
    })
    monkeypatch.setattr(agents_routes, "_agents", {
        default_agent.id: default_agent,
        websearch_agent.id: websearch_agent,
        local_agent.id: local_agent,
        other_agent.id: other_agent,
    })

    agents = asyncio.run(agents_routes.list_agents(workspace_id="workspace-b"))

    assert [agent.id for agent in agents] == [
        "default",
        DEFAULT_WEBSEARCH_JOB_AGENT_ID,
        "workspace-job",
    ]


def test_system_websearch_job_agent_cannot_be_deleted(monkeypatch) -> None:
    """系统 Job Agent 与默认 Chat Agent 使用同一删除保护。"""

    agent = agents_routes._build_default_websearch_job_agent()
    monkeypatch.setattr(agents_routes, "_agents", {agent.id: agent})

    with pytest.raises(Exception) as exc_info:
        asyncio.run(agents_routes.delete_agent(agent.id))

    assert getattr(exc_info.value, "status_code", None) == 403
    assert agents_routes._agents[agent.id] is agent


def test_system_websearch_job_agent_keeps_unattended_invariants(monkeypatch) -> None:
    """更新可改业务配置，但不能改角色、关闭网络或恢复人工审批。"""

    agent = agents_routes._build_default_websearch_job_agent()
    monkeypatch.setattr(agents_routes, "_agents", {agent.id: agent})
    monkeypatch.setattr(agents_routes, "upsert_agent", lambda _data: None)
    monkeypatch.setattr(agents_routes, "invalidate_agent", lambda _agent_id: None)

    updated = asyncio.run(agents_routes.update_agent(
        agent.id,
        agents_routes.AgentUpdate(
            name="联网任务",
            role="chat",
            guardrails=agents_routes.Guardrails(
                network_enabled=False,
                human_approval=True,
                toolPermissionProfile="ask_every_time",
                toolPermissionMode="ask_every_time",
            ),
        ),
    ))

    assert updated.name == "联网任务"
    assert updated.role == "job"
    assert updated.guardrails.network_enabled is True
    assert updated.guardrails.human_approval is False
    assert updated.guardrails.tool_permission_profile == "trusted_tools"
    assert updated.guardrails.tool_permission_mode == "bypass"


def test_system_job_agent_does_not_require_project_binding() -> None:
    """只豁免系统 Agent 本身，不向白名单加入其他资源。"""

    access = ProjectResourceAccess(
        workspace_id="workspace-b",
        project_id="project-b",
        agent_ids=frozenset(),
        skill_ids=frozenset(),
        mcp_server_ids=frozenset(),
        http_tool_ids=frozenset(),
    )

    access.require_agent(DEFAULT_WEBSEARCH_JOB_AGENT_ID)
    with pytest.raises(ValueError):
        access.require_agent("ordinary-job-agent")


def test_system_job_agent_required_network_tools_reach_firing_preview() -> None:
    """空能力合同也不能裁掉系统 Job Agent 的必注入工具。"""

    agent = agents_routes._build_default_websearch_job_agent()
    job = SimpleNamespace(
        agent_id=agent.id,
        capability_contract={},
        tool_scope={},
    )

    spec = assemble_scheduled_firing_toolkit_spec(job, agent=agent)

    assert spec["tool_scope"]["allow_web"] is True
    assert spec["tool_scope"]["allow_http"] is True
    assert "WebFetch" in spec["firing_tools_preview"]
    assert "HttpRequest" in spec["firing_tools_preview"]
    assert "FinalAnswerStart" in spec["firing_tools_preview"]


def test_job_agent_catalog_filters_other_workspaces(monkeypatch) -> None:
    """Chat 目录始终含系统 Job Agent，但不暴露其他 Workspace 的普通 Agent。"""

    system_agent = agents_routes._build_default_websearch_job_agent()
    local_agent = system_agent.model_copy(update={
        "id": "local-job",
        "name": "Local",
        "workspace_id": "workspace-b",
    })
    other_agent = system_agent.model_copy(update={
        "id": "other-job",
        "name": "Other",
        "workspace_id": "workspace-a",
    })
    monkeypatch.setattr(agents_routes, "_agents", {
        system_agent.id: system_agent,
        local_agent.id: local_agent,
        other_agent.id: other_agent,
    })

    line = scheduled_job_tools._job_agent_catalog_line("workspace-b")

    assert f"WebSearch ({DEFAULT_WEBSEARCH_JOB_AGENT_ID})" in line
    assert "Local (local-job)" in line
    assert "Other (other-job)" not in line
