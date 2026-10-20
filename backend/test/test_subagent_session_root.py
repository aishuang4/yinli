"""Subagent 檔案落在 Session root，子任務只消費父 snapshot 子集。"""

from src.agents.runtime_types import (
    AgentGuardrails,
    AgentModelOptions,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    RuntimeToolScope,
)
from src.config.runtime_paths import session_root
from src.subagents.manager import SubagentDelegationManager  # noqa: F401  先完成 runner 初始化，避免循環匯入
from src.subagents.models import SubagentDefinition
from src.subagents.runner import SubagentRunner, _subset_resources
from src.subagents.snapshot_subset import child_snapshot_subset
from src.subagents.task_store import SubagentTaskStore, reconcile_interrupted_file_tasks
from src.sessions.records import create_session, freeze_run_snapshot
from src.storage.database import ensure_db


def _definition() -> SubagentDefinition:
    return SubagentDefinition(
        name="explorer",
        description="Explore.",
        prompt="Explore.",
        scope="user",
        path="explorer.md",
        tools=["maps", "wiki"],
        skills=["alpha"],
    )


def test_result_path_and_reconcile_ignore_external_directory(tmp_path) -> None:
    ensure_db()
    create_session(session_id="ses-sub", agent_id="agent-shared")
    external = tmp_path / "external"
    external.mkdir()
    store = SubagentTaskStore(session_id="ses-sub", project_root=external)
    invocation_id = "subagent-" + "ab" * 16
    store.create(
        invocation_id=invocation_id,
        run_id="run-sub",
        tool_call_id="tool-1",
        parent_scope="single",
        parent_agent_id="agent-shared",
        parent_agent_name="Agent",
        definition=_definition(),
        task="Inspect.",
        expected_output="",
        context="",
        constraints=[],
    )
    result = store.result_path(invocation_id)
    assert store.status(invocation_id)["resultRef"] == f"subagents/{invocation_id}/result.md"
    assert result.is_relative_to(session_root("ses-sub"))
    assert external not in result.parents
    assert reconcile_interrupted_file_tasks([external]) == 1
    interrupted = store.status(invocation_id)
    assert interrupted["status"] == "interrupted"
    assert "restarted" in result.read_text(encoding="utf-8")


def test_child_receives_only_frozen_snapshot_subset() -> None:
    ensure_db()
    create_session(session_id="ses-sub-snap", agent_id="agent-shared")
    snapshot = freeze_run_snapshot(
        run_id="run-sub-snap",
        session_id="ses-sub-snap",
        working_directory="",
        allowed_roots=[],
        resources={"items": [
            {"resourceType": "skill", "resourceId": "alpha"},
            {"resourceType": "skill", "resourceId": "beta"},
            {"resourceType": "mcp", "resourceId": "maps"},
            {"resourceType": "mcp", "resourceId": "mail"},
            {"resourceType": "http_tool", "resourceId": "wiki"},
            {"resourceType": "knowledge", "resourceId": "crm"},
        ]},
        model_snapshot={},
        approval_snapshot={},
        agent_snapshot={"agentId": "agent-shared"},
        snapshot_id="snap-sub",
    )
    subset = child_snapshot_subset(snapshot, _definition())
    assert subset is not None
    assert {item["resourceId"] for item in subset["resources"]["items"]} == {"alpha", "maps", "wiki"}
    mcp, http = _subset_resources(
        [
            McpRuntimeSpec(name="maps", transport="sse", url="http://maps.local"),
            McpRuntimeSpec(name="mail", transport="sse", url="http://mail.local"),
        ],
        [
            KnowledgeToolRuntimeSpec(id="wiki", name="wiki", kind="http"),
            KnowledgeToolRuntimeSpec(id="crm", name="crm"),
        ],
        subset,
    )
    assert [item.name for item in mcp] == ["maps"]
    assert [item.id for item in http] == ["wiki"]
    runner = SubagentRunner(
        session_id="ses-sub-snap",
        run_id="run-sub-snap",
        parent_scope="single",
        parent_agent_id="agent-shared",
        parent_agent_name="Agent",
        model_name="model",
        fallback_names=[],
        model_options=AgentModelOptions(),
        guardrails=AgentGuardrails(),
        mcp_servers=[
            McpRuntimeSpec(name="maps", transport="sse", url="http://maps.local"),
            McpRuntimeSpec(name="mail", transport="sse", url="http://mail.local"),
        ],
        knowledge_tools=[
            KnowledgeToolRuntimeSpec(id="wiki", name="wiki", kind="http"),
            KnowledgeToolRuntimeSpec(id="crm", name="crm"),
        ],
        parent_tool_scope=RuntimeToolScope(),
        persist=False,
    )
    prepared = runner.prepare(_definition(), subset)
    loaded = {skill.id for skill in prepared.runtime_context.loaded_skills}
    assert "beta" not in loaded
    assert subset["id"] == snapshot["id"]
