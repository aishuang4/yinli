"""对话路由包 — 对接 AgentScope 2.0 真实 Agent。

提供对话相关的 API 端点：
- POST /api/chat/send                发送消息并通过 SSE 返回 Agent 事件流
- POST /api/chat/resume              人工审批后恢复运行
- GET  /api/chat/sessions            获取会话列表
- GET  /api/chat/sessions/{id}/messages
- DELETE /api/chat/sessions/{id}

事件流格式遵循 AgentScope 2.0 EventType：
  REPLY_START / REPLY_END
  MODEL_CALL_START / MODEL_CALL_END
  TEXT_BLOCK_START / TEXT_BLOCK_DELTA / TEXT_BLOCK_END
  THINKING_BLOCK_START / THINKING_BLOCK_DELTA / THINKING_BLOCK_END
  TOOL_CALL_START / TOOL_CALL_DELTA / TOOL_CALL_END
  TOOL_RESULT_START / TOOL_RESULT_TEXT_DELTA / TOOL_RESULT_END

本模块对外重新导出 router 及路由共享模型。
"""

from src.server.routes.chat.router import router
from src.server.routes.chat import handlers  # noqa: F401  触发端点注册
from src.server.routes.chat.models import (
    ChatImagePayload,
    ChatMessage,
    ChatResumeRequest,
    ChatSession,
    ChatSkillRun,
    ChatStore,
    ChatTeamMemberPayload,
    ChatToolRun,
    PendingInteraction,
    StoredChatMessage,
)
from src.server.routes.chat.store import _load_store

__all__ = [
    "router",
    "ChatImagePayload",
    "ChatMessage",
    "ChatResumeRequest",
    "ChatSession",
    "ChatSkillRun",
    "ChatStore",
    "ChatTeamMemberPayload",
    "ChatToolRun",
    "PendingInteraction",
    "StoredChatMessage",
    "_load_store",
]
