"""Memory API 路由。

习惯、专业区、时间线、旧 Markdown、历史节点和设置分在 memory_api。
"""

from fastapi import APIRouter

from src.server.routes.memory_api.live import (
    read_habits_memory,
    read_memory_timeline,
    read_professional_memory,
    router as live_router,
    write_habits_memory,
    write_professional_memory,
)
from src.server.routes.memory_api.markdown import (
    list_markdown_candidates,
    read_agent_markdown_memory,
    read_default_global_markdown_memory,
    read_default_user_markdown_memory,
    read_user_markdown_memory,
    router as markdown_router,
    update_markdown_candidate,
    write_agent_markdown_memory,
    write_default_global_markdown_memory,
    write_default_user_markdown_memory,
    write_user_markdown_memory,
)
from src.server.routes.memory_api.models import (
    MarkdownCandidateAction,
    MarkdownCandidateListResponse,
    MarkdownMemoryUpdate,
    MemoryAdmissionPolicyUpdate,
    MemoryAuditEventListResponse,
    MemoryNodeCreate,
    MemoryNodeStatusUpdate,
    MemoryRetrievalFeedback,
    MemorySettingsResponse,
)
from src.server.routes.memory_api.nodes import (
    get_node,
    node_query,
    node_tree,
    router as nodes_router,
    update_node_status,
    upsert_node,
)
from src.server.routes.memory_api.settings import (
    memory_audit_events,
    memory_settings,
    record_retrieval_feedback,
    router as settings_router,
    update_memory_admission_settings,
)
router = APIRouter()
router.include_router(nodes_router)
router.include_router(markdown_router)
router.include_router(live_router)
router.include_router(settings_router)
