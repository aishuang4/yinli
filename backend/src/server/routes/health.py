"""
健康检查路由

提供后端服务状态、版本信息等端点。
前端通过此接口检测后端连接状态。
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
async def health_check() -> dict:
    """
    健康检查端点

    前端定期轮询此接口以判断后端是否可用。

    Returns:
        dict: 包含 status、version、timestamp 的状态信息
    """
    from datetime import datetime, timezone

    return {
        "status": "ok",
        "version": "0.1.0",
        "framework": "AgentScope 2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
