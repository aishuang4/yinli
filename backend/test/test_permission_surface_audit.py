"""公開表面不得帶出憑據或審核思維鏈，可信區用戶訊息維持截斷。"""

import json

import pytest

from src.agents.permission_review_model import ReviewModelResponse
from src.security.authorization.display_input import HIDDEN_CREDENTIAL
from src.security.authorization.payloads import ActionInputVault, public_input
from src.security.authorization.review_contract import ReviewEvidence, smartguard_input
from src.security.authorization.reviewer import PermissionReviewer
from src.server.routes.chat.authorization_display import (
    public_authorization_event,
    trace_safe_event_data,
)
from src.tenancy.context import use_tenant
from test.authorization_fakes import run_async
from test.test_permission_records import action_for
from test.test_permission_reviewer import review_binding


SECRET = "Bearer sk-audittestsecret"
THINKING = "私有思维链不应该出现"


class _Agent:
    def __init__(self):
        self.authorization_binding = object()


def test_sse_and_trace_views_drop_credentials(local_tenant):
    agent = _Agent()
    raw = '{"headers":{"Authorization":"%s"},"note":"%s"}' % (SECRET, THINKING)
    delta = public_authorization_event(agent, "TOOL_CALL_DELTA", {
        "tool_call_id": "call-1", "delta": raw,
    })
    ended = public_authorization_event(agent, "TOOL_CALL_END", {"tool_call_id": "call-1"})
    traced = trace_safe_event_data(agent, "TOOL_CALL_DELTA", {"tool_call_id": "call-1", "delta": raw})
    confirm = public_authorization_event(agent, "REQUIRE_USER_CONFIRM", {"tool_calls": [
        {"id": "call-1", "name": "HttpRequest", "input": raw},
    ]})
    blob = json.dumps({"delta": delta, "end": ended, "trace": traced, "ui": confirm}, ensure_ascii=False)
    assert SECRET not in blob and "sk-audittestsecret" not in blob
    assert delta["delta"] == ""
    assert HIDDEN_CREDENTIAL in ended["input"]
    assert traced["delta"] == ""
    assert "thinking" not in ended


def test_restricted_snapshot_keeps_secret_out_of_decision_json(local_tenant):
    owner = action_for().authorization
    with use_tenant(local_tenant):
        vault = ActionInputVault(owner)
        canonical = json.dumps({"Authorization": SECRET, "note": THINKING}, ensure_ascii=False)
        ref, digest = vault.put(canonical)
    action = action_for().model_copy(update={
        "normalized_input_json": json.dumps(public_input(json.loads(canonical)), ensure_ascii=False),
        "restricted_input_ref": ref,
        "execution_input_digest": digest,
    })
    stored = action.model_dump_json(by_alias=True)
    assert SECRET not in stored and "sk-audittestsecret" not in stored and THINKING not in stored
    assert ref in stored
    with use_tenant(local_tenant):
        assert SECRET in vault.get(ref)


def test_smartguard_trusted_goal_is_truncated_without_secret():
    action = action_for(approvalMode="model_review")
    goal = f"{SECRET} " + ("目标" * 400)
    payload = smartguard_input(action, user_goal=goal, execution_context=SECRET, user_restrictions=(SECRET,))
    trusted = json.dumps(payload["trusted"], ensure_ascii=False)
    assert len(payload["trusted"]["userGoal"]) <= 500
    assert SECRET not in trusted and "sk-audittestsecret" not in trusted and THINKING not in trusted
    assert "thinking" not in payload["trusted"]
    assert set(payload) == {"trusted", "untrusted"}


@run_async
async def test_smartguard_observation_drops_thinking_and_provider_secret():
    action = action_for(approvalMode="model_review")
    observed = []

    async def evidence(_action):
        return ReviewEvidence(f"{SECRET} " + ("字" * 600), '{"path":"README.md"}')

    async def transport(_binding, *, system, prompt, max_output_tokens):
        assert SECRET not in prompt and "sk-audittestsecret" not in prompt and THINKING not in prompt
        assert THINKING not in system
        assert max_output_tokens <= 64 or max_output_tokens >= 16
        return ReviewModelResponse(f"{THINKING}\ndeny", 3, 1)

    reviewer = PermissionReviewer(
        binding=review_binding(), evidence=evidence, transport=transport,
        available=lambda _: True, observe=observed.append,
    )
    decision = await reviewer(action)
    blob = json.dumps(observed, ensure_ascii=False) + decision.model_dump_json()
    assert decision.verdict == "needs_human"
    assert SECRET not in blob and THINKING not in blob
    assert "prompt" not in observed[-1]
    assert observed[-1]["outcome"] == "invalid_token"
