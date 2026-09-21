"""ReAct 上下文治理 middleware：一级无模型整理 + 压缩可观测。

AgentScope 自带的压缩是"二级"手段：估算 tokens 超过 `trigger_ratio * context_size`
时调用一次模型生成结构化摘要。它有两个问题：触发前上下文可能已经被老的工具
输出和图片撑满；触发后对外完全不可见。这里补两层：

1. `ContextHygieneMiddleware`（`on_reply`）：每轮开始前，把早于最近 `KEEP_RECENT_TURNS`
   轮的大段 tool result 换成一行存根、老图片换成文字占位。不调模型、不改消息
   顺序、不破坏 tool_call / tool_result 配对。Skill 正文（`SkillContextMiddleware`
   负责）和 HintBlock 不动。
2. `ContextCompressionMiddleware`（`on_compress_context`）：记录 AgentScope 真正
   压缩了几次、每次前后的消息数和摘要长度，供 `leader_stream` 写 `CONTEXT_COMPRESSED`
   trace，并把累计次数并入 `CONTEXT_USAGE`。

所有跨轮状态都放在 `agent.state.middle_context`，实例搬运 / 落库时随 state 一起走。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

from agentscope.message import DataBlock, TextBlock, ToolResultBlock
from agentscope.middleware import MiddlewareBase

logger = logging.getLogger(__name__)

HYGIENE_KEY = "aw_context_hygiene"
COMPRESSION_KEY = "aw_context_compression"

# 最近几轮（按 user 文本消息切分）保持原样，只整理更早的内容。
KEEP_RECENT_TURNS = 2
# tool result 输出短于这个字符数时不值得存根化。
STUB_MIN_CHARS = 600
# 存根里保留的原文开头字符数，帮助模型记得"当时看到的是什么"。
STUB_HEAD_CHARS = 120
# 这些工具的输出不做存根：Skill 正文由 SkillContextMiddleware 管理。
HYGIENE_SKIP_TOOLS = frozenset({"Skill"})


def _middle_context(agent: Any) -> dict[str, Any] | None:
    state = getattr(agent, "state", None)
    store = getattr(state, "middle_context", None)
    return store if isinstance(store, dict) else None


def set_context_session_id(agent: Any, session_id: str) -> None:
    """让 AgentScope offloader 按 Chat 会话分目录落盘，而不是随机 id。"""

    state = getattr(agent, "state", None)
    if state is None or not session_id:
        return
    try:
        state.session_id = str(session_id)
    except Exception:
        logger.debug("无法设置 state.session_id", exc_info=True)


def _is_user_turn(msg: Any) -> bool:
    """用户文本消息才算一轮的开始；tool result 也是 user role，但只含 ToolResultBlock。"""

    if getattr(msg, "role", "") != "user":
        return False
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return bool(content.strip())
    return any(isinstance(block, TextBlock) for block in content or [])


def _text_len(output: Any) -> int:
    if isinstance(output, str):
        return len(output)
    return sum(len(str(getattr(item, "text", "") or "")) for item in output or [] if isinstance(item, TextBlock))


def _head_text(output: Any) -> str:
    if isinstance(output, str):
        text = output
    else:
        text = " ".join(str(getattr(item, "text", "") or "") for item in output or [] if isinstance(item, TextBlock))
    text = " ".join(text.split())
    return text[:STUB_HEAD_CHARS]


def compact_stale_context(agent: Any, *, keep_recent_turns: int = KEEP_RECENT_TURNS) -> dict[str, int]:
    """一级整理：存根化早于最近 N 轮的大段 tool result、移除老图片。返回本次统计。"""

    state = getattr(agent, "state", None)
    context = list(getattr(state, "context", []) or [])
    result = {"stubbedResults": 0, "droppedImages": 0, "savedChars": 0}
    if not context:
        return result
    # 找到最近 keep_recent_turns 轮的起点，之前的消息才允许整理。
    turn_starts = [index for index, msg in enumerate(context) if _is_user_turn(msg)]
    if len(turn_starts) <= keep_recent_turns:
        return result
    boundary = turn_starts[-keep_recent_turns]

    for msg in context[:boundary]:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        rebuilt: list[Any] = []
        changed = False
        for block in content:
            if isinstance(block, ToolResultBlock):
                name = str(getattr(block, "name", "") or "")
                length = _text_len(block.output)
                if name in HYGIENE_SKIP_TOOLS or length < STUB_MIN_CHARS:
                    rebuilt.append(block)
                    continue
                head = _head_text(block.output)
                stub = (
                    f"[上下文已整理] 工具 {name or 'unknown'} 的输出（{length} 字）早于最近 {keep_recent_turns} 轮，"
                    f"原文已移出上下文。开头：{head}… 如仍需要请重新调用工具。"
                )
                block.output = stub
                result["stubbedResults"] += 1
                result["savedChars"] += max(0, length - len(stub))
                rebuilt.append(block)
                changed = True
            elif isinstance(block, DataBlock):
                rebuilt.append(TextBlock(type="text", text="[图片已移出上下文：早于最近几轮的用户图片，如需请让用户重新提供]"))
                result["droppedImages"] += 1
                changed = True
            else:
                rebuilt.append(block)
        if changed:
            msg.content = rebuilt

    store = _middle_context(agent)
    if store is not None and (result["stubbedResults"] or result["droppedImages"]):
        totals = store.get(HYGIENE_KEY)
        if not isinstance(totals, dict):
            totals = {"stubbedResults": 0, "droppedImages": 0, "savedChars": 0}
        for key, value in result.items():
            totals[key] = int(totals.get(key) or 0) + value
        totals["lastAt"] = time.time()
        store[HYGIENE_KEY] = totals
        logger.info(
            "上下文一级整理：存根 %s 个工具输出、移除 %s 张图片、约省 %s 字",
            result["stubbedResults"],
            result["droppedImages"],
            result["savedChars"],
        )
    return result


class ContextHygieneMiddleware(MiddlewareBase):
    """每轮开始前做一次无模型整理，把压缩触发点往后推。"""

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[Any, None]],
    ) -> AsyncGenerator[Any, None]:
        try:
            compact_stale_context(agent)
        except Exception:
            logger.warning("上下文一级整理失败，跳过", exc_info=True)
        async for item in next_handler(**input_kwargs):
            yield item


def _summary_len(summary: Any) -> int:
    if isinstance(summary, str):
        return len(summary)
    return sum(len(str(getattr(item, "text", "") or "")) for item in summary or [])


class ContextCompressionMiddleware(MiddlewareBase):
    """记录 AgentScope 压缩事件：什么时候压、压掉多少消息、摘要多长。"""

    async def on_compress_context(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., Any],
    ) -> None:
        state = getattr(agent, "state", None)
        before_messages = len(getattr(state, "context", []) or [])
        before_summary = _summary_len(getattr(state, "summary", ""))
        started = time.time()
        await next_handler(**input_kwargs)
        after_messages = len(getattr(state, "context", []) or [])
        after_summary = _summary_len(getattr(state, "summary", ""))
        summary_value = getattr(state, "summary", "")
        # AgentScope 每步都会调 compress_context，未越过阈值时直接返回；只有 context 变短或摘要变化才算真压缩。
        if after_messages >= before_messages and after_summary == before_summary:
            return
        store = _middle_context(agent)
        if store is None:
            return
        record = store.get(COMPRESSION_KEY)
        if not isinstance(record, dict):
            record = {"count": 0, "pending": []}
        record["count"] = int(record.get("count") or 0) + 1
        event = {
            "index": record["count"],
            "messagesBefore": before_messages,
            "messagesAfter": after_messages,
            "summaryCharsBefore": before_summary,
            "summaryCharsAfter": after_summary,
            "durationMs": int((time.time() - started) * 1000),
            "summaryPreview": (summary_value if isinstance(summary_value, str) else "")[:400],
        }
        pending = record.get("pending")
        if not isinstance(pending, list):
            pending = []
        pending.append(event)
        record["pending"] = pending
        record["lastAt"] = time.time()
        store[COMPRESSION_KEY] = record
        logger.info(
            "上下文压缩完成：第 %s 次，消息 %s -> %s，摘要 %s 字",
            record["count"],
            before_messages,
            after_messages,
            after_summary,
        )


async def force_compress_context(agent: Any) -> None:
    """用户主动压缩：先做一级整理，再把阈值降到接近 0 以触发 AgentScope 摘要。"""

    try:
        compact_stale_context(agent)
    except Exception:
        logger.warning("主动压缩前的一级整理失败，继续尝试摘要", exc_info=True)
    config = getattr(agent, "context_config", None)
    forced = None
    if config is not None:
        try:
            payload = config.model_dump()
            payload["trigger_ratio"] = 1e-9
            forced = type(config).model_construct(**payload)
        except Exception:
            logger.debug("无法复制 context_config，改用默认强制阈值", exc_info=True)
    try:
        compress = getattr(agent, "compress_context", None)
        if not callable(compress):
            return
        if forced is None:
            await compress()
        else:
            await compress(context_config=forced)
    except RuntimeError:
        logger.info("主动压缩跳过：上下文过短或无法压缩")
    except Exception:
        logger.warning("主动压缩失败", exc_info=True)


def drain_compression_records(agent: Any) -> list[dict[str, Any]]:
    """取走尚未写入 trace 的压缩记录。"""

    store = _middle_context(agent)
    if store is None:
        return []
    record = store.get(COMPRESSION_KEY)
    if not isinstance(record, dict):
        return []
    pending = record.get("pending")
    if not isinstance(pending, list) or not pending:
        return []
    record["pending"] = []
    return list(pending)


def context_governance_stats(agent: Any) -> dict[str, int]:
    """供 `CONTEXT_USAGE` / `contextStats` 合并的累计治理指标。"""

    store = _middle_context(agent)
    stats: dict[str, int] = {}
    if store is None:
        return stats
    compression = store.get(COMPRESSION_KEY)
    if isinstance(compression, dict):
        stats["compressionCount"] = int(compression.get("count") or 0)
    hygiene = store.get(HYGIENE_KEY)
    if isinstance(hygiene, dict):
        stats["stubbedToolResults"] = int(hygiene.get("stubbedResults") or 0)
        stats["droppedImages"] = int(hygiene.get("droppedImages") or 0)
        stats["hygieneSavedChars"] = int(hygiene.get("savedChars") or 0)
    return stats
