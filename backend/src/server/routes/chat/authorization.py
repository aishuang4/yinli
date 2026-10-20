"""Chat 单 Agent 授权现场组装；模式取自冻结 Run，不读取 Agent 审批偏好。"""

import re
from dataclasses import asdict

from src.runtime.authorization import PlatformAuthorizationService, RunAuthorization, RunAuthorizationBinding
from src.runtime.authorization_resources import RuntimeCapabilityReader
from src.security.authorization.actions import RuntimeActionBuilder
from src.security.authorization.boundaries import RuntimeAuthorizationBoundary
from src.security.authorization.review_contract import ReviewEvidence
from src.security.authorization.reviewer import PermissionReviewer
from src.server.routes.chat.background_runs import current_chat_background_run_id, get_chat_background_run
from src.server.routes.chat.plan_mode.policy import PLAN_CONTROL_TOOL_NAMES
from src.tenancy.context import current_tenant


async def build_chat_authorization(
    ctx,
    *,
    plan_mode_manager=None,
    actor_id: str | None = None,
    run_id: str | None = None,
    restriction=None,
) -> RunAuthorizationBinding:
    """公开资源读取和 SDK 工具目录组合，每次执行重验当前能力。"""
    run = await get_chat_background_run(run_id or current_chat_background_run_id())
    if run is None or run.authorization_metadata is None:
        raise ValueError("缺少本轮授权快照")
    metadata = run.authorization_metadata
    capabilities = RuntimeCapabilityReader(context=ctx.runtime_context, actor_id=ctx.agent_id,
                                           tool_scope=ctx.runtime_tool_scope,
                                           denied_tool_names=ctx.tool_name_denylist,
                                           extra_filesystem_scopes=ctx.extra_filesystem_scopes)
    version, guardrails, mcp, _ = capabilities.snapshot()
    owner = RunAuthorization(tenantId=metadata.tenant_id, workspaceId="",
                             projectId="", sessionId=ctx.session_id, runId=run.id,
                             actorId=actor_id or ctx.agent_id, approvalMode=metadata.approval_mode,
                             capabilityVersion=version, reviewerConfigVersion=metadata.reviewer_config_version)
    # 已知 URL 只来自这次人类输入；网页或 Skill 的自述不能升级为授权来源。
    known_urls = frozenset(re.findall(r'https?://[^\s<>"\']+', run.task_text))
    builder = RuntimeActionBuilder(owner, filesystem_scope=guardrails.filesystem_scope,
                                   extra_filesystem_scopes=tuple(ctx.extra_filesystem_scopes),
                                   known_urls=known_urls, mcp_connections={item.name: asdict(item) for item in mcp})
    capabilities.tool_catalog = builder.available_tool_names

    async def domain_check(action, tool, raw):
        if restriction is not None:
            from src.tools.scoped_paths import resolve_workspace_path

            module = type(tool).__module__
            is_shell_or_network = bool(tool.is_mcp) or module in {
                "src.agents.guarded_bash", "src.tools.http_request", "src.tools.configured_http_tool",
                "src.tools.knowledge_base_search", "src.tools.web_fetch",
            }
            if restriction.permission_profile.is_read_only and (
                action.effect not in {"read_only", "output"} or is_shell_or_network
            ):
                return "子执行者只读能力不允许此行动"
            for target in action.targets:
                if target.target.startswith("/"):
                    try:
                        resolve_workspace_path(target.target, scope_dir=restriction.filesystem_scope)
                    except ValueError:
                        return "行动超出子执行者文件范围"
        if plan_mode_manager is not None and plan_mode_manager.is_plan_active():
            # 控制工具的身份也要精确登记，名称不能替代 effects 证据。
            control = (type(tool).__module__ == "src.server.routes.chat.plan_mode.tools"
                       and action.tool_name in PLAN_CONTROL_TOOL_NAMES)
            if not control and action.effect not in {"read_only", "output"}:
                return "Plan 只读阶段不允许此行动"
        return None

    boundary = RuntimeAuthorizationBoundary(builder=builder, capabilities=capabilities, domain_check=domain_check)
    reviewer = None
    if metadata.approval_mode == "model_review":
        if run.permission_review_binding is None:
            raise ValueError("本轮审核模型现场已失效")

        async def evidence(action):
            # 不根据主 Agent 的推断生成用户限制；完整人类目标内保留原句。
            return ReviewEvidence(user_goal=run.task_text, action_input_json=builder.execution_input(action),
                                  trusted_facts=(f"行动影响：{action.effect}",))

        from src.server.routes.chat.authorization_observation import observe_permission_review

        reviewer = PermissionReviewer(binding=run.permission_review_binding, evidence=evidence,
                                      observe=lambda record: observe_permission_review(run, record))
    service = PlatformAuthorizationService(boundary_check=boundary, revalidate=builder.revalidate, reviewer=reviewer)
    binding = RunAuthorizationBinding(owner, service, builder)
    run.authorization_bindings.append(binding)
    return binding
