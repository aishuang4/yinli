"""运行后 Markdown 记忆反思测试。"""

from __future__ import annotations

import asyncio

from src.memory.extraction_agents import (
    MemoryReflectionAgentPayload,
    SkillDraftPayload,
    SkillEvolutionAgentPayload,
)
from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.reflection import (
    MemoryReflectionInput,
    is_manual_skill_generation_request,
    reflect_completed_run_memory,
    reflect_completed_run_memory_with_agents,
)
from src.memory.trace import memory_reflection_audit_payload
from src.memory.extraction_agents import _build_memory_reflection_system_prompt, _build_memory_reflection_user_prompt
from src.tools.memory_reflection import MemoryFileToolSet
from src.server.routes.chat.models import ChatToolRun
from src.storage import database


def test_reflection_writes_user_preference_memory(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="以后默认用中文回答，解释保持简洁。",
            assistant_text="好的。",
        ),
        store=store,
    )

    assert result.ignored_reason == ""
    assert result.updates[0].scope == "user"
    assert result.updates[0].section == "Stable Preferences"
    content = (tmp_path / "memory" / "users" / "user_memory.md").read_text(encoding="utf-8")
    assert "以后默认用中文回答，解释保持简洁。" in content
    assert "source: session-1" in content


def test_reflection_writes_agent_negative_memory(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="你以后不要再恢复旧 Plan Runtime。",
            assistant_text="明白。",
        ),
        store=store,
    )

    assert result.updates[0].scope == "agent"
    assert result.updates[0].section == "Negative Rules"
    content = (tmp_path / "memory" / "agents" / "agent-1" / "agent_memory.md").read_text(encoding="utf-8")
    assert "你以后不要再恢复旧 Plan Runtime。" in content


def test_reflection_writes_global_memory(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")

    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="整个工作台以后默认不要新增 memory runtime.kind。",
            assistant_text="收到。",
            workspace_id="workspace-1",
        ),
        store=store,
    )

    assert result.updates[0].scope == "global_preference"
    assert result.updates[0].section == "Negative Rules"
    content = (tmp_path / "memory" / "global" / "workspace-1" / "global_memory.md").read_text(encoding="utf-8")
    assert "整个工作台以后默认不要新增 memory runtime.kind。" in content


def test_reflection_ignores_without_long_term_intent(tmp_path):
    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="帮我看一下这个函数。",
            assistant_text="可以。",
        ),
        store=MarkdownMemoryStore(root=tmp_path / "memory"),
    )

    assert result.ignored_reason == "below_history_threshold"
    assert not (tmp_path / "memory").exists()


def test_reflection_audit_payload_includes_admission_quality(tmp_path):
    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="以后默认用中文回答，解释保持简洁。",
            assistant_text="好的，我会记住这个偏好。",
        ),
        store=MarkdownMemoryStore(root=tmp_path / "memory"),
    )

    payload = memory_reflection_audit_payload(result)

    assert payload["ignoredReason"] == ""
    assert payload["admission"]["reason"] == "accepted"
    assert payload["admission"]["scores"]["markdown"] >= payload["admission"]["thresholds"]["markdown"]
    assert payload["admission"]["redactedUserMessage"]


def test_reflection_ignores_sensitive_content(tmp_path):
    result = reflect_completed_run_memory(
        MemoryReflectionInput(
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            user_message="记住 token=secret-value-123456",
            assistant_text="不应该记。",
        ),
        store=MarkdownMemoryStore(root=tmp_path / "memory"),
    )

    assert result.ignored_reason == "sensitive_content"
    assert not (tmp_path / "memory").exists()


def test_reflection_deduplicates_existing_note(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")
    item = MemoryReflectionInput(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        user_message="以后默认用中文回答。",
        assistant_text="好的。",
    )

    first = reflect_completed_run_memory(item, store=store)
    second = reflect_completed_run_memory(item, store=store)

    assert first.updates
    assert second.ignored_reason == "duplicate"
    content = (tmp_path / "memory" / "users" / "user_memory.md").read_text(encoding="utf-8")
    assert "auto-generated summary" in content
    assert content.count("以后默认用中文回答。 _(source: session-1)_") == 1


def test_reflection_near_duplicate_ignores_different_source(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")
    first = MemoryReflectionInput(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        user_message="以后默认用中文回答，解释保持简洁。",
        assistant_text="好的。",
        source_run_id="run-1",
    )
    second = MemoryReflectionInput(
        session_id="session-2",
        agent_id="agent-1",
        agent_name="Agent",
        user_message="以后默认用中文回答，解释保持简洁。",
        assistant_text="收到。",
        source_run_id="run-2",
    )

    reflect_completed_run_memory(first, store=store)
    result = reflect_completed_run_memory(second, store=store)

    assert result.ignored_reason == "duplicate"
    content = (tmp_path / "memory" / "users" / "user_memory.md").read_text(encoding="utf-8")
    assert "auto-generated summary" in content
    assert content.count("以后默认用中文回答，解释保持简洁。 _(source: run-1)_") == 1


def test_reflection_merges_conflicting_language_preference(tmp_path):
    store = MarkdownMemoryStore(root=tmp_path / "memory")
    first = MemoryReflectionInput(
        session_id="session-1",
        agent_id="agent-1",
        agent_name="Agent",
        user_message="以后默认用中文回答。",
        assistant_text="好的。",
    )
    second = MemoryReflectionInput(
        session_id="session-2",
        agent_id="agent-1",
        agent_name="Agent",
        user_message="以后默认用英文回答。",
        assistant_text="收到。",
    )

    reflect_completed_run_memory(first, store=store)
    reflect_completed_run_memory(second, store=store)

    content = (tmp_path / "memory" / "users" / "user_memory.md").read_text(encoding="utf-8")
    assert "以后默认用英文回答。" in content
    assert "以后默认用中文回答。 _(source:" not in content


def test_memory_add_tool_redacts_sensitive_note_before_write(tmp_path):
    path = tmp_path / "user_memory.md"
    path.write_text("# Summary\n\n# Stable Preferences\n", encoding="utf-8")
    tool_set = MemoryFileToolSet(
        scope="user",
        owner_id="default",
        file_path=str(path),
        read_content=lambda: path.read_text(encoding="utf-8"),
        write_content=lambda content: path.write_text(content, encoding="utf-8"),
    )
    add_tool = tool_set.tools()[1]

    asyncio.run(add_tool(section="Stable Preferences", note="以后默认联系邮箱是 test@example.com。"))

    content = path.read_text(encoding="utf-8")
    assert "test@example.com" not in content
    assert "<redacted>" in content


def test_reflection_with_agents_writes_model_memories(tmp_path, monkeypatch):
    captured_scopes = []

    async def fake_memory_agent(**kwargs):
        memory_file = kwargs["memory_file"]
        captured_scopes.append((memory_file.scope, memory_file.file_path))
        if memory_file.scope == "global_preference":
            add_tool = memory_file.tools()[1]
            await add_tool(
                section="Workspace Rules",
                note="整个工作台默认通过 hooks 和 tools 接入记忆，不新增 memory runtime.kind。",
                reason="全局架构规则。",
            )
        return MemoryReflectionAgentPayload(
            shouldWrite=True,
            reason="用户明确给出长期规则。",
            memories=[],
        )

    async def fake_skill_agent(**kwargs):
        return None

    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", fake_memory_agent)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)
    store = MarkdownMemoryStore(root=tmp_path / "memory")
    store.write_global_memory("default", "# Summary\n\n已有规则：不要恢复旧记忆模块。")

    result = asyncio.run(
        reflect_completed_run_memory_with_agents(
            MemoryReflectionInput(
                session_id="session-1",
                agent_id="agent-1",
                agent_name="Agent",
                user_message="整个工作台以后默认记忆都通过 hook 和 tools 实现。",
                assistant_text="收到，我会按这个规则处理。",
            ),
            model_name="model",
            store=store,
        ),
    )

    assert result.ignored_reason == ""
    assert result.updates[0].scope == "global_preference"
    assert [scope for scope, _path in captured_scopes] == ["user", "agent", "global_preference"]
    assert captured_scopes[0][1].endswith("/users/user_memory.md")
    assert captured_scopes[1][1].endswith("/agents/agent-1/agent_memory.md")
    assert captured_scopes[2][1].endswith("/global/global_memory.md")
    content = (tmp_path / "memory" / "global" / "global_memory.md").read_text(encoding="utf-8")
    assert "不新增 memory runtime.kind" in content


def test_memory_reflection_prompt_requires_chinese_entity_links():
    tool_set = MemoryFileToolSet(
        scope="global_preference",
        owner_id="default",
        file_path="/tmp/global_memory.md",
        read_content=lambda: "# Summary\n",
        write_content=lambda _content: None,
    )

    system_prompt = _build_memory_reflection_system_prompt("global_preference")
    user_prompt = _build_memory_reflection_user_prompt(
        user_message="整个工作台不要恢复旧 Plan Runtime。",
        assistant_text="收到。",
        agent_id="agent-1",
        agent_name="Agent",
        memory_file=tool_set,
    )

    assert "[[类型:名称]]" in system_prompt
    assert "[[规则:禁止恢复独立 Plan Runtime 分支]]" in system_prompt
    assert '"useObsidianEntityLinks":true' in user_prompt


def test_reflection_with_agents_runs_three_memory_agents_in_parallel(tmp_path, monkeypatch):
    started = []

    async def fake_memory_agent(**kwargs):
        memory_file = kwargs["memory_file"]
        started.append(memory_file.scope)
        if len(started) < 3:
            while len(started) < 3:
                await asyncio.sleep(0)
        return None

    async def fake_skill_agent(**kwargs):
        return None

    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", fake_memory_agent)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)

    result = asyncio.run(
        asyncio.wait_for(
            reflect_completed_run_memory_with_agents(
                MemoryReflectionInput(
                    session_id="session-1",
                    agent_id="agent-1",
                    agent_name="Agent",
                    user_message="以后默认用中文回答。",
                    assistant_text="好的。",
                ),
                model_name="model",
                store=MarkdownMemoryStore(root=tmp_path / "memory"),
            ),
            timeout=1,
        ),
    )

    assert started == ["user", "agent", "global_preference"]
    assert result.updates


def test_reflection_with_agents_returns_skill_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    import src.server.routes.skills as skills_routes
    original_skills = dict(skills_routes._skills)

    async def fake_memory_agent(**kwargs):
        return None

    async def fake_skill_agent(**kwargs):
        assert kwargs["router_gate"].should_evaluate is True
        assert kwargs["workflow_evidence"].successful_tool_chain
        return SkillEvolutionAgentPayload(
            reusable=True,
            confidence=0.86,
            granularity="atomic",
            reason="本次工作形成了可复用流程。",
            draft=SkillDraftPayload(
                name="Memory Hook Design",
                description="设计并接入记忆 hooks 的流程。",
                trigger="当用户需要设计记忆模块 hooks 时使用。",
                content="1. 明确 hook 生命周期。\n2. 区分 Markdown 和节点记忆。",
                tags=["memory", "hooks"],
            ),
        )

    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", fake_memory_agent)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)

    try:
        result = asyncio.run(
            reflect_completed_run_memory_with_agents(
                MemoryReflectionInput(
                    session_id="session-1",
                    agent_id="agent-1",
                    agent_name="Agent",
                    user_message="帮我设计一个可复用的记忆 hook 流程。",
                    assistant_text="已完成设计。\n" + "实现步骤：" * 80,
                    tool_calls=[
                        ChatToolRun(
                            id="call-edit",
                            name="Edit",
                            args='{"file_path":"backend/src/memory/reflection.py"}',
                            result="updated",
                            status="completed",
                        ),
                    ],
                ),
                model_name="model",
                store=MarkdownMemoryStore(root=tmp_path / "memory"),
            ),
        )
    finally:
        skills_routes._skills.clear()
        skills_routes._skills.update(original_skills)

    assert result.skill_candidate is not None
    assert result.skill_candidate.draft is not None
    assert result.skill_candidate.draft.name == "Memory Hook Design"
    assert result.skill_evolution_record is not None
    assert result.skill_evolution_record["status"] == "registered"
    assert result.skill_evolution_record["draftSkillName"] == "Memory Hook Design"
    assert result.skill_evolution_record["registeredSkillId"]


def test_reflection_with_agents_skips_skill_agent_when_router_gate_rejects(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)

    async def fake_memory_agent(**kwargs):
        return None

    async def fake_skill_agent(**kwargs):
        raise AssertionError("SkillEvolutionAgent should not run without reusable evidence")

    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", fake_memory_agent)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)

    result = asyncio.run(
        reflect_completed_run_memory_with_agents(
            MemoryReflectionInput(
                session_id="session-1",
                agent_id="agent-1",
                agent_name="Agent",
                user_message="帮我看一下这个函数。",
                assistant_text="可以。",
            ),
            model_name="model",
            store=MarkdownMemoryStore(root=tmp_path / "memory"),
        ),
    )

    assert result.skill_candidate is None
    assert result.skill_evolution_record is not None
    assert result.skill_evolution_record["status"] == "skipped"
    assert result.skill_evolution_record["routerGate"]["shouldEvaluate"] is False


def test_manual_skill_generation_request_is_detected():
    assert is_manual_skill_generation_request("把这个生成 Skill")
    assert is_manual_skill_generation_request("把刚才的流程生成skills")
    assert is_manual_skill_generation_request("create a skill from this")
    assert not is_manual_skill_generation_request("这个功能是不是可以用 Skill")


def test_manual_skill_request_forces_skill_evolution_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    import src.server.routes.skills as skills_routes

    original_skills = dict(skills_routes._skills)
    monkeypatch.setattr(skills_routes, "SKILL_PACKAGES_DIR", str(tmp_path / "skill_packages"))

    async def fake_memory_agent(**kwargs):
        return None

    async def fake_skill_agent(**kwargs):
        assert kwargs["router_gate"].should_evaluate is True
        assert "manual_skill_request" in kwargs["router_gate"].signals
        assert kwargs["user_message"] == "帮我完成 API 文档检查流程。"
        return SkillEvolutionAgentPayload(
            reusable=True,
            confidence=0.9,
            granularity="atomic",
            reason="用户显式要求沉淀 Skill。",
            draft=SkillDraftPayload(
                name="API 文档检查流程",
                description="检查 API 文档完整性的流程。",
                trigger="当用户需要检查 API 文档时使用。",
                content="1. 检查参数。\n2. 检查错误码。\n3. 输出问题清单。",
                tags=["api", "docs"],
            ),
        )

    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", fake_memory_agent)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)

    try:
        result = asyncio.run(
            reflect_completed_run_memory_with_agents(
                MemoryReflectionInput(
                    session_id="session-1",
                    agent_id="agent-1",
                    agent_name="Agent",
                    user_message="帮我完成 API 文档检查流程。",
                    assistant_text="已完成。",
                    manual_skill_request=True,
                ),
                model_name="model",
                store=MarkdownMemoryStore(root=tmp_path / "memory"),
            ),
        )
    finally:
        skills_routes._skills.clear()
        skills_routes._skills.update(original_skills)

    assert result.skill_evolution_record is not None
    assert result.skill_evolution_record["status"] == "registered"
    assert result.skill_evolution_record["draftSkillName"] == "API 文档检查流程"


def test_reflection_with_agents_rejects_too_broad_skill_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)

    async def fake_memory_agent(**kwargs):
        return None

    async def fake_skill_agent(**kwargs):
        return SkillEvolutionAgentPayload(
            reusable=True,
            confidence=0.9,
            granularity="too_broad",
            requiresWorkflow=True,
            reason="本次工作过大，应拆分。",
            draft=None,
        )

    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", fake_memory_agent)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)

    result = asyncio.run(
        reflect_completed_run_memory_with_agents(
            MemoryReflectionInput(
                session_id="session-1",
                agent_id="agent-1",
                agent_name="Agent",
                user_message="帮我实现一个可复用的旅行规划工作流。",
                assistant_text="已完成设计。\n" + "实现步骤：" * 80,
                tool_calls=[
                    ChatToolRun(
                        id="call-read",
                        name="Read",
                        args='{"file_path":"docs/workflow.md"}',
                        result="read",
                        status="completed",
                    ),
                ],
            ),
            model_name="model",
            store=MarkdownMemoryStore(root=tmp_path / "memory"),
        ),
    )

    assert result.skill_candidate is None
    assert result.skill_evolution_record is not None
    assert result.skill_evolution_record["status"] == "skipped"
    assert "原子化" in result.skill_evolution_record["riskNotes"]


def test_reflection_with_agents_falls_back_to_rules(tmp_path, monkeypatch):
    async def failing_memory_agent(**kwargs):
        raise RuntimeError("model unavailable")

    async def fake_skill_agent(**kwargs):
        return None

    monkeypatch.setattr("src.memory.reflection.run_memory_reflection_agent", failing_memory_agent)
    monkeypatch.setattr("src.memory.reflection.run_skill_evolution_agent", fake_skill_agent)

    result = asyncio.run(
        reflect_completed_run_memory_with_agents(
            MemoryReflectionInput(
                session_id="session-1",
                agent_id="agent-1",
                agent_name="Agent",
                user_message="以后默认用中文回答。",
                assistant_text="好的。",
            ),
            model_name="model",
            store=MarkdownMemoryStore(root=tmp_path / "memory"),
        ),
    )

    assert result.updates
    content = (tmp_path / "memory" / "users" / "user_memory.md").read_text(encoding="utf-8")
    assert "以后默认用中文回答。" in content
