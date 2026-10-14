"""工具死循环护栏：和轮次预算分开计数。"""

import asyncio
from types import SimpleNamespace

from agentscope.message import ToolResultState

from src.runtime.tool_loop_guard import ToolLoopGuard, ToolLoopGuardMiddleware


def test_identical_reads_warn_and_hard_stop() -> None:
    guard = ToolLoopGuard(hard_stop=True)
    notes = []
    for _ in range(4):
        assert guard.before_call("Read", {"path": "a.md"}) is None
        notes = guard.after_call("Read", {"path": "a.md"}, "same body", failed=False)
    assert any("连续" in note for note in notes)
    assert guard.halt is None
    guard.after_call("Read", {"path": "a.md"}, "same body", failed=False)
    blocked = guard.before_call("Read", {"path": "a.md"})
    assert blocked is not None
    assert blocked.action in {"block", "halt"}


def test_attended_mode_warns_without_stopping() -> None:
    guard = ToolLoopGuard(hard_stop=False)
    for _ in range(8):
        assert guard.before_call("Read", {"path": "a.md"}) is None
        guard.after_call("Read", {"path": "a.md"}, "same body", failed=False)
    assert guard.halt is None


def test_exact_failure_blocks_only_when_hard_stop() -> None:
    guard = ToolLoopGuard(hard_stop=True)
    for index in range(5):
        guard.after_call("Grep", {"pattern": "x"}, f"Error: boom {index}", failed=True)
    blocked = guard.before_call("Grep", {"pattern": "x"})
    assert blocked is not None
    assert blocked.action == "block"


def test_two_step_cycle_is_noted() -> None:
    guard = ToolLoopGuard(hard_stop=False)
    calls = [("Read", {"path": "a"}), ("Read", {"path": "b"})]
    notes: list[str] = []
    for _ in range(3):
        for name, args in calls:
            notes = guard.after_call(name, args, name + str(args), failed=False)
    assert any("重复" in note for note in notes)


def test_blocked_skill_call_does_not_fail_on_result_state_import() -> None:
    """拦截工具时必须能构造错误结果。ToolResultState 不在 agentscope.tool。"""

    middleware = ToolLoopGuardMiddleware(hard_stop=True)
    for index in range(5):
        middleware.guard.after_call("Skill", {"skill": "crew-qual-check"}, f"Error: boom {index}", failed=True)

    async def next_handler(**_kwargs):
        raise AssertionError("已拦截的 Skill 调用不应继续执行")
        yield None

    async def run():
        items = []
        async for item in middleware.on_acting(
            object(),
            {"tool_call": SimpleNamespace(name="Skill", input={"skill": "crew-qual-check"}, id="call-1")},
            next_handler,
        ):
            items.append(item)
        return items

    items = asyncio.run(run())
    assert len(items) == 1
    assert items[0].state == ToolResultState.ERROR
    assert "停止" in items[0].content[0].text


def test_reset_clears_streak() -> None:
    guard = ToolLoopGuard(hard_stop=True)
    for _ in range(5):
        guard.after_call("Read", {"path": "a.md"}, "same", failed=False)
    guard.reset()
    assert guard.before_call("Read", {"path": "a.md"}) is None
