"""AgentScope 原生 Skills 运行时回归测试。"""

from __future__ import annotations

import asyncio
import os

from src.agents.factory import (
    RuntimePathSkillLoader,
    _bash_error_chunk,
    _create_default_toolkit,
    _resolve_windows_command_shell,
)
from src.agents.runtime_types import AgentGuardrails, RuntimeToolScope
from src.security.permissions import ApprovalPolicy, UnifiedPermissionProfile
from src.server.routes.chat.runtime_builder import build_runtime_agent_cache_key
from src.server.routes.chat.leader_stream import LeaderStreamState, _skill_lifecycle_events
from src.server.routes.chat.models import ChatSkillRun, ChatToolRun
from src.server.routes.skills import Skill, resolve_runtime_skill_paths


def _write_skill(tmp_path, name: str = "workbook-inspector") -> str:
    package = tmp_path / name
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Inspect workbook structure.\n"
        "---\n\n"
        "Run `python scripts/inspect_workbook.py`.\n",
        encoding="utf-8",
    )
    return str(package)


def test_toolkit_registers_native_skill_loader_and_path_rules(tmp_path) -> None:
    package = _write_skill(tmp_path)
    toolkit = _create_default_toolkit(skill_paths=[package])

    instructions = asyncio.run(toolkit.get_skill_instructions())
    tool_names = asyncio.run(_tool_names(toolkit))

    assert instructions is not None
    assert "workbook-inspector" in instructions
    assert package in instructions
    assert "Resolve relative paths" in instructions
    assert "Skill" in tool_names


def test_runtime_path_loader_exposes_container_skill_directory(tmp_path) -> None:
    package = _write_skill(tmp_path)
    loader = RuntimePathSkillLoader(package, "/workspace/skills/workbook-inspector")

    skills = asyncio.run(loader.list_skills())

    assert skills[0].dir == "/workspace/skills/workbook-inspector"
    assert "scripts/inspect_workbook.py" in skills[0].markdown


def test_skill_cache_key_changes_when_skill_markdown_changes(tmp_path) -> None:
    package = _write_skill(tmp_path)
    kwargs = {
        "agent_id": "agent",
        "session_id": "session",
        "system_prompt": "prompt",
        "model_name": "model",
        "deep_thinking": False,
        "fallback_names": [],
        "used_skills": [],
        "skill_paths": [package],
        "mcp_servers": [],
        "knowledge_tools": [],
        "runtime_tool_scope": RuntimeToolScope(),
        "guardrail_cache_key": "guard",
    }
    first = build_runtime_agent_cache_key(**kwargs)
    skill_md = os.path.join(package, "SKILL.md")
    stat = os.stat(skill_md)
    os.utime(skill_md, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    second = build_runtime_agent_cache_key(**kwargs)

    assert first != second


def test_local_bash_does_not_depend_on_network_permission() -> None:
    guardrails = AgentGuardrails(
        permission_profile=UnifiedPermissionProfile(
            network_enabled=False,
            tool_policy=ApprovalPolicy.TRUSTED_TOOLS,
        ),
    )
    toolkit = _create_default_toolkit(
        guardrails=guardrails,
        tool_scope=RuntimeToolScope(allow_bash=True, allow_http=True, allow_web=True),
    )

    tool_names = asyncio.run(_tool_names(toolkit))

    assert "Bash" in tool_names
    assert "HttpRequest" not in tool_names
    assert "WebFetch" not in tool_names


def test_windows_command_shell_prefers_comspec_absolute_path() -> None:
    """Windows Bash 必须无需 Git Bash，并优先使用系统声明的 cmd.exe。"""

    command_shell = _resolve_windows_command_shell(
        {"ComSpec": r"C:\\Windows\\System32\\cmd.exe"},
        path_exists=lambda value: value.endswith("cmd.exe"),
        command_lookup=lambda _name: None,
    )

    assert command_shell == r"C:\\Windows\\System32\\cmd.exe"


def test_bash_failure_chunk_always_contains_error_detail() -> None:
    """Windows 子进程异常不能在对话界面退化为空白 Error。"""

    chunk = _bash_error_chunk("dir", "cmd.exe not found")

    assert "Error:\ncmd.exe not found" in chunk.content[0].text


def test_runtime_skill_paths_reject_paths_outside_package_root(tmp_path, monkeypatch) -> None:
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    inside = _write_skill(packages_root, "inside")
    outside = _write_skill(tmp_path, "outside")
    monkeypatch.setattr("src.server.routes.skills.SKILL_PACKAGES_DIR", str(packages_root))
    skills = [
        _skill("inside", inside),
        _skill("outside", outside),
    ]

    assert resolve_runtime_skill_paths(skills) == [inside]


def test_skill_lifecycle_starts_only_after_viewer_call_and_loads_context() -> None:
    detail = {
        "id": "workbook-inspector",
        "name": "workbook-inspector",
        "description": "Inspect workbook structure.",
        "trigger": "",
        "content": "",
        "resourcePath": "/skills/workbook-inspector",
        "resources": ["SKILL.md"],
    }
    state = LeaderStreamState(available_skill_details=[detail])
    skill_runs: list[ChatSkillRun] = []

    started = _skill_lifecycle_events(
        event_type="TOOL_CALL_START",
        event_data={"tool_call_id": "call-1", "tool_call_name": "Skill"},
        state=state,
        skill_runs=skill_runs,
        session_id="session",
        persist=False,
        agent_id="agent",
        agent_name="Agent",
    )
    state.assistant_tool_calls = [
        ChatToolRun(
            id="call-1",
            name="Skill",
            args='{"skill":"workbook-inspector"}',
            status="running",
        ),
    ]
    loaded = _skill_lifecycle_events(
        event_type="TOOL_RESULT_END",
        event_data={"tool_call_id": "call-1", "state": "success"},
        state=state,
        skill_runs=skill_runs,
        session_id="session",
        persist=False,
        agent_id="agent",
        agent_name="Agent",
    )

    assert any("SKILL_STARTED" in event for event in started)
    assert any("SKILL_CONTEXT" in event for event in loaded)
    assert [skill.id for skill in skill_runs] == ["workbook-inspector"]


def _skill(skill_id: str, path: str) -> Skill:
    return Skill.model_validate({
        "id": skill_id,
        "name": skill_id,
        "description": "test",
        "category": "test",
        "resourcePath": path,
        "resources": ["SKILL.md"],
        "status": "installed",
        "enabled": True,
        "updatedAt": "2026-07-20T00:00:00+00:00",
    })


async def _tool_names(toolkit) -> list[str]:
    schemas = await toolkit.get_tool_schemas()
    return [
        item.get("function", {}).get("name") or item.get("name")
        for item in schemas
    ]
