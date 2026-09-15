"""Chat session 级 AgentScope 对话上下文的延续与恢复。

设计原则：
- AgentScope `AgentState.context` / `summary` 是会话对话记忆的唯一在线事实源，
  不再把历史消息扁平化拼进 system prompt。
- Agent 实例只是执行壳。同一 `(agent_id, session_id)` 因配置变化需要换实例时，
  把旧实例的对话上下文整体搬到新实例；进程重启后则用 `chat_messages` 里的
  user / assistant 正文重建一份精简上下文。
- 上一轮遗留的 AskUser / 审批等待如果用户没有回应而直接发新消息，必须先
  补一条 ToolResult 把等待关闭，否则 AgentScope 会拒绝新的 Msg 输入。
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from agentscope.message import AssistantMsg, TextBlock, ToolResultBlock, UserMsg
from agentscope.message._block import ToolCallState, ToolResultState

logger = logging.getLogger(__name__)

# 进程重启后从 chat_messages 重建上下文时的上限，避免一次性塞入整段长会话。
SESSION_HISTORY_SEED_LIMIT = 20
SESSION_HISTORY_SEED_CHARS = 2000

ABANDONED_TOOL_CALL_NOTE = (
    "用户没有回应这次请求，而是直接发送了新的消息。请忽略这次等待，以最新的用户消息为准继续。"
)


@dataclass(frozen=True)
class SessionHistoryMessage:
    """重建上下文所需的最小历史消息快照。"""

    role: str
    content: str
    file_names: tuple[str, ...] = ()


def session_context_key(agent_id: str, session_id: str) -> str:
    """同一持久 Agent 在同一 Chat session 下的对话上下文标识。"""

    return f"{agent_id}::{session_id}"


def settle_awaiting_tool_calls(agent: Any, *, note: str = ABANDONED_TOOL_CALL_NOTE) -> int:
    """把尾部仍在等待用户/外部结果的 tool call 关闭，返回关闭数量。"""

    state = getattr(agent, "state", None)
    name = getattr(agent, "name", "")
    if state is None or not getattr(state, "context", None):
        return 0
    awaiting = list(state.get_awaiting_tool_calls(name))
    if not awaiting:
        return 0
    tail = state.context[-1]
    for tool_call in awaiting:
        tool_call.state = ToolCallState.FINISHED
        tail.content.append(
            ToolResultBlock(
                id=tool_call.id,
                name=tool_call.name,
                output=[TextBlock(type="text", text=note)],
                state=ToolResultState.INTERRUPTED,
            )
        )
    return len(awaiting)


def carry_over_context(previous: Any, target: Any, *, settle: bool = True) -> int:
    """把旧实例的对话上下文搬到新实例，返回搬运的消息数。

    `settle=False` 用于旧实例仍在执行的场景：只复制快照，不修改旧实例状态。
    """

    prev_state = getattr(previous, "state", None)
    target_state = getattr(target, "state", None)
    if prev_state is None or target_state is None:
        return 0
    if settle:
        settle_awaiting_tool_calls(previous)
    context = list(getattr(prev_state, "context", []) or [])
    if not context and not getattr(prev_state, "summary", ""):
        return 0
    target_state.context = copy.deepcopy(context)
    if not settle:
        settle_awaiting_tool_calls(target)
    target_state.summary = copy.deepcopy(getattr(prev_state, "summary", ""))
    # 旧实例的 middleware 跨轮数据（例如 Plan Mode 计数）一并保留。
    target_state.middle_context = copy.deepcopy(getattr(prev_state, "middle_context", {}) or {})
    # 旧实例名字可能不同（Agent 改名），统一改为新实例名，保证 get_awaiting_tool_calls 命中。
    for msg in target_state.context:
        if msg.role == "assistant" and msg.name == getattr(previous, "name", None):
            msg.name = getattr(target, "name", msg.name)
    return len(target_state.context)


def seed_context_from_history(
    agent: Any,
    history: Iterable[SessionHistoryMessage],
    *,
    limit: int = SESSION_HISTORY_SEED_LIMIT,
    max_chars: int = SESSION_HISTORY_SEED_CHARS,
) -> int:
    """进程内没有上下文时，用持久化的会话正文重建精简上下文。

    只在实例上下文为空时执行；只保留最近 `limit` 条有正文的 user / assistant
    消息，每条裁到 `max_chars`。工具调用细节不重建，由模型按需重新读取。
    """

    state = getattr(agent, "state", None)
    if state is None or getattr(state, "context", None):
        return 0
    items = [item for item in history if (item.content or "").strip() or item.file_names]
    items = items[-limit:]
    if not items:
        return 0
    messages = []
    for item in items:
        text = " ".join((item.content or "").split())
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
        if item.file_names:
            text = f"{text}\n[附件: {', '.join(item.file_names)}]".strip()
        if item.role == "user":
            messages.append(UserMsg(name="user", content=text))
        else:
            messages.append(AssistantMsg(name=getattr(agent, "name", "assistant"), content=text))
    state.context = messages
    logger.debug("已从会话历史重建 %s 条上下文消息", len(messages))
    return len(messages)


def snapshot_session_context_state(agent: Any) -> tuple[str, dict[str, Any]]:
    """取出可落库的会话级状态：压缩摘要文本 + middleware 跨轮数据。"""

    state = getattr(agent, "state", None)
    if state is None:
        return "", {}
    summary = getattr(state, "summary", "")
    if not isinstance(summary, str):
        summary = "".join(str(getattr(item, "text", "") or "") for item in (summary or []))
    middle_context = getattr(state, "middle_context", None)
    return summary, dict(middle_context) if isinstance(middle_context, dict) else {}


def restore_session_context_state(agent: Any, summary: str, middle_context: dict[str, Any] | None) -> bool:
    """实例重建后恢复压缩摘要与 middleware 跨轮数据；已有值不覆盖。返回是否有恢复动作。"""

    state = getattr(agent, "state", None)
    if state is None:
        return False
    restored = False
    if summary and not getattr(state, "summary", ""):
        state.summary = summary
        restored = True
    store = getattr(state, "middle_context", None)
    if isinstance(store, dict) and middle_context:
        for key, value in middle_context.items():
            if key not in store:
                store[key] = copy.deepcopy(value)
                restored = True
    if restored:
        logger.debug("已恢复会话上下文状态：summary=%s 字，middle_context=%s 项", len(summary or ""), len(middle_context or {}))
    return restored


def reset_agent_context(agent: Any) -> None:
    """清空实例对话上下文。供 Readiness / IntentRouter 这类一次性系统 Agent 每次调用前使用。"""

    state = getattr(agent, "state", None)
    if state is None:
        return
    state.context = []
    state.summary = ""


def context_message_count(agent: Any) -> int:
    """当前实例对话上下文中的消息数（供 trace / 测试观测）。"""

    state = getattr(agent, "state", None)
    return len(getattr(state, "context", []) or []) if state is not None else 0
