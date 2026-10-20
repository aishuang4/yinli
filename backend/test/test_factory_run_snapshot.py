"""兩個 Session 共用同一個 Agent 時，factory 仍使用各自凍結的目錄與資源。"""

import os

from src.agents.runtime_types import (
    AgentGuardrails,
    AgentModelOptions,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    RuntimeToolScope,
)
from src.runtime.agent_spec import RuntimeModelConfig, build_main_agent_runtime_spec
from src.sessions.records import create_session, freeze_run_snapshot, get_run_snapshot
from src.storage.database import _connect, _lock, ensure_db


def _spec(session_id: str, snapshot: dict, skill_paths: list[str]):
    return build_main_agent_runtime_spec(
        agent_id="agent-shared",
        agent_name="共用助手",
        session_id=session_id,
        system_prompt="同一份提示",
        model_config=RuntimeModelConfig(
            model_name="model",
            fallback_names=[],
            model_options=AgentModelOptions(),
        ),
        deep_thinking=False,
        used_skills=[],
        skill_paths=skill_paths,
        guardrails=AgentGuardrails(),
        mcp_servers=[
            McpRuntimeSpec(name="maps", transport="sse", url="http://maps.local"),
            McpRuntimeSpec(name="mail", transport="sse", url="http://mail.local"),
        ],
        knowledge_tools=[
            KnowledgeToolRuntimeSpec(id="wiki", name="wiki", kind="legacy_knowledge"),
            KnowledgeToolRuntimeSpec(id="crm", name="crm", kind="http"),
        ],
        runtime_tool_scope=RuntimeToolScope(),
        guardrail_cache_key="guardrails",
        workspace_dir="/unused",
        run_snapshot=snapshot,
    )


def test_same_agent_keeps_separate_snapshot_factory_inputs(tmp_path) -> None:
    ensure_db()
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    skills = tmp_path / "skills"
    alpha = skills / "alpha"
    beta = skills / "beta"
    other = skills / "other"
    for directory in (alpha, beta, other):
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text("---\nname: skill\n---\n", encoding="utf-8")
    create_session(session_id="ses-left", agent_id="agent-shared", working_directory=str(left))
    create_session(session_id="ses-right", agent_id="agent-shared", working_directory=str(right))
    skill_paths = [str(alpha), str(beta), str(other)]
    left_snapshot = freeze_run_snapshot(
        run_id="run-left",
        session_id="ses-left",
        working_directory=str(left),
        allowed_roots=[{"normalizedPath": str(left), "accessMode": "read_write"}],
        resources={"items": [
            {"resourceType": "skill", "resourceId": "alpha"},
            {"resourceType": "mcp", "resourceId": "maps"},
            {"resourceType": "knowledge", "resourceId": "wiki"},
        ]},
        model_snapshot={},
        approval_snapshot={"mode": "manual"},
        agent_snapshot={"agentId": "agent-shared"},
        snapshot_id="snap-left",
    )
    right_snapshot = freeze_run_snapshot(
        run_id="run-right",
        session_id="ses-right",
        working_directory=str(right),
        allowed_roots=[{"normalizedPath": str(right), "accessMode": "read_write"}],
        resources={"items": [
            {"resourceType": "skill", "resourceId": "beta"},
            {"resourceType": "mcp", "resourceId": "mail"},
            {"resourceType": "http_tool", "resourceId": "crm"},
        ]},
        model_snapshot={},
        approval_snapshot={"mode": "manual"},
        agent_snapshot={"agentId": "agent-shared"},
        snapshot_id="snap-right",
    )

    left_spec = _spec("ses-left", left_snapshot, skill_paths)
    right_spec = _spec("ses-right", right_snapshot, skill_paths)

    assert left_spec.workspace_dir == str(left.resolve())
    assert right_spec.workspace_dir == str(right.resolve())
    assert os.path.basename(left_spec.skill_paths[0]) == "alpha"
    assert os.path.basename(right_spec.skill_paths[0]) == "beta"
    assert [item.name for item in left_spec.mcp_servers] == ["maps"]
    assert [item.name for item in right_spec.mcp_servers] == ["mail"]
    assert [item.id for item in left_spec.knowledge_tools] == ["wiki"]
    assert [item.id for item in right_spec.knowledge_tools] == ["crm"]
    assert left_spec.guardrails.filesystem_scope == str(left.resolve())
    assert right_spec.guardrails.filesystem_scope == str(right.resolve())
    assert left_spec.runtime_agent_id != right_spec.runtime_agent_id
    assert "snapshot-snap-left" in left_spec.runtime_agent_id
    assert "snapshot-snap-right" in right_spec.runtime_agent_id

    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE chat_sessions SET working_directory = ? WHERE id = ?",
            (str(right), "ses-left"),
        )
        conn.commit()
    reloaded = _spec("ses-left", get_run_snapshot("run-left"), skill_paths)
    assert reloaded.workspace_dir == left_spec.workspace_dir
    assert reloaded.skill_paths == left_spec.skill_paths
    assert reloaded.runtime_agent_id == left_spec.runtime_agent_id
