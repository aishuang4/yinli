"""记忆注入文本按会话冻结，保证 system prompt 跨轮字节一致。"""

from __future__ import annotations

from src.memory import prompt_cache as mpc
from src.memory.legacy.prompt_cache import session_memory_prompt


def setup_function() -> None:
    mpc.invalidate_memory_prompts()


def teardown_function() -> None:
    mpc.invalidate_memory_prompts()


def test_same_session_reuses_first_text() -> None:
    calls: list[int] = []

    def build() -> str:
        calls.append(1)
        return f"memory v{len(calls)}"

    first = session_memory_prompt(agent_id="a", session_id="s", build=build)
    second = session_memory_prompt(agent_id="a", session_id="s", build=build)
    assert first == second == "memory v1"
    assert len(calls) == 1


def test_other_session_or_no_session_builds_fresh() -> None:
    counter = iter(range(100))
    build = lambda: f"v{next(counter)}"  # noqa: E731
    assert session_memory_prompt(agent_id="a", session_id="s1", build=build) == "v0"
    assert session_memory_prompt(agent_id="a", session_id="s2", build=build) == "v1"
    # 无 session（沙箱 / 未持久化）不冻结
    assert session_memory_prompt(agent_id="a", session_id=None, build=build) == "v2"
    assert session_memory_prompt(agent_id="a", session_id=None, build=build) == "v3"


def test_invalidate_by_agent_and_all() -> None:
    counter = iter(range(100))
    build = lambda: f"v{next(counter)}"  # noqa: E731
    session_memory_prompt(agent_id="a", session_id="s", build=build)
    session_memory_prompt(agent_id="b", session_id="s", build=build)
    assert mpc.invalidate_memory_prompts(agent_id="a") == 1
    assert session_memory_prompt(agent_id="a", session_id="s", build=build) == "v2"
    assert session_memory_prompt(agent_id="b", session_id="s", build=build) == "v1"
    assert mpc.invalidate_memory_prompts() == 2


def test_ttl_expiry(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(mpc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(mpc, "MEMORY_PROMPT_TTL_SECONDS", 10)
    counter = iter(range(100))
    build = lambda: f"v{next(counter)}"  # noqa: E731
    assert session_memory_prompt(agent_id="a", session_id="s", build=build) == "v0"
    now[0] += 5
    assert session_memory_prompt(agent_id="a", session_id="s", build=build) == "v0"
    now[0] += 11
    assert session_memory_prompt(agent_id="a", session_id="s", build=build) == "v1"


def test_habit_invalidation_rebuilds_next_prompt() -> None:
    calls: list[str] = []

    def build() -> str:
        calls.append("x")
        return f"habit v{len(calls)}"

    assert mpc.session_habit_prompt(user_id="default", build=build) == "habit v1"
    assert mpc.session_habit_prompt(user_id="default", build=build) == "habit v1"
    mpc.invalidate_habit_prompts()
    assert mpc.session_habit_prompt(user_id="default", build=build) == "habit v2"
