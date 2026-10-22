"""把附加说明拼到用户文本后面。"""
from __future__ import annotations



def append_user_note(text: str, note: str) -> str:
    """把附件说明或召回记忆等附加段拼到用户文本后面。"""

    if not note:
        return text
    return f"{text}\n\n{note}" if text else note
