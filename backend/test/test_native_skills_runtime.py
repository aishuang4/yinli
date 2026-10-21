"""AgentScope 原生 Skills 运行时回归测试。"""

from __future__ import annotations

import asyncio
import os

from src.agents.factory import (
    GuardedBash,
    RuntimePathSkillLoader,
    _bash_error_chunk,
    _create_default_toolkit,
    _decode_windows_shell_output,
    _resolve_windows_command_shell,
    _windows_utf8_command,
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


def test_scheduled_preloaded_toolkit_does_not_register_skill_viewer(tmp_path) -> None:
    package = _write_skill(tmp_path)
    toolkit = _create_default_toolkit(skill_paths=[package], register_skill_catalog=False)

    instructions = asyncio.run(toolkit.get_skill_instructions())
    tool_names = asyncio.run(_tool_names(toolkit))

    assert instructions is None
    assert "Skill" not in tool_names


def test_skill_loading_mode_changes_agent_cache_key(tmp_path) -> None:
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
    interactive = build_runtime_agent_cache_key(**kwargs)
    scheduled = build_runtime_agent_cache_key(**kwargs, skill_loading_mode="scheduled_preloaded")

    assert interactive != scheduled


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


def test_windows_bash_uses_utf8_code_page_and_decodes_legacy_output() -> None:
    """Windows Bash 应统一 UTF-8，并兼容未跟随代码页的 GBK 旧程序。"""

    assert _windows_utf8_command("dir") == "chcp 65001 >nul 2>&1 & dir"
    assert _decode_windows_shell_output("中文目录".encode("utf-8")) == "中文目录"
    assert _decode_windows_shell_output("旧程序输出".encode("gbk")) == "旧程序输出"


def test_local_bash_streams_output_before_process_exit(tmp_path) -> None:
    """本地 Bash 必须在进程退出前交付 stdout chunk。"""

    async def run_case():
        tool = GuardedBash([], timeout_seconds=5, cwd=str(tmp_path))
        stream = tool.call("printf 'first\\n'; sleep 0.1; printf 'second\\n'")
        first = await anext(stream)
        rest = [chunk async for chunk in stream]
        return first, rest

    first, rest = asyncio.run(run_case())

    assert first.content[0].text == "first\n"
    assert first.is_last is False
    assert any("second\n" in chunk.content[0].text for chunk in rest)
    assert rest[-1].is_last is True


def test_local_bash_cancellation_kills_child_processes(tmp_path) -> None:
    """取消 Bash 工具时必须终止 shell 派生进程，不能继续产生副作用。"""

    marker = tmp_path / "should-not-exist"

    async def run_case():
        tool = GuardedBash([], timeout_seconds=5, cwd=str(tmp_path))

        async def consume():
            async for _ in tool.call(f"sleep 0.4; touch '{marker}'"):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.45)

    asyncio.run(run_case())

    assert not marker.exists()


def test_runtime_skill_paths_reject_paths_outside_package_root(tmp_path, monkeypatch) -> None:
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    inside = _write_skill(packages_root, "inside")
    outside = _write_skill(tmp_path, "outside")
    monkeypatch.setattr("src.skills.registry.skill_packages_root", lambda: packages_root)
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

    assert any(event.event_type == "SKILL_STARTED" for event in started)
    assert any(event.event_type == "SKILL_CONTEXT" for event in loaded)
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
