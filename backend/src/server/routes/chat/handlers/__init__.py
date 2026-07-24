"""导入各端点模块以触发 router 注册。"""

from src.server.routes.chat.handlers import (  # noqa: F401
    resume,
    send,
    runs,
    sessions,
)
