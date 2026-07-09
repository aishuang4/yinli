"""
FastAPI 服务入口 — Agent Workstation 后端

职责：
1. 创建 FastAPI 应用实例
2. 注册 CORS 中间件（开发模式允许前端跨域）
3. 注册 API 路由
4. 启动/关闭生命周期事件（初始化 DB、AgentScope 等）
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.server.routes import agent_builder, agents, chat, health, mcp, memory, sandbox, settings as settings_routes, skills, subagents, teams, tools, traces
from src.agents.agentscope_usage_patch import patch_agentscope_usage_events
from src.config.settings import settings
from src.storage.database import init_db, close_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期管理

    启动时：初始化数据库、AgentScope 配置
    关闭时：关闭数据库连接、清理资源
    """
    # === 启动 ===
    patch_agentscope_usage_events()
    await init_db()
    print(f"[Server] Agent Workstation Backend started on port {settings.port}")
    deepseek_key_display = ("***" + settings.deepseek_api_key[-4:]) if settings.deepseek_api_key else "NOT SET"
    print(f"[Server] DeepSeek API Key: {deepseek_key_display}")

    yield  # 应用运行中

    # === 关闭 ===
    await close_db()
    print("[Server] Backend shutdown complete")


# 创建 FastAPI 应用
app = FastAPI(
    title="Agent Workstation API",
    description="基于 AgentScope 2.0 的多 Agent 协作工作台后端服务",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置 — 开发模式允许前端 dev server 跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health.router, tags=["健康检查"])
app.include_router(agents.router, prefix="/api/agents", tags=["Agent 管理"])
app.include_router(agent_builder.router, prefix="/api/agent-builder", tags=["Agent 智能编排"])
app.include_router(chat.router, prefix="/api/chat", tags=["对话"])
app.include_router(mcp.router, prefix="/api/mcp", tags=["MCP 管理"])
app.include_router(tools.router, prefix="/api/tools", tags=["工具管理"])
app.include_router(skills.router, prefix="/api/skills", tags=["Skills 管理"])
app.include_router(subagents.router, prefix="/api/subagents", tags=["Subagent 管理"])
app.include_router(memory.router, prefix="/api/memory", tags=["Memory 管理"])
app.include_router(teams.router, prefix="/api/teams", tags=["Team 管理"])
app.include_router(settings_routes.router, prefix="/api/settings", tags=["系统设置"])
app.include_router(traces.router, prefix="/api/traces", tags=["运行轨迹"])
app.include_router(sandbox.router, prefix="/api/sandbox", tags=["测试沙盒"])
