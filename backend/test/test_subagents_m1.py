"""Subagent M1 定义加载、工具策略和审计回归测试。"""

import asyncio
import sqlite3
from pathlib import Path

from agentscope.event import ReplyEndEvent, TextBlockDeltaEvent, ToolCallStartEvent
from agentscope.message import Msg, ToolCallBlock, ToolCallState
from agentscope.state import AgentState

from src.agents.runtime_types import (
    AgentGuardrails,
    AgentModelOptions,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    RuntimeToolScope,
)
from src.security.permissions import ApprovalPolicy, UnifiedPermissionProfile
from src.server.routes.chat.hitl import _create_pending_interaction
from src.server.routes.chat.event_hub.projector import _subagent_context_detail
from src.server.routes.chat.leader_stream import _delegate_task_batch_error_results
from src.server.routes.chat.models import (
    ChatResumeRequest,
    PENDING_AGENTS,
    PENDING_INTERACTIONS,
    PENDING_SUBAGENT_PARENT_CONTEXTS,
)
from src.storage import database
from src.subagents.manager import SubagentDelegationManager
from src.subagents.loader import load_subagent_file
from src.subagents.models import SubagentCatalog, SubagentDefinition
from src.subagents.permission_policy import resolve_subagent_permission
from src.subagents.registry import load_subagent_catalog
from src.subagents.runner import (
    SubagentHitlRequired,
    SubagentRunResult,
    _build_child_system_prompt,
    _child_tool_scope,
    _filter_mcp_servers,
)
from src.subagents.runtime_context import SubagentRuntimeContext
from src.subagents.tool_policy import build_subagent_tool_policy
from src.tools.delegate_task import DelegateTask


def test_load_subagent_frontmatter_parses_yaml_and_body(tmp_path: Path) -> None:
    path = tmp_path / "code-reviewer.md"
    path.write_text(
        """---
name: code-reviewer
description: Reviews code after changes.
tools: Read, Grep, Glob
disallowedTools:
  - Write
model: inherit
permissionMode: default
maxTurns: 12
skills:
  - review-style
background: true
---

你是代码审查专家。
""",
        encoding="utf-8",
    )

    definition, error = load_subagent_file(path, scope="project")

    assert error is None
    assert definition is not None
    assert definition.name == "code-reviewer"
    assert definition.tools == ["Read", "Grep", "Glob"]
    assert definition.disallowed_tools == ["Write"]
    assert definition.max_turns == 12
    assert definition.skills == ["review-style"]
    assert definition.prompt == "你是代码审查专家。"
    assert definition.metadata["recognizedUnsupportedFields"] == ["background"]


def test_load_subagent_rejects_unknown_permission_mode(tmp_path: Path) -> None:
    path = tmp_path / "unsafe-typo.md"
    path.write_text(
        """---
name: unsafe-typo
description: Should not silently gain parent permissions.
permissionMode: plna
---
Prompt.
""",
        encoding="utf-8",
    )

    definition, error = load_subagent_file(path, scope="project")

    assert definition is None
    assert error is not None
    assert "permissionMode" in error.message


def test_registry_project_scope_overrides_user_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project_dir = workspace / ".agents" / "subagents"
    user_dir = tmp_path / "user-subagents"
    project_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    (user_dir / "reviewer.md").write_text(
        """---
name: reviewer
description: User reviewer.
---
User prompt.
""",
        encoding="utf-8",
    )
    (project_dir / "reviewer.md").write_text(
        """---
name: reviewer
description: Project reviewer.
---
Project prompt.
""",
        encoding="utf-8",
    )

    catalog = load_subagent_catalog(workspace_dir=str(workspace), user_dir=str(user_dir))

    assert catalog.definitions["reviewer"].description == "Project reviewer."
    assert catalog.definitions["reviewer"].scope == "project"
    assert catalog.warnings


def test_registry_reports_invalid_yaml_and_ignores_duplicate_same_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project_dir = workspace / ".agents" / "subagents"
    nested_dir = project_dir / "nested"
    nested_dir.mkdir(parents=True)
    (project_dir / "reviewer.md").write_text(
        """---
name: reviewer
description: First reviewer.
---
First prompt.
""",
        encoding="utf-8",
    )
    (nested_dir / "reviewer.md").write_text(
        """---
name: reviewer
description: Duplicate reviewer.
---
Duplicate prompt.
""",
        encoding="utf-8",
    )
    (project_dir / "broken.md").write_text(
        """---
name: broken
description: Broken YAML
tools: [Read
---
Prompt.
""",
        encoding="utf-8",
    )

    catalog = load_subagent_catalog(workspace_dir=str(workspace), user_dir=str(tmp_path / "missing-user"))

    assert catalog.definitions["reviewer"].description in {"First reviewer.", "Duplicate reviewer."}
    assert len(catalog.definitions) == 1
    assert catalog.errors
    assert any("重复 Subagent name" in warning for warning in catalog.warnings)


def test_tool_policy_inherits_parent_pool_then_applies_deny_and_allow(tmp_path: Path) -> None:
    path = tmp_path / "safe.md"
    path.write_text(
        """---
name: safe
description: Safe research.
tools: Read, Grep, Bash, Write
disallowedTools: Write
---
Prompt.
""",
        encoding="utf-8",
    )
    definition, error = load_subagent_file(path, scope="project")
    assert error is None
    assert definition is not None

    policy = build_subagent_tool_policy(
        definition,
        parent_tool_scope=RuntimeToolScope(allow_bash=True, allow_http=False, allow_mcp=False, allow_knowledge=False),
        parent_guardrails=AgentGuardrails(
            permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.TRUSTED_TOOLS)
        ),
    )

    assert "Read" in policy.effective_tool_names
    assert "Grep" in policy.effective_tool_names
    assert "Bash" in policy.effective_tool_names
    assert "FinalAnswerStart" in policy.effective_tool_names
    assert "Write" not in policy.effective_tool_names
    assert "AskUser" not in policy.effective_tool_names
    assert "DelegateTask" not in policy.effective_tool_names


def test_tool_policy_keeps_internal_final_answer_marker_when_tools_are_restricted(tmp_path: Path) -> None:
    path = tmp_path / "explorer.md"
    path.write_text(
        """---
name: explorer
description: Read-only explorer.
tools: Read, Grep, Glob
disallowedTools:
  - FinalAnswerStart
---
Prompt.
""",
        encoding="utf-8",
    )
    definition, error = load_subagent_file(path, scope="project")
    assert error is None
    assert definition is not None

    policy = build_subagent_tool_policy(
        definition,
        parent_tool_scope=RuntimeToolScope(),
        parent_guardrails=AgentGuardrails(
            permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.TRUSTED_TOOLS)
        ),
    )

    assert policy.effective_tool_names == {"Read", "Grep", "Glob", "FinalAnswerStart"}
    assert "FinalAnswerStart" not in policy.denied_tool_names


def test_tool_policy_applies_mcp_server_patterns_to_runtime_tools(tmp_path: Path) -> None:
    path = tmp_path / "mcp-worker.md"
    path.write_text(
        """---
name: mcp-worker
description: Uses one MCP server.
tools: mcp__github
disallowedTools: mcp__linear
---
Prompt.
""",
        encoding="utf-8",
    )
    definition, error = load_subagent_file(path, scope="project")
    assert error is None
    assert definition is not None

    policy = build_subagent_tool_policy(
        definition,
        parent_tool_scope=RuntimeToolScope(allow_mcp=True),
        parent_guardrails=AgentGuardrails(
            permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.TRUSTED_TOOLS)
        ),
        mcp_servers=[
            McpRuntimeSpec(name="github", transport="sse", url="https://example.test/github", enable_tools=["search", "read"]),
            McpRuntimeSpec(name="linear", transport="sse", url="https://example.test/linear", enable_tools=["issue"]),
        ],
    )

    assert "mcp__github__search" in policy.effective_tool_names
    assert "mcp__github__read" in policy.effective_tool_names
    assert "mcp__linear__issue" not in policy.effective_tool_names


def test_tool_policy_can_deny_all_mcp_tools(tmp_path: Path) -> None:
    path = tmp_path / "no-mcp.md"
    path.write_text(
        """---
name: no-mcp
description: Denies MCP.
disallowedTools: mcp__*
---
Prompt.
""",
        encoding="utf-8",
    )
    definition, error = load_subagent_file(path, scope="project")
    assert error is None
    assert definition is not None

    policy = build_subagent_tool_policy(
        definition,
        parent_tool_scope=RuntimeToolScope(allow_mcp=True),
        parent_guardrails=AgentGuardrails(
            permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.TRUSTED_TOOLS)
        ),
        mcp_servers=[
            McpRuntimeSpec(name="github", transport="sse", url="https://example.test/github", enable_tools=["search"]),
        ],
    )

    assert not any(tool.startswith("mcp__") for tool in policy.effective_tool_names)


def test_tool_policy_uses_real_knowledge_tool_names(tmp_path: Path) -> None:
    path = tmp_path / "knowledge-worker.md"
    path.write_text(
        """---
name: knowledge-worker
description: Uses one knowledge tool.
tools: KnowledgeBaseSearch_Product_Docs
---
Prompt.
""",
        encoding="utf-8",
    )
    definition, error = load_subagent_file(path, scope="project")
    assert error is None
    assert definition is not None

    policy = build_subagent_tool_policy(
        definition,
        parent_tool_scope=RuntimeToolScope(allow_knowledge=True),
        parent_guardrails=AgentGuardrails(
            permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.TRUSTED_TOOLS)
        ),
        knowledge_tools=[
            KnowledgeToolRuntimeSpec(id="docs", name="Product Docs", url="https://example.test/search"),
            KnowledgeToolRuntimeSpec(id="api", name="API Docs", url="https://example.test/api"),
        ],
    )

    assert "KnowledgeBaseSearch_Product_Docs" in policy.effective_tool_names
    assert "KnowledgeBaseSearch_API_Docs" not in policy.effective_tool_names


def test_child_scope_keeps_custom_knowledge_and_http_tools_enabled() -> None:
    parent_scope = RuntimeToolScope(allow_knowledge=True)

    knowledge_scope = _child_tool_scope(
        parent_scope,
        {"KnowledgeBaseSearch_Product_Docs", "FinalAnswerStart"},
    )
    http_scope = _child_tool_scope(
        parent_scope,
        {"HttpTool_Product_API", "FinalAnswerStart"},
    )

    assert knowledge_scope.allow_knowledge is True
    assert http_scope.allow_knowledge is True


def test_batch_delegate_task_returns_one_error_per_call() -> None:
    results = _delegate_task_batch_error_results([
        {"id": "delegate-1", "name": "DelegateTask"},
        {"id": "delegate-2", "name": "DelegateTask"},
    ])

    assert [result.id for result in results] == ["delegate-1", "delegate-2"]
    assert all(str(result.state).lower() == "error" for result in results)
    assert all("exactly one delegation" in result.output[0].text for result in results)


def test_subagent_context_projection_uses_runtime_audit_field_names() -> None:
    detail = _subagent_context_detail({
        "effectiveTools": ["Read", "Grep"],
        "loadedSkills": [{"id": "review", "name": "Review Style"}],
        "permission": {"effectivePermissionMode": "plan"},
    })

    assert detail == "可用工具 2 个，注入 Skill 1 个，权限模式 plan"


def test_action_subagent_tools_are_available_with_parent_permission(tmp_path: Path) -> None:
    path = tmp_path / "debugger.md"
    path.write_text(
        """---
name: debugger
description: Debug and apply scoped fixes.
tools: Read, Grep, Glob, Bash, Write, Edit, HttpRequest
permissionMode: acceptEdits
---
Prompt.
""",
        encoding="utf-8",
    )
    definition, error = load_subagent_file(path, scope="project")
    assert error is None
    assert definition is not None
    parent_guardrails = AgentGuardrails(
        permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.CONFIRM_DESTRUCTIVE)
    )
    permission = resolve_subagent_permission(definition, parent_guardrails)

    policy = build_subagent_tool_policy(
        definition,
        parent_tool_scope=RuntimeToolScope(allow_bash=True, allow_http=True),
        parent_guardrails=parent_guardrails,
        child_guardrails=permission.guardrails,
    )

    assert {"Read", "Grep", "Glob", "Write", "Edit", "Bash", "HttpRequest", "FinalAnswerStart"} <= policy.effective_tool_names
    assert permission.effective_policy == ApprovalPolicy.CONFIRM_DESTRUCTIVE


def test_action_subagent_tools_are_clamped_by_parent_read_only(tmp_path: Path) -> None:
    path = tmp_path / "debugger.md"
    path.write_text(
        """---
name: debugger
description: Debug and apply scoped fixes.
tools: Read, Bash, Write, Edit, HttpRequest
permissionMode: acceptEdits
---
Prompt.
""",
        encoding="utf-8",
    )
    definition, error = load_subagent_file(path, scope="project")
    assert error is None
    assert definition is not None
    parent_guardrails = AgentGuardrails(
        permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.READ_ONLY)
    )
    permission = resolve_subagent_permission(definition, parent_guardrails)

    policy = build_subagent_tool_policy(
        definition,
        parent_tool_scope=RuntimeToolScope(allow_bash=True, allow_http=True),
        parent_guardrails=parent_guardrails,
        child_guardrails=permission.guardrails,
    )

    assert "Read" in policy.effective_tool_names
    assert "FinalAnswerStart" in policy.effective_tool_names
    assert "Write" not in policy.effective_tool_names
    assert "Edit" not in policy.effective_tool_names
    assert "Bash" not in policy.effective_tool_names
    assert "HttpRequest" not in policy.effective_tool_names
    assert permission.clamped_by_parent is True
    assert permission.effective_policy == ApprovalPolicy.READ_ONLY


def test_filter_mcp_servers_keeps_only_effective_mcp_tools() -> None:
    filtered = _filter_mcp_servers(
        [
            McpRuntimeSpec(name="github", transport="sse", url="https://example.test/github", enable_tools=["search", "read"]),
            McpRuntimeSpec(name="linear", transport="sse", url="https://example.test/linear", enable_tools=["issue"]),
        ],
        {"mcp__github__search"},
    )

    assert len(filtered) == 1
    assert filtered[0].name == "github"
    assert filtered[0].enable_tools == ["search"]


def test_filter_mcp_servers_can_narrow_server_without_parent_tool_list() -> None:
    filtered = _filter_mcp_servers(
        [
            McpRuntimeSpec(name="github", transport="sse", url="https://example.test/github", enable_tools=[]),
        ],
        {"mcp__github__search"},
    )

    assert len(filtered) == 1
    assert filtered[0].name == "github"
    assert filtered[0].enable_tools == ["search"]


def test_delegation_manager_rejects_unknown_subagent() -> None:
    manager = SubagentDelegationManager(
        catalog=SubagentCatalog(definitions={}),
        session_id="session-1",
        run_id="run-1",
        parent_scope="single",
        parent_agent_id="agent-1",
        parent_agent_name="Agent",
        model_name="deepseek-chat",
        fallback_names=[],
        model_options=AgentModelOptions(),
        guardrails=AgentGuardrails(
            permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.TRUSTED_TOOLS)
        ),
        mcp_servers=[],
        knowledge_tools=[],
        parent_tool_scope=RuntimeToolScope(),
        persist=False,
    )

    try:
        asyncio.run(manager.delegate(subagent_name="missing", task="Do work."))
    except ValueError as exc:
        assert "Unknown subagent" in str(exc)
    else:
        raise AssertionError("Unknown Subagent should be rejected before any invocation is created.")


def test_delegation_manager_emits_started_and_completed_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    definition = SubagentDefinition(
        name="reviewer",
        description="Review code.",
        prompt="You review code.",
        scope="project",
        path=".agents/subagents/reviewer.md",
    )
    manager = SubagentDelegationManager(
        catalog=SubagentCatalog(definitions={"reviewer": definition}),
        session_id="session-1",
        run_id="run-1",
        parent_scope="single",
        parent_agent_id="agent-1",
        parent_agent_name="Agent",
        model_name="deepseek-chat",
        fallback_names=[],
        model_options=AgentModelOptions(),
        guardrails=AgentGuardrails(
            permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.TRUSTED_TOOLS)
        ),
        mcp_servers=[],
        knowledge_tools=[],
        parent_tool_scope=RuntimeToolScope(),
        persist=False,
    )

    class FakeRunner:
        def prepare(self, definition):
            runtime_context = SubagentRuntimeContext(memory_agent_id=f"subagent:{definition.name}")
            permission = resolve_subagent_permission(definition, manager.guardrails)

            class Prepared:
                audit_payload = runtime_context.audit_payload(definition)
                audit_payload["permission"] = permission.audit_payload()

                def __init__(self, runtime_context, permission):
                    self.runtime_context = runtime_context
                    self.permission = permission

            return Prepared(runtime_context, permission)

        async def run(self, **kwargs):
            return SubagentRunResult(text="No blocking findings.", duration_ms=12)

    manager._runner = FakeRunner()  # noqa: SLF001

    result = asyncio.run(
        manager.delegate(
            subagent_name="reviewer",
            task="Review the latest diff.",
            tool_call_id="tool-call-reviewer",
        )
    )
    events = manager.drain_runtime_events()
    invocations = database.list_subagent_invocations(run_id="run-1")

    assert "No blocking findings." in result
    assert [event["type"] for event in events] == ["SUBAGENT_STARTED", "SUBAGENT_CONTEXT", "SUBAGENT_COMPLETED"]
    assert events[0]["payload"]["subagentName"] == "reviewer"
    assert events[0]["payload"]["toolCallId"] == "tool-call-reviewer"
    assert events[1]["payload"]["toolCallId"] == "tool-call-reviewer"
    assert events[2]["payload"]["toolCallId"] == "tool-call-reviewer"
    assert events[1]["payload"]["memoryAgentId"] == "subagent:reviewer"
    assert events[1]["payload"]["permission"]["effectivePermissionMode"] == "default"
    assert events[1]["payload"]["permission"]["effectiveToolPolicy"] == "trusted_tools"
    assert invocations[0]["status"] == "completed"
    assert invocations[0]["toolCallId"] == "tool-call-reviewer"


def test_delegation_manager_marks_subagent_waiting_on_hitl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    PENDING_INTERACTIONS.clear()
    PENDING_AGENTS.clear()
    definition = SubagentDefinition(
        name="debugger",
        description="Debug code.",
        prompt="You debug code.",
        scope="project",
        path=".agents/subagents/debugger.md",
    )
    manager = SubagentDelegationManager(
        catalog=SubagentCatalog(definitions={"debugger": definition}),
        session_id="session-1",
        run_id="run-1",
        parent_scope="single",
        parent_agent_id="agent-1",
        parent_agent_name="Agent",
        model_name="deepseek-chat",
        fallback_names=[],
        model_options=AgentModelOptions(),
        guardrails=AgentGuardrails(
            permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.CONFIRM_DESTRUCTIVE)
        ),
        mcp_servers=[],
        knowledge_tools=[],
        parent_tool_scope=RuntimeToolScope(),
        persist=False,
    )
    fake_agent = object()

    class FakeRunner:
        def prepare(self, definition):
            runtime_context = SubagentRuntimeContext(memory_agent_id=f"subagent:{definition.name}")
            permission = resolve_subagent_permission(definition, manager.guardrails)

            class Prepared:
                audit_payload = runtime_context.audit_payload(definition)
                audit_payload["permission"] = permission.audit_payload()

                def __init__(self, runtime_context, permission):
                    self.runtime_context = runtime_context
                    self.permission = permission

            return Prepared(runtime_context, permission)

        async def run(self, **kwargs):
            raise SubagentHitlRequired(
                "Subagent paused.",
                agent=fake_agent,
                interaction_type="REQUIRE_USER_CONFIRM",
                reply_id="reply-1",
                tool_calls=[
                    {
                        "type": "tool_call",
                        "id": "write-1",
                        "name": "Write",
                        "input": "{\"file_path\":\"tmp.txt\"}",
                        "state": "asking",
                    }
                ],
                partial_result="Need to edit tmp.txt.",
                duration_ms=34,
            )

    manager._runner = FakeRunner()  # noqa: SLF001

    result = asyncio.run(
        manager.delegate(
            subagent_name="debugger",
            task="Fix the file.",
            tool_call_id="delegate-call-1",
        )
    )
    events = manager.drain_runtime_events()
    invocations = database.list_subagent_invocations(run_id="run-1")

    assert "paused for human approval" in result
    assert [event["type"] for event in events] == ["SUBAGENT_STARTED", "SUBAGENT_CONTEXT", "SUBAGENT_WAITING_HUMAN"]
    assert events[2]["status"] == "waiting"
    interaction_id = events[2]["payload"]["interactionId"]
    assert interaction_id
    assert interaction_id in PENDING_INTERACTIONS
    assert PENDING_AGENTS[interaction_id] is fake_agent
    assert PENDING_INTERACTIONS[interaction_id].scope == "subagent"
    assert PENDING_INTERACTIONS[interaction_id].chat_data["invocationId"] == invocations[0]["id"]
    assert events[2]["payload"]["interactionType"] == "REQUIRE_USER_CONFIRM"
    assert events[2]["payload"]["replyId"] == "reply-1"
    assert events[2]["payload"]["toolCalls"][0]["name"] == "Write"
    assert invocations[0]["status"] == "waiting"
    assert invocations[0]["completedAt"] is None
    assert "parent DelegateTask is not automatically continued" in invocations[0]["error"]
    PENDING_INTERACTIONS.clear()
    PENDING_AGENTS.clear()


def test_resume_subagent_pending_interaction_completes_invocation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    PENDING_INTERACTIONS.clear()
    PENDING_AGENTS.clear()
    PENDING_SUBAGENT_PARENT_CONTEXTS.clear()
    invocation = database.create_subagent_invocation(
        {
            "run_id": "run-1",
            "session_id": "session-1",
            "tool_call_id": "delegate-call-1",
            "parent_scope": "single",
            "parent_agent_id": "agent-1",
            "parent_agent_name": "Agent",
            "definition_scope": "project",
            "definition_path": ".agents/subagents/debugger.md",
            "subagent_name": "debugger",
            "status": "waiting",
            "task": "Fix the file.",
            "expected_output": "Summary.",
        }
    )

    class FakeSubagent:
        async def reply_stream(self, _event):
            yield ToolCallStartEvent(
                reply_id="reply-1",
                tool_call_id="final-1",
                tool_call_name="FinalAnswerStart",
            )
            yield TextBlockDeltaEvent(
                reply_id="reply-1",
                block_id="text-1",
                delta="Fixed the file.",
            )
            yield ReplyEndEvent(
                session_id="session-1",
                reply_id="reply-1",
            )

    class FakeParentAgent:
        def __init__(self) -> None:
            self.resumed = False

        async def reply_stream(self, _event):
            self.resumed = True
            yield ToolCallStartEvent(
                reply_id="reply-parent",
                tool_call_id="final-parent",
                tool_call_name="FinalAnswerStart",
            )
            yield TextBlockDeltaEvent(
                reply_id="reply-parent",
                block_id="text-parent",
                delta="Parent continued.",
            )
            yield ReplyEndEvent(
                session_id="session-1",
                reply_id="reply-parent",
            )

    interaction = _create_pending_interaction(
        interaction_type="REQUIRE_USER_CONFIRM",
        agent=FakeSubagent(),
        session_id="session-1",
        agent_id="subagent:debugger",
        agent_name="Subagent debugger",
        model_name="deepseek-chat",
        reply_id="reply-1",
        tool_calls=[
            {
                "type": "tool_call",
                "id": "write-1",
                "name": "Write",
                "input": "{\"file_path\":\"tmp.txt\"}",
                "state": "asking",
            }
        ],
        message="Fix the file.",
        assistant_text="",
        skill_runs=[],
        scope="subagent",
        chat_data={
            "invocationId": invocation["id"],
            "toolCallId": "delegate-call-1",
            "subagentName": "debugger",
        },
        run_id="run-1",
        persist=False,
    )
    from src.server.routes.chat.leader_stream import LeaderStreamState

    parent_agent = FakeParentAgent()
    PENDING_SUBAGENT_PARENT_CONTEXTS[interaction.id] = {
        "agent": parent_agent,
        "reply_id": "reply-parent",
        "tool_call": {"id": "delegate-call-1", "name": "DelegateTask"},
        "state": LeaderStreamState(),
        "session_id": "session-1",
        "persist": False,
        "agent_id": "agent-1",
        "agent_name": "Agent",
        "model_name": "deepseek-chat",
        "guardrails": AgentGuardrails(),
        "message": "Fix the file.",
        "stream_scope": "single",
        "skill_runs": [],
        "plan_mode_manager": None,
        "runtime_event_sources": [],
        "sandbox": False,
    }

    async def collect_resume_events() -> str:
        from src.server.routes.chat.handlers.resume import resume_interaction

        response = await resume_interaction(
            ChatResumeRequest(interactionId=interaction.id, action="approve")
        )
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
        return "".join(chunks)

    output = asyncio.run(collect_resume_events())
    invocations = database.list_subagent_invocations(run_id="run-1")

    assert "SUBAGENT_COMPLETED" in output
    assert "Parent continued." in output
    assert "RUN_COMPLETED" in output
    assert parent_agent.resumed is True
    assert invocations[0]["status"] == "completed"
    assert invocations[0]["resultPreview"] == "Fixed the file."
    assert interaction.id not in PENDING_INTERACTIONS
    assert interaction.id not in PENDING_AGENTS
    assert interaction.id not in PENDING_SUBAGENT_PARENT_CONTEXTS


def test_delegate_task_passes_current_tool_call_id_from_agent_state() -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.tool_call_id = ""

        async def delegate(self, **kwargs):
            self.tool_call_id = kwargs["tool_call_id"]
            return "Subagent reviewer completed.\n\nSummary:\nDone."

    manager = FakeManager()
    tool = DelegateTask(manager)  # type: ignore[arg-type]
    state = AgentState(
        context=[
            Msg(
                role="assistant",
                name="Agent",
                content=[
                    ToolCallBlock(
                        id="delegate-call-1",
                        name="DelegateTask",
                        input='{"subagentName":"reviewer","task":"Review."}',
                        state=ToolCallState.ALLOWED,
                    )
                ],
            )
        ]
    )

    chunk = asyncio.run(
        tool(
            subagentName="reviewer",
            task="Review.",
            _agent_state=state,
        )
    )

    assert manager.tool_call_id == "delegate-call-1"
    assert chunk.state == "success"


def test_child_system_prompt_injects_subagent_skills_and_memory() -> None:
    definition = SubagentDefinition(
        name="researcher",
        description="Research with a loaded skill.",
        prompt="你是研究型 Subagent。",
        scope="project",
        path=".agents/subagents/researcher.md",
    )
    runtime_context = SubagentRuntimeContext(
        skill_prompt="## Loaded Skills\n### Skill 1: Review Style\nFollow the review checklist.",
        memory_prompt="# Memory\nAgent Markdown memory:\n## Summary\nPrefer concise findings.",
        memory_agent_id="subagent:researcher",
    )

    prompt = _build_child_system_prompt(definition, runtime_context)

    assert "你是研究型 Subagent。" in prompt
    assert "## Loaded Skills" in prompt
    assert "Follow the review checklist." in prompt
    assert "Agent Markdown memory" in prompt
    assert "subagent:researcher" not in prompt
    assert "必须调用 FinalAnswerStart" in prompt


def test_permission_mode_plan_restricts_child_to_read_only_tools() -> None:
    definition = SubagentDefinition(
        name="planner",
        description="Plan without edits.",
        prompt="Plan only.",
        scope="project",
        path=".agents/subagents/planner.md",
        tools=["Read", "Write", "Bash", "FinalAnswerStart"],
        permission_mode="plan",
    )
    parent_guardrails = AgentGuardrails(
        permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.TRUSTED_TOOLS)
    )
    permission = resolve_subagent_permission(definition, parent_guardrails)
    policy = build_subagent_tool_policy(
        definition,
        parent_tool_scope=RuntimeToolScope(allow_bash=True),
        parent_guardrails=parent_guardrails,
        child_guardrails=permission.guardrails,
    )

    assert permission.effective_policy == ApprovalPolicy.READ_ONLY
    assert permission.agentscope_mode.value == "explore"
    assert "Read" in policy.effective_tool_names
    assert "FinalAnswerStart" in policy.effective_tool_names
    assert "Write" not in policy.effective_tool_names
    assert "Bash" not in policy.effective_tool_names


def test_permission_mode_bypass_cannot_exceed_parent_read_only() -> None:
    definition = SubagentDefinition(
        name="unsafe",
        description="Requests broad permissions.",
        prompt="Try to edit.",
        scope="project",
        path=".agents/subagents/unsafe.md",
        tools=["Read", "Write", "Bash"],
        permission_mode="bypassPermissions",
    )
    parent_guardrails = AgentGuardrails(
        permission_profile=UnifiedPermissionProfile(tool_policy=ApprovalPolicy.READ_ONLY)
    )
    permission = resolve_subagent_permission(definition, parent_guardrails)
    policy = build_subagent_tool_policy(
        definition,
        parent_tool_scope=RuntimeToolScope(allow_bash=True),
        parent_guardrails=parent_guardrails,
        child_guardrails=permission.guardrails,
    )

    assert permission.clamped_by_parent is True
    assert permission.effective_policy == ApprovalPolicy.READ_ONLY
    assert permission.audit_payload()["requestedPermissionMode"] == "bypassPermissions"
    assert "Write" not in policy.effective_tool_names
    assert "Bash" not in policy.effective_tool_names


def test_subagent_invocation_audit_create_update_and_list(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)

    created = database.create_subagent_invocation(
        {
            "run_id": "run-audit",
            "session_id": "session-audit",
            "tool_call_id": "tool-call-1",
            "parent_scope": "single",
            "parent_agent_id": "agent-a",
            "parent_agent_name": "Agent A",
            "definition_scope": "project",
            "definition_path": ".agents/subagents/reviewer.md",
            "subagent_name": "reviewer",
            "task": "Review code.",
            "expected_output": "Findings.",
        }
    )
    updated = database.update_subagent_invocation(
        created["id"],
        status="completed",
        result_preview="No blocking findings.",
    )
    by_run = database.list_subagent_invocations(run_id="run-audit")

    assert updated is not None
    assert updated["status"] == "completed"
    assert updated["completedAt"]
    assert by_run[0]["id"] == created["id"]
    assert by_run[0]["subagentName"] == "reviewer"


def test_legacy_subagent_agent_columns_are_removed_on_db_startup(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "agent_workstation.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                avatar TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                system_prompt TEXT NOT NULL,
                model_config TEXT NOT NULL,
                mcp_tools TEXT NOT NULL,
                knowledge_tools TEXT NOT NULL DEFAULT '[]',
                skills TEXT NOT NULL,
                tags TEXT NOT NULL,
                plan_config TEXT NOT NULL DEFAULT '{}',
                guardrails TEXT NOT NULL,
                callable_agents TEXT NOT NULL,
                max_depth INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "_initialized", False)

    database.ensure_db()
    columns = {
        row[1]
        for row in sqlite3.connect(db_path).execute("PRAGMA table_info(agents)").fetchall()
    }

    assert "callable_agents" not in columns
    assert "max_depth" not in columns
