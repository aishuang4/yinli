"""工具死循环护栏。

Chat、Team、群聊都用工具名、参数和结果指纹判断是不是在原地打转。
先把提醒追加到当前工具结果，达到阈值就停掉这一轮。
轮次只留 512 的异常兜底，不用小上限把长任务打断。计数只活在这一次 reply 里。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, AsyncGenerator

from agentscope.middleware import MiddlewareBase

IDEMPOTENT_TOOLS = frozenset({"Read", "Grep", "Glob", "WebFetch"})
FAILURE_TOLERANT_TOOLS = frozenset({"Bash", "HttpRequest", "WebFetch"})
PROGRESS_RESET_TOOLS = frozenset({"Write", "Edit", "Bash"})

WARN_EXACT_FAILURE = 2
STOP_EXACT_FAILURE = 5
WARN_SAME_TOOL_FAILURE = 3
STOP_SAME_TOOL_FAILURE = 8
WARN_NO_PROGRESS = 2
STOP_NO_PROGRESS = 5
WARN_IDENTICAL_STREAK = 3
WARN_CYCLE_LAPS = 3
STOP_CYCLE_LAPS = 5
CYCLE_HISTORY = 64


@dataclass(frozen=True)
class LoopDecision:
    """护栏决定。block 表示这次工具不要执行。"""

    action: str
    message: str


@dataclass
class ToolLoopGuard:
    """一次 reply 内的工具循环计数。达到停止阈值就结束这一轮。"""

    hard_stop: bool = True
    exact_failure: dict[tuple[str, str], int] = field(default_factory=dict)
    same_tool_failure: dict[str, int] = field(default_factory=dict)
    no_progress: dict[tuple[str, str], tuple[str, int]] = field(default_factory=dict)
    progressed: set[tuple[str, str]] = field(default_factory=set)
    streak_key: tuple[tuple[str, str], str] | None = None
    streak_count: int = 0
    history: deque[tuple[tuple[str, str], str]] = field(default_factory=lambda: deque(maxlen=CYCLE_HISTORY))
    halt: LoopDecision | None = None

    def reset(self) -> None:
        """新的一次用户提问开始时清零。"""

        self.exact_failure.clear()
        self.same_tool_failure.clear()
        self.no_progress.clear()
        self.progressed.clear()
        self.streak_key = None
        self.streak_count = 0
        self.history.clear()
        self.halt = None

    def before_call(self, tool_name: str, args: object) -> LoopDecision | None:
        """执行前拦截已经认定的重复失败或只读空转。"""

        if not self.hard_stop or self.halt is not None:
            return self.halt
        signature = _signature(tool_name, args)
        if signature not in self.progressed and self.exact_failure.get(signature, 0) >= STOP_EXACT_FAILURE:
            return self._halt("block", f"{tool_name} 用相同参数连续失败，已停止重试。")
        record = self.no_progress.get(signature)
        if tool_name in IDEMPOTENT_TOOLS and record is not None and record[1] >= STOP_NO_PROGRESS:
            return self._halt("block", f"{tool_name} 连续返回相同结果，已停止重复读取。")
        return None

    def after_call(self, tool_name: str, args: object, result_text: str, *, failed: bool) -> list[str]:
        """执行后更新计数，返回要追加到工具结果末尾的提醒。"""

        signature = _signature(tool_name, args)
        notes: list[str] = []
        if failed:
            if signature in self.progressed:
                self.progressed.discard(signature)
                self.exact_failure.pop(signature, None)
            self.exact_failure[signature] = self.exact_failure.get(signature, 0) + 1
            self.same_tool_failure[tool_name] = self.same_tool_failure.get(tool_name, 0) + 1
            self.no_progress.pop(signature, None)
            exact_count = self.exact_failure[signature]
            same_count = self.same_tool_failure[tool_name]
            if (
                self.hard_stop
                and tool_name not in FAILURE_TOLERANT_TOOLS
                and same_count >= STOP_SAME_TOOL_FAILURE
            ):
                self._halt("halt", f"{tool_name} 反复失败，已停止这一轮。")
            elif exact_count >= WARN_EXACT_FAILURE:
                notes.append(f"[循环提醒] {tool_name} 用相同参数已失败 {exact_count} 次。先看错误再改做法，不要原样重试。")
            elif same_count >= WARN_SAME_TOOL_FAILURE:
                notes.append(f"[循环提醒] {tool_name} 已失败 {same_count} 次。先确认路径、参数或权限，再换一种调用。")
        else:
            self.exact_failure.pop(signature, None)
            self.same_tool_failure.pop(tool_name, None)
            if tool_name in PROGRESS_RESET_TOOLS:
                self.progressed.update(self.exact_failure)
                self.same_tool_failure.clear()
            if tool_name in IDEMPOTENT_TOOLS:
                digest = _digest(result_text)
                previous = self.no_progress.get(signature)
                count = previous[1] + 1 if previous is not None and previous[0] == digest else 1
                self.no_progress[signature] = (digest, count)
                if count >= WARN_NO_PROGRESS:
                    notes.append(f"[循环提醒] {tool_name} 已连续 {count} 次返回相同内容。不要再用同一参数读取。")

        key = (signature, _digest(result_text))
        self.streak_count = self.streak_count + 1 if key == self.streak_key else 1
        self.streak_key = key
        self.history.append(key)
        if self.streak_count >= WARN_IDENTICAL_STREAK:
            notes.append(f"[循环提醒] {tool_name} 已连续 {self.streak_count} 次调用且结果相同。")
            if self.hard_stop and self.streak_count >= STOP_NO_PROGRESS:
                self._halt("halt", f"{tool_name} 连续相同调用，已停止这一轮。")
        else:
            cycle = _detect_cycle(self.history)
            if cycle is not None:
                period, laps = cycle
                notes.append(f"[循环提醒] 同样的 {period} 次调用已经重复 {laps} 圈。")
                if self.hard_stop and laps >= STOP_CYCLE_LAPS:
                    self._halt("halt", "工具调用在几个步骤之间打转，已停止这一轮。")
        if self.halt is not None and self.halt.action == "halt":
            notes.append(f"[循环停止] {self.halt.message}")
        return notes

    def _halt(self, action: str, message: str) -> LoopDecision:
        decision = LoopDecision(action=action, message=message)
        if self.halt is None:
            self.halt = decision
        return self.halt


def _signature(tool_name: str, args: object) -> tuple[str, str]:
    return (tool_name, _digest(_canonical(args)))


def _canonical(value: object) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        return str(value)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _detect_cycle(history: deque[tuple[tuple[str, str], str]]) -> tuple[int, int] | None:
    items = list(history)
    for period in range(2, 5):
        if len(items) < period * WARN_CYCLE_LAPS:
            continue
        laps = 1
        while True:
            start = len(items) - period * (laps + 1)
            if start < 0:
                break
            window = items[start:start + period]
            tail = items[-period:]
            if window != tail:
                break
            laps += 1
        if laps >= WARN_CYCLE_LAPS:
            return period, laps
    return None


class ToolLoopGuardMiddleware(MiddlewareBase):
    """把死循环护栏接到 AgentScope on_acting。提醒只追加在当前工具结果后面。"""

    def __init__(self, *, hard_stop: bool = True) -> None:
        self.guard = ToolLoopGuard(hard_stop=hard_stop)

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Any,
    ) -> AsyncGenerator[Any, None]:
        self.guard.reset()
        async for item in next_handler(**input_kwargs):
            yield item

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Any,
    ) -> AsyncGenerator[Any, None]:
        from agentscope.message import TextBlock, ToolResultState
        from agentscope.tool import ToolResponse

        tool_call = input_kwargs.get("tool_call")
        tool_name = str(getattr(tool_call, "name", "") or "")
        args = getattr(tool_call, "input", None)
        blocked = self.guard.before_call(tool_name, args) if tool_name else None
        if blocked is not None:
            _stop_react_after_this_round(agent)
            yield ToolResponse(
                id=str(getattr(tool_call, "id", "") or ""),
                content=[TextBlock(text=blocked.message)],
                state=ToolResultState.ERROR,
            )
            return

        async for item in next_handler(**input_kwargs):
            if isinstance(item, ToolResponse) and tool_name:
                result_text = _response_text(item)
                failed = str(getattr(item, "state", "") or "").lower() in {"error", "failed"}
                notes = self.guard.after_call(tool_name, args, result_text, failed=failed)
                if notes:
                    content = list(getattr(item, "content", None) or [])
                    content.append(TextBlock(text="\n\n".join(notes)))
                    item.content = content
                if self.guard.halt is not None and self.guard.halt.action == "halt":
                    _stop_react_after_this_round(agent)
            yield item


def _response_text(response: object) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _stop_react_after_this_round(agent: object) -> None:
    """这一轮工具结束后让 ReAct 预算耗尽，交给现有的无工具总结。"""

    config = getattr(agent, "react_config", None)
    state = getattr(agent, "state", None)
    if config is None:
        return
    current = int(getattr(state, "cur_iter", 0) or 0)
    config.max_iters = current + 1
