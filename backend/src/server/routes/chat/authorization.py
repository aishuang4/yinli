"""把當前 Chat Run 收成唯一平台授權現場。

Leader、Team Member、Subagent 與 external action 都從這裡繼承模式和能力上限。
沒有已落庫的 live Run 時不綁定，基礎設施 Agent 仍走原工廠。
"""

from __future__ import annotations

from src.runtime.authorization import RunAuthorizationBinding
from src.runtime.authorization_resources import RuntimeCapabilityReader
from src.security.authorization.actions import RuntimeActionBuilder
from src.security.authorization.boundaries import RuntimeAuthorizationBoundary
from src.security.authorization.models import RunAuthorization, RunAuthorizationMetadata
from src.security.authorization.service import PlatformAuthorizationService
from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.plan_mode.policy import is_plan_mode_tool_allowed
from src.storage.database import get_chat_run, upsert_chat_run
from src.tenancy.context import current_tenant


async def get_chat_background_run(run_id: str):
    """讀取已落庫的 Run。測試可替換，用來提供凍結的審核元數據。"""

    return get_chat_run(run_id)


def _metadata_from_record(record) -> RunAuthorizationMetadata | None:
    if record is None:
        return None
    metadata = getattr(record, "authorization_metadata", None)
    if isinstance(metadata, RunAuthorizationMetadata):
        return metadata
    raw = record.get("authorization") if isinstance(record, dict) else None
    if not raw:
        return None
    return RunAuthorizationMetadata.model_validate(raw)


async def build_chat_authorization(
    ctx,
    *,
    actor_id: str | None = None,
    run_id: str | None = None,
    restriction=None,
    plan_mode_manager=None,
) -> RunAuthorizationBinding:
    """用已凍結的 Run 模式和當前能力建立 Leader 授權。"""

    del restriction
    resolved_run_id = run_id or current_chat_background_run_id() or str(getattr(ctx.runtime_context, "run_id", "") or "")
    if not resolved_run_id:
        raise ValueError("授权 Run 不存在")
    record = await get_chat_background_run(resolved_run_id)
    metadata = _metadata_from_record(record)
    tenant_id = current_tenant().tenant_id
    if metadata is None:
        mode = str(getattr(ctx, "approval_mode", "manual") or "manual")
        if mode not in {"manual", "model_review", "full_access"}:
            mode = "manual"
        metadata = RunAuthorizationMetadata(tenantId=tenant_id, approvalMode=mode)
        upsert_chat_run({
            "id": resolved_run_id,
            "authorization": metadata.model_dump(mode="json", by_alias=True),
        })
    if metadata.tenant_id != tenant_id:
        raise ValueError("Run 授权租户不匹配")
    actor = actor_id or str(getattr(ctx, "agent_id", "") or "")
    reader = RuntimeCapabilityReader(
        context=ctx.runtime_context,
        actor_id=str(getattr(ctx, "agent_id", "") or actor),
        tool_scope=ctx.runtime_tool_scope,
        denied_tool_names=frozenset(getattr(ctx, "tool_name_denylist", None) or ()),
        extra_filesystem_scopes=tuple(getattr(ctx, "extra_filesystem_scopes", None) or ()),
    )
    version, guardrails, _mcp, _knowledge = reader.snapshot()
    owner = RunAuthorization(
        tenantId=tenant_id,
        workspaceId=ctx.runtime_context.workspace_id,
        projectId=ctx.runtime_context.project_id,
        sessionId=str(getattr(ctx, "session_id", "") or ctx.runtime_context.session_id),
        runId=resolved_run_id,
        actorId=actor,
        approvalMode=metadata.approval_mode,
        capabilityVersion=version,
        reviewerConfigVersion=metadata.reviewer_config_version,
        surface="job" if getattr(ctx, "trigger_source", "user") == "scheduled_job" else "interactive",
    )
    builder = RuntimeActionBuilder(
        owner,
        filesystem_scope=guardrails.filesystem_scope,
        extra_filesystem_scopes=tuple(getattr(ctx, "extra_filesystem_scopes", None) or ()),
    )
    reader.tool_catalog = builder.available_tool_names

    async def domain_check(action, tool, raw):
        del tool
        if plan_mode_manager is not None and plan_mode_manager.is_plan_active():
            if not is_plan_mode_tool_allowed(action.tool_name, raw):
                return "Plan Mode 不允许此变更"
        return None

    boundary = RuntimeAuthorizationBoundary(builder=builder, capabilities=reader, domain_check=domain_check)
    service = PlatformAuthorizationService(boundary_check=boundary, revalidate=builder.revalidate)
    return RunAuthorizationBinding(owner=owner, service=service, action_builder=builder)


async def attach_live_run_authorization(ctx, *, plan_mode_manager=None) -> RunAuthorizationBinding | None:
    """只有背景 Run 已落庫時才綁定。單測沒有 Run 列時維持原 Agent。"""

    existing = getattr(ctx, "platform_binding", None)
    if existing is not None:
        return existing
    run_id = current_chat_background_run_id()
    if not run_id or get_chat_run(run_id) is None:
        return None
    binding = await build_chat_authorization(ctx, run_id=run_id, plan_mode_manager=plan_mode_manager)
    ctx.platform_binding = binding
    ctx.platform_denied = frozenset(getattr(ctx, "tool_name_denylist", None) or ())
    ctx.platform_filesystem = binding.action_builder.filesystem_scope
    return binding
