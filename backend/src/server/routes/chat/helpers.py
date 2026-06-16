"""对话路由通用小工具 — SSE 格式化、文本裁剪、Skill 状态更新。"""

import json

from src.server.routes.chat.models import ChatSkillRun


def _sse_event(event_type: str, data: dict) -> str:
    """
    格式化 SSE 事件

    遵循 Server-Sent Events 规范：
      event: <type>
      data: <json>

    Args:
        event_type: 事件类型名称
        data: 事件数据（将被 JSON 序列化）

    Returns:
        str: 符合 SSE 规范的事件字符串
    """
    json_str = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {json_str}\n\n"


def _trim_text(text: str, limit: int) -> str:
    """Trim text for compact activity cards."""
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _set_skill_run_status(
    skill_runs: list[ChatSkillRun],
    status: str,
) -> list[ChatSkillRun]:
    """返回更新状态后的 Skill run 列表。"""
    return [
        item.model_copy(update={"status": status})
        for item in skill_runs
    ]
