"""兼容入口。实现在 src.memory.prompt_cache，避免记忆模块反向导入 Chat 路由包。"""

from src.memory.legacy.prompt_cache import session_memory_prompt
from src.memory.prompt_cache import (
    MEMORY_PROMPT_TTL_SECONDS,
    invalidate_habit_prompts,
    invalidate_memory_prompts,
    invalidate_professional_prompts,
    session_habit_prompt,
    session_professional_prompt,
)

__all__ = [
    "MEMORY_PROMPT_TTL_SECONDS",
    "invalidate_habit_prompts",
    "invalidate_memory_prompts",
    "invalidate_professional_prompts",
    "session_habit_prompt",
    "session_memory_prompt",
    "session_professional_prompt",
]
