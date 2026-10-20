"""Team Member 必須沿用父 Run 快照。"""

from types import SimpleNamespace

import pytest

from src.server.routes.chat.team_runtime import member_runtime
from src.sessions.errors import SessionError


def test_member_requires_parent_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(member_runtime, "replace", lambda obj, **_kwargs: obj)
    ctx = SimpleNamespace(
        runtime_model_config=SimpleNamespace(model_options=SimpleNamespace(max_react_iters=1)),
        agent_id="default",
        runtime_context=SimpleNamespace(run_id=""),
    )
    with pytest.raises(SessionError) as caught:
        member_runtime._create_team_agent(
            ctx=ctx,
            actor_id="member",
            actor_name="成員",
            session_id="sess",
            system_prompt="",
            runtime_key="member-1",
            extra_tools=[],
            tool_allowlist=set(),
            used_skills=[],
            max_react_iters=4,
        )
    assert caught.value.code == "snapshot_required"
