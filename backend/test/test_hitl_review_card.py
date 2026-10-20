"""模型拒绝复核卡的公开载荷：原因、风险规则和 reviewable。"""

from types import SimpleNamespace

from src.server.routes.chat.hitl import _create_pending_interaction, _hitl_payload


def test_model_deny_review_payload_exposes_reviewable_reason():
    owner = SimpleNamespace(approval_mode="model_review", actor_id="actor")
    agent = SimpleNamespace(
        authorization_binding=SimpleNamespace(owner=owner),
        authorization_details=lambda call_id: {
            "reviewable": call_id == "call-1",
            "reason": "模型拒绝了这一次：外发",
            "riskRule": "外发请求必须经过审核",
            "riskClass": "dangerous",
            "status": "pending",
            "reasonCode": "human_review_of_model_deny",
        },
    )
    interaction = _create_pending_interaction(
        interaction_type="REQUIRE_USER_CONFIRM", agent=agent,
        session_id="s", agent_id="a", agent_name="Agent", model_name="fake",
        reply_id="reply", tool_calls=[{"id": "call-1", "name": "HttpRequest", "input": "{}"}],
        message="发送", assistant_text="", skill_runs=[], run_id="r", persist=False,
    )
    payload = _hitl_payload(interaction)
    assert payload["approvalMode"] == "model_review"
    assert payload["decisions"][0]["reviewable"] is True
    assert payload["decisions"][0]["riskRule"] == "外发请求必须经过审核"
    assert "外发" in payload["decisions"][0]["reason"]
