"""
FastAPI 服务入口 — 引力 YINLI 后端

职责：
1. 创建 FastAPI 应用实例
2. 注册 CORS 中间件（开发模式允许前端跨域）
3. 注册 API 路由
4. 启动/关闭生命周期事件（初始化 DB、AgentScope 等）
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.server.routes import agent_builder, agents, chat, db_admin, group_studio, health, mcp, memory, notifications, sandbox, settings as settings_routes, scheduled_jobs, skills, subagents, tools, traces, workflows, workspaces
from src.config.settings import assert_hosted_email_config, assert_hosted_platform_config, assert_hosted_sandbox, settings
from src.server.auth.middleware import HostedSessionMiddleware
from src.server.auth.platform_models import router as platform_model_router
from src.server.platform_gateway import router as platform_gateway_router
from src.server.auth.routes import admin_router, auth_router
from src.server.auth.service import ensure_default_admins
from src.storage.control_db import ensure_control_db
from src.tenancy.context import is_hosted_mode
from src.group_studio.lifecycle import reconcile_interrupted_studios
from src.storage.database import close_db, init_db, list_projects, reconcile_interrupted_subagent_invocations
from src.subagents.task_store import reconcile_interrupted_file_tasks
from src.server.routes.chat.team_runtime.maintenance import maintain_team_runtime_runs
from src.server.local_access import LocalAccessMiddleware
from src.server.product_feature_gate import ProductFeatureGateMiddleware
from src.config.product_features import load_product_features
from src.scheduler.ticker import get_job_scheduler, shutdown_scheduler
from src.config.runtime_paths import ensure_process_directories, ensure_tenant_directories
from src.tenancy.context import use_tenant
from src.tenancy.lifecycle import startup_tenants

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期管理

    启动时：初始化数据库、AgentScope 配置
    关闭时：关闭数据库连接、清理资源
    """
    # === 启动 ===
    assert_hosted_email_config()
    from src.config.secret_store import assert_hosted_secret_key

    assert_hosted_secret_key()
    assert_hosted_platform_config()
    assert_hosted_sandbox()
    ensure_process_directories()
    if is_hosted_mode():
        ensure_control_db()
        ensure_default_admins()
    from src.scheduler.ticker import bootstrap_tenant_scheduler

    for ctx in startup_tenants():
        with use_tenant(ctx):
            try:
                ensure_tenant_directories()
                await init_db()
                from src.skills.registry import reload_skill_cache

                reload_skill_cache()
                interrupted_subagents = reconcile_interrupted_subagent_invocations()
                interrupted_subagent_files = reconcile_interrupted_file_tasks([
                    Path(str(project.get("rootPath") or ""))
                    for project in list_projects(include_archived=True)
                    if str(project.get("rootPath") or "")
                ])
                if interrupted_subagents or interrupted_subagent_files:
                    print(
                        "[Subagent] reconciled "
                        f"{interrupted_subagents} index task(s), "
                        f"{interrupted_subagent_files} file task(s) tenant={ctx.tenant_id}"
                    )
                team_maintenance = maintain_team_runtime_runs(
                    retention_days=settings.team_run_retention_days,
                    max_records=settings.team_run_max_records,
                )
                if any(team_maintenance.values()):
                    print(f"[TeamRuntime] maintenance tenant={ctx.tenant_id}: {team_maintenance}")
                scheduler_bootstrap = bootstrap_tenant_scheduler()
                if any(scheduler_bootstrap.values()):
                    print(f"[Scheduler] bootstrap tenant={ctx.tenant_id}: {scheduler_bootstrap}")
                studio_reconciled = reconcile_interrupted_studios()
                if studio_reconciled:
                    print(
                        f"[GroupStudio] reconciled {studio_reconciled} "
                        f"interrupted studio(s) tenant={ctx.tenant_id}"
                    )
            except Exception:
                logger.exception("租户启动对账失败 tenant=%s", ctx.tenant_id)
    get_job_scheduler().start_if_needed()
    print(f"[Server] 引力 YINLI Backend started on {settings.host}:{settings.port}")
    product_features = load_product_features()
    disabled = [
        *[key for key, enabled in product_features.views.items() if not enabled],
        *[key for key, enabled in product_features.surfaces.items() if not enabled],
    ]
    source = product_features.source_path or "built-in defaults"
    print(f"[Server] Product features: {source}; disabled={disabled or 'none'}")
    if not is_hosted_mode():
        try:
            from src.runtime.agent_python import ensure_agent_venv

            agent_venv = ensure_agent_venv()
            if agent_venv is not None:
                print(f"[Server] Agent Python: {agent_venv.python}")
            else:
                print("[Server] Agent Python: 隔离环境不可用，Bash 回退到用户 PATH")
        except Exception as exc:
            print(f"[Server] Agent Python 环境初始化失败: {exc}")
    deepseek_key_display = ("***" + settings.deepseek_api_key[-4:]) if settings.deepseek_api_key else "NOT SET"
    print(f"[Server] DeepSeek API Key: {deepseek_key_display}")

    yield  # 应用运行中

    # === 关闭 ===
    await shutdown_scheduler()
    await close_db()
    print("[Server] Backend shutdown complete")


# 创建 FastAPI 应用
app = FastAPI(
    title="引力 YINLI API",
    description="基于 AgentScope 2.0 的本地优先办公 AI 工作台后端服务",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置 — 开发模式允许前端 dev server 跨域
# 本地访问中间件必须覆盖全部路由；即使误绑定到 0.0.0.0，也不接受局域网请求。
app.add_middleware(HostedSessionMiddleware)
app.add_middleware(LocalAccessMiddleware)
app.add_middleware(ProductFeatureGateMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health.router, tags=["健康检查"])
app.include_router(db_admin.router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(platform_model_router)
app.include_router(platform_gateway_router)
app.include_router(workspaces.router, prefix="/api", tags=["Workspace / Project"])
app.include_router(agents.router, prefix="/api/agents", tags=["Agent 管理"])
app.include_router(agent_builder.router, prefix="/api/agent-builder", tags=["Agent 智能编排"])
app.include_router(chat.router, prefix="/api/chat", tags=["对话"])
app.include_router(mcp.router, prefix="/api/mcp", tags=["MCP 管理"])
app.include_router(tools.router, prefix="/api/tools", tags=["工具管理"])
app.include_router(skills.router, prefix="/api/skills", tags=["Skills 管理"])
app.include_router(subagents.router, prefix="/api/subagents", tags=["Subagent 管理"])
app.include_router(memory.router, prefix="/api/memory", tags=["Memory 管理"])
app.include_router(settings_routes.router, prefix="/api/settings", tags=["系统设置"])
app.include_router(traces.router, prefix="/api/traces", tags=["运行轨迹"])
app.include_router(sandbox.router, prefix="/api/sandbox", tags=["测试沙盒"])
app.include_router(workflows.router, prefix="/api/workflows", tags=["Dynamic Workflow"])
app.include_router(group_studio.router, prefix="/api/group-studio", tags=["群聊工作室"])
app.include_router(scheduled_jobs.router, prefix="/api", tags=["定时任务"])
app.include_router(notifications.router, prefix="/api", tags=["通知"])
