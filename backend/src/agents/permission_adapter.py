"""固定 AgentScope 2.0.5 的集中授权接点；保留原生 ReAct 与事件生命周期。

由生产工厂统一安装。平台服务负责动作归属、硬边界和一次许可；
框架状态及 remembered rules 均不是平台授权证据。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import jsonschema
from agentscope.agent import Agent
from agentscope.exception import AgentOrientedException
from agentscope.event import (
    ExternalExecutionResultEvent,
    RequireExternalExecutionEvent,
    UserConfirmResultEvent,
)
from agentscope.message import ToolCallBlock, ToolCallState, ToolResultBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolBase, ToolResponse

from src.runtime.authorization import (
    AUTHORIZATION_ADAPTER_VERSION, ActionSnapshot, AuthorizationDecision, AuthorizationService,
    RunAuthorizationBinding,
)
from src.security.authorization.execution_context import execution_scope
from src.security.authorization.risk import classify_risk

ADAPTER_VERSION = AUTHORIZATION_ADAPTER_VERSION
ActionBuilder = Callable[[ToolBase, str, str], Awaitable[ActionSnapshot]]


@dataclass
class _CallContext:
    """每个并发 asyncio task 独有的调用，不在 engine 上存当前调用。"""

    tool: ToolBase
    action: ActionSnapshot
    builder: ActionBuilder
    decision: AuthorizationDecision | None = None


_call_context: ContextVar[_CallContext | None] = ContextVar("platform_permission_call", default=None)


def _execution_input(action: ActionSnapshot, builder: ActionBuilder) -> str:
    """受限快照只允许由创建者还原真实参数；旧内存契约仍可用纯 JSON。"""

    if action.restricted_input_ref:
        resolver = getattr(builder, "execution_input", None)
        if not callable(resolver):
            raise ValueError("受限行动参数不可读取")
        return resolver(action)
    return action.normalized_input_json


class _PlatformEngine:
    """只翻译平台结论，不读写框架的宽泛允许规则。"""

    def __init__(self, service: AuthorizationService):
        self.service = service

    def add_rule(self, rule: Any) -> None:
        """旧确认携带的规则不产生授权，硬拒绝由平台 policy 管理。"""

    async def check_permission(self, tool: ToolBase, tool_input: dict[str, Any]) -> PermissionDecision:
        """相同规范输入交给唯一服务；external 在分发前领取许可。"""

        ctx = _call_context.get()
        if ctx is None or ctx.tool is not tool or json.loads(_execution_input(ctx.action, ctx.builder)) != tool_input:
            return PermissionDecision(behavior=PermissionBehavior.DENY, message="授权调用上下文不匹配")
        decision = await self.service.authorize(ctx.action)
        ctx.decision = decision
        if decision.action_fingerprint != ctx.action.fingerprint:
            return PermissionDecision(behavior=PermissionBehavior.DENY, message="授权行动版本不匹配")
        behavior = {"allow": PermissionBehavior.ALLOW, "deny": PermissionBehavior.DENY,
                    "needs_human": PermissionBehavior.ASK}[decision.verdict]
        return PermissionDecision(behavior=behavior, message=decision.reason,
                                  decision_reason=decision.id, suggested_rules=[], bypass_immune=True)


class PlatformAgent(Agent):
    """仅包装调用入口与真实执行接点，不替换 reasoning 或复制执行循环。"""

    def __init__(self, *args: Any, authorization_service: AuthorizationService,
                 action_builder: ActionBuilder, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.authorization_service = authorization_service
        self.action_builder = action_builder
        self._engine = _PlatformEngine(authorization_service)
        self._pending_external: dict[str, _CallContext] = {}
        self._authorization_calls: dict[str, _CallContext] = {}
        self.authorization_binding: RunAuthorizationBinding | None = None
        self._attach_tool_catalog()

    def _attach_tool_catalog(self) -> None:
        attach = getattr(self.action_builder, "attach_tool_catalog", None)
        if callable(attach):
            attach(self.available_tool_names)

    async def available_tool_names(self) -> frozenset[str]:
        """只用 Toolkit 公开 schema 取得当前动态可见工具。"""
        schemas = await self.toolkit.get_tool_schemas(self.state.tool_context.activated_groups)
        return frozenset(schema["function"]["name"] for schema in schemas)

    def bind_authorization(self, binding: RunAuthorizationBinding) -> None:
        """每轮刷新服务与参数现场，旧框架 allow/suggested rules 不产生授权。"""

        previous = self.authorization_binding
        if previous is not None and previous.owner == binding.owner:
            # 同一 live Run 的恢复必须保留其 vault、external call 和一次许可现场。
            return
        if previous is not None and previous.owner.run_id != binding.owner.run_id:
            if self.state.has_awaiting_tool_calls(self.name) or self._pending_external:
                raise ValueError("等待中的 Agent 不能换绑其他 Run")
        self.authorization_binding = binding
        self.authorization_service = binding.service
        self.action_builder = binding.action_builder
        self._engine = _PlatformEngine(binding.service)
        self._attach_tool_catalog()
        self.state.permission_context.allow_rules.clear()
        self._pending_external.clear()
        self._authorization_calls.clear()

    def has_pending_authorization(self) -> bool:
        """工厂不得让下一轮覆盖等待中的授权现场。"""

        return self.state.has_awaiting_tool_calls(self.name) or bool(self._pending_external)

    async def _handle_incoming_event(self, event):
        """AskUser 的回答也消费原 external 现场；委托结果已由实际分发接点消费。"""
        if isinstance(event, UserConfirmResultEvent) and getattr(self.authorization_service, "store", None) is not None:
            if any(result.rules for result in event.confirm_results):
                raise ValueError("框架允许规则不能产生授权")
            calls = [result.tool_call.model_dump(mode="json") for result in event.confirm_results]
            if calls:
                self.answer_authorization(
                    calls,
                    approved=all(result.confirmed for result in event.confirm_results),
                    approved_by="human",
                )
        if isinstance(event, ExternalExecutionResultEvent):
            results = []
            for result in event.execution_results:
                ctx = self._pending_external.get(result.id)
                if ctx is not None:
                    identity = (type(ctx.tool).__module__, type(ctx.tool).__name__)
                    if identity != ("src.tools.ask_user", "AskUser"):
                        result = ToolResultBlock(id=result.id, name=ctx.action.tool_name, output=[], state=ToolResultState.DENIED)
                    else:
                        async def receive(call):
                            return result
                        result = await self.execute_authorized_external_tool_call(
                            {"id": result.id, "name": ctx.action.tool_name,
                             "input": _execution_input(ctx.action, ctx.builder)}, receive)
                results.append(result)
            event = event.model_copy(update={"execution_results": results})
        async for item in super()._handle_incoming_event(event):
            yield item

    async def _execute_tool_call(self, tool_call: ToolCallBlock, kept_rules=None):
        """严格规范化后委托 SDK；人工确认的 ALLOWED 同样回到平台检查。"""

        # SDK 在 context 中更新的 block 可能不是调用方手上的同一实例。
        last = self._get_last_msg()
        if last is not None:
            for stored in last.get_content_blocks("tool_call"):
                if stored.id == tool_call.id and stored.state in (ToolCallState.FINISHED, ToolCallState.SUBMITTED):
                    return
        if tool_call.state in (ToolCallState.FINISHED, ToolCallState.SUBMITTED):
            return
        try:
            tool = await self.toolkit.check_tool_available(tool_call.name, self.state.tool_context.activated_groups)
            if tool.is_mcp and callable(getattr(self.action_builder, "register_tool_identity", None)):
                # 原生 tool.name 可能经过 SDK 清洗；绑定服务器真正调用的身份和完整 schema。
                self.action_builder.register_tool_identity(tool, lambda: {
                    "server": tool.mcp_name, "tool": tool._tool.model_dump(mode="json"),
                })
            canonical = ActionSnapshot.canonical_json(tool_call.input)
            jsonschema.validate(json.loads(canonical), tool.input_schema)
            action = await self.action_builder(tool, tool_call.id, canonical)
            if action.call_id != tool_call.id or action.tool_name != tool.name:
                raise ValueError("动作构造归属不匹配")
            jsonschema.validate(json.loads(_execution_input(action, self.action_builder)), tool.input_schema)
        except (ValueError, jsonschema.ValidationError, AgentOrientedException):
            async for event in self._handle_error_tool_call(
                tool_call, "工具输入或授权快照无效", state=ToolResultState.ERROR,
            ):
                yield event
            return

        tool_call.input = _execution_input(action, self.action_builder)
        tool_call.suggested_rules = []
        if tool_call.state == ToolCallState.ALLOWED:
            self._update_tool_call_state(tool_call.id, ToolCallState.PENDING)
            tool_call.state = ToolCallState.PENDING
        token = _call_context.set(_CallContext(tool=tool, action=action, builder=self.action_builder))
        try:
            async for event in super()._execute_tool_call(tool_call, kept_rules):
                self._authorization_calls[tool_call.id] = _call_context.get()
                if isinstance(event, RequireExternalExecutionEvent):
                    self._pending_external[tool_call.id] = _call_context.get()
                yield event
        finally:
            # 审批暂停会在另一个任务里关闭这个异步生成器，原 ContextVar token 不能跨上下文 reset。
            try:
                _call_context.reset(token)
            except ValueError:
                _call_context.set(None)

    def authorization_details(self, call_id: str) -> dict | None:
        """只交付普通审计合同，不向路由泄露 SDK 或受限执行参数。"""
        ctx = self._authorization_calls.get(call_id)
        if ctx is None or ctx.decision is None:
            return None
        action, decision = ctx.action, ctx.decision
        risk = classify_risk(action)
        from src.security.authorization.display_input import display_input

        result = {"version": 2, "decisionId": decision.id, "actionFingerprint": action.fingerprint,
                "approvalMode": action.authorization.approval_mode, "actorId": action.authorization.actor_id,
                "source": decision.source, "verdict": decision.verdict, "reasonCode": decision.reason_code,
                "reason": str(display_input(decision.reason)), "effect": action.effect,
                "targets": [target.target for target in action.targets],
                "riskClass": risk.risk_class, "riskRule": risk.rule_description,
                "reviewable": decision.reason_code == "human_review_of_model_deny"}
        display = getattr(ctx.builder, "display_action", None)
        if callable(display):
            result.update(display(action))
        store = getattr(self.authorization_service, "store", None)
        if store is not None and decision.source != "boundary":
            approval = store.get_approval(decision.id, action.authorization)
            if approval is not None:
                result.update(approvalId=approval.id, status=approval.status, expiresAt=approval.expires_at,
                              callId=call_id, toolName=action.tool_name)
                result.setdefault("summary", result["reason"])
            execution = store.get_execution(decision.id, action.authorization)
            if execution is not None:
                result.update(executionId=execution.id, executionStatus=execution.status)
            if decision.related_decision_id:
                original = store.get_decision(decision.related_decision_id, action.authorization)
                if original is not None:
                    result["originalDecision"] = {
                        "id": original.id, "verdict": original.verdict,
                        "source": original.source, "reasonCode": original.reason_code,
                    }
        return result

    def answer_authorization(self, calls: list[dict], *, approved: bool, approved_by: str) -> None:
        """只批准服务端暂停列表对应的精确 decision，不消费未来调用或客户端替换输入。"""
        store = getattr(self.authorization_service, "store", None)
        if store is None:
            raise ValueError("权限审批存储不可用")
        contexts = []
        for call in calls:
            ctx = self._authorization_calls.get(str(call.get("id") or ""))
            if ctx is None or ctx.decision is None or ctx.decision.verdict != "needs_human":
                raise ValueError("待批准行动不存在")
            if (str(call.get("name")) != ctx.action.tool_name
                    or ActionSnapshot.canonical_json(call.get("input", "{}")) != _execution_input(ctx.action, ctx.builder)):
                raise ValueError("待批准行动参数已变化")
            approval = store.get_approval(ctx.decision.id, ctx.action.authorization)
            if approval is None or approval.status != "pending":
                raise ValueError("审批已处理或失效")
            contexts.append(ctx)
        for ctx in contexts:
            if not store.answer(ctx.decision.id, ctx.action.authorization, approved=approved, approved_by=approved_by):
                raise ValueError("审批已处理或运行已失效")
        if approved and contexts:
            reset = getattr(self.authorization_service, "reset_denials", None)
            if callable(reset):
                reset(contexts[0].action.authorization)

    async def _acting_impl(self, tool_call: ToolCallBlock):
        """在所有 acting middleware 后核对实际参数并领取一次许可。"""

        ctx = _call_context.get()
        if (ctx is None or ctx.decision is None or tool_call.id != ctx.action.call_id
                or tool_call.name != ctx.action.tool_name):
            yield ToolResponse(state=ToolResultState.DENIED)
            return
        try:
            current = await self.action_builder(ctx.tool, tool_call.id,
                                               ActionSnapshot.canonical_json(tool_call.input))
            if current.fingerprint != ctx.action.fingerprint:
                raise ValueError("待执行动作已变化")
            await self.authorization_service.reserve_execution(current, ctx.decision.id)
            await self.authorization_service.start_execution(current, ctx.decision.id)
        except ValueError:
            yield ToolResponse(state=ToolResultState.DENIED)
            return
        terminal = "unknown"
        try:
            with execution_scope(current):
                async for item in super()._acting_impl(tool_call):
                    if isinstance(item, ToolResponse) and item.state == ToolResultState.SUCCESS:
                        terminal = "succeeded"
                    yield item
        finally:
            # ERROR 不足以证明没有副作用，断流/取消同样不自动重发。
            await self.authorization_service.finish_execution(current, ctx.decision.id, status=terminal)

    async def execute_authorized_external_tool_call(
        self, tool_call: dict[str, Any], executor: Callable[[dict[str, Any]], Awaitable[ToolResultBlock]],
    ) -> ToolResultBlock:
        """external 的实际分发接点；暂停事件本身不消费许可。"""

        call_id = str(tool_call.get("id") or "")
        ctx = self._pending_external.pop(call_id, None)
        if ctx is None or ctx.decision is None:
            return ToolResultBlock(id=call_id, name=str(tool_call.get("name") or ""),
                                   output=[], state=ToolResultState.DENIED)
        try:
            current = await self.action_builder(
                ctx.tool, call_id, ActionSnapshot.canonical_json(tool_call.get("input", "")),
            )
            if tool_call.get("name") != ctx.action.tool_name or current.fingerprint != ctx.action.fingerprint:
                raise ValueError("外部行动已变化")
            await self.authorization_service.reserve_execution(current, ctx.decision.id)
            await self.authorization_service.start_execution(current, ctx.decision.id)
        except ValueError:
            return ToolResultBlock(id=call_id, name=ctx.action.tool_name, output=[], state=ToolResultState.DENIED)
        terminal = "unknown"
        try:
            with execution_scope(current):
                result = await executor({**tool_call, "input": _execution_input(current, self.action_builder)})
            if result.id != call_id or result.name != ctx.action.tool_name:
                raise ValueError("外部结果归属不匹配")
            if result.state == ToolResultState.SUCCESS:
                terminal = "succeeded"
            return result
        finally:
            await self.authorization_service.finish_execution(current, ctx.decision.id, status=terminal)
