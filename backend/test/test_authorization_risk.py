"""决策矩阵与风险分类。三种模式只改变决定来源。"""

import pytest

from src.security.authorization.models import TargetVersion
from src.security.authorization.policy import evaluate_policy
from src.security.authorization.risk import classify_risk
from src.security.authorization.service import PlatformAuthorizationService
from test.authorization_fakes import run_async
from test.test_permission_records import action_for


async def _open(_action):
    return None


def _action(**updates):
    return action_for(**updates) if "approvalMode" in updates else action_for()


def _with(action, **updates):
    return action.model_copy(update=updates)


CASES = [
    ("trusted_readonly", {"effect": "read_only", "tool_class": "ScopedRead"}, "allow", "allow", "allow", False),
    ("platform_internal", {"effect": "orchestration", "tool_class": "DelegateTask"}, "allow", "allow", "allow", False),
    ("low_risk", {"effect": "mutation", "tool_class": "MemoryAdd"}, "needs_human", "allow", "allow", False),
    ("human_only", {"effect": "mutation", "tool_class": "ConfirmJob"}, "needs_human", "needs_human", "allow", False),
    ("dangerous", {"effect": "mutation", "tool_class": "MemoryDelete"}, "needs_human", "needs_human", "allow", True),
    ("unknown", {"effect": "unknown", "tool_class": "UnregisteredTool"}, "needs_human", "needs_human", "allow", True),
    ("insufficient_evidence", {"effect": "mutation", "tool_class": "ScopedWrite", "targets": ()}, "needs_human", "needs_human", "allow", False),
]


@pytest.mark.parametrize("risk_class,updates,manual,review,full,calls_model", CASES)
@run_async
async def test_mode_matrix_changes_only_decision_source(risk_class, updates, manual, review, full, calls_model):
    expected = {"manual": manual, "model_review": review, "full_access": full}
    calls = {"manual": 0, "model_review": 0, "full_access": 0}

    async def reviewer(action):
        calls[action.authorization.approval_mode] += 1
        return PlatformAuthorizationService._decision(action, "allow", "model", "smartguard", "allow")

    for mode, verdict in expected.items():
        action = _with(action_for(approvalMode=mode), **updates)
        result = await evaluate_policy(action, _open)
        assert result.risk_class == risk_class
        assert result.verdict == verdict
        service = PlatformAuthorizationService(boundary_check=_open, revalidate=_open, reviewer=reviewer)
        decision = await service.authorize(action)
        if verdict == "allow" or (mode == "model_review" and calls_model):
            assert decision.verdict == "allow"
            execution = await service.reserve_execution(action, decision.id)
            started = await service.start_execution(action, decision.id)
            assert started.id == execution.id
            assert await service.finish_execution(action, decision.id, status="succeeded")
        else:
            assert decision.verdict == "needs_human"
    assert calls == {
        "manual": 0,
        "model_review": 1 if calls_model else 0,
        "full_access": 0,
    }


@pytest.mark.parametrize("tool_class,effect,expected", [
    *[(name, "orchestration", "platform_internal") for name in (
        "PlanEnterTool", "SpawnMember", "DelegateTask", "ResetTools", "ReadMailbox",
    )],
    *[(name, "mutation", "low_risk") for name in (
        "MemoryAdd", "MemoryUpdate", "TimelineIngest", "SkillDraftPropose",
        "ScheduleJob", "PauseJob", "RecheckJobCapabilities",
    )],
    ("KnowledgeBaseSearch", "outbound", "low_risk"),
    ("ConfirmJob", "mutation", "human_only"),
    ("MemoryDelete", "mutation", "dangerous"),
    ("HttpRequest", "outbound", "dangerous"),
    ("ConfiguredHttpTool", "outbound", "dangerous"),
])
def test_initial_tool_table(tool_class, effect, expected):
    found = classify_risk(_with(action_for(), effect=effect, tool_class=tool_class))
    assert found.risk_class == expected


def test_tool_table_and_shell_patterns():
    session = "/tmp/.agent-workstation/sessions/s/note.md"
    outside = "/tmp/outside.txt"
    samples = [
        ({"effect": "mutation", "tool_class": "ScopedWrite", "targets": (TargetVersion(target=session),)}, "low_risk"),
        ({"effect": "mutation", "tool_class": "ScopedWrite", "targets": (TargetVersion(target=outside),)}, "dangerous"),
        ({"effect": "outbound", "tool_class": "HttpRequest"}, "dangerous"),
        ({"effect": "outbound", "tool_class": "KnowledgeBaseSearch"}, "low_risk"),
        ({"effect": "unknown", "tool_class": "GuardedBash", "normalized_input_json": '{"command":"launchctl list"}'}, "human_only"),
        ({"effect": "unknown", "tool_class": "GuardedBash", "normalized_input_json": '{"command":"sudo true"}'}, "dangerous"),
        ({"effect": "unknown", "tool_class": "GuardedBash", "normalized_input_json": '{"command":"echo hello"}'}, "unknown"),
        ({"effect": "unknown", "tool_class": "GuardedBash", "normalized_input_json": '{"command":"unterminated \'quote"}'}, "insufficient_evidence"),
    ]
    for updates, expected in samples:
        found = classify_risk(_with(action_for(), **updates))
        assert found.risk_class == expected, updates


def test_smartguard_input_keeps_exact_action_and_drops_history():
    from src.security.authorization.review_contract import smartguard_input

    action = _with(
        action_for(),
        effect="unknown",
        tool_class="GuardedBash",
        normalized_input_json='{"command":"echo hello # comment"}',
    )
    payload = smartguard_input(
        action,
        user_goal="请只读 " + ("很" * 600),
        execution_context="desktop cwd=/tmp",
        user_restrictions=("不要删除",),
    )
    assert set(payload) == {"trusted", "untrusted"}
    assert payload["untrusted"]["action"] == action.normalized_input_json
    assert "# comment" in payload["untrusted"]["action"]
    assert len(payload["trusted"]["userGoal"]) <= 500
    assert "history" not in payload["trusted"]
    assert "skill" not in payload["trusted"]


def test_comment_change_invalidates_fingerprint_without_stripping():
    original = _with(
        action_for(),
        effect="unknown",
        tool_class="GuardedBash",
        normalized_input_json='{"command":"echo hello # keep"}',
    )
    changed = original.model_copy(update={"normalized_input_json": '{"command":"echo hello # drop"}'})
    assert "# keep" in original.normalized_input_json
    assert original.fingerprint != changed.fingerprint


def test_smartguard_resource_matches_approved_appendix():
    from pathlib import Path
    from src.security.authorization.prompt_provider import load_smartguard_prompt

    design = Path(__file__).parents[2].joinpath(
        "openspec/changes/permission-system-v2/design.md",
    ).read_text(encoding="utf-8")
    section = design.split("## 附录 A", 1)[1]
    fences = []
    cursor = section
    while "```text" in cursor:
        cursor = cursor.split("```text", 1)[1]
        body, cursor = cursor.split("```", 1)
        fences.append(body.strip("\n"))
    prompt = load_smartguard_prompt()
    assert prompt.version == "smartguard-2026-10-19"
    assert "d97007c3" in Path(__file__).parents[1].joinpath(
        "src/security/authorization/prompts/smartguard.md",
    ).read_text(encoding="utf-8")
    assert prompt.system == fences[0]
    assert prompt.user_template == fences[1]


def test_classification_failure_is_unknown(monkeypatch):
    def broken(_action):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.security.authorization.risk._classify", broken)
    found = classify_risk(action_for())
    assert found.risk_class == "unknown"
    assert found.rule_key == "classification_error"
