"""独立权限审核服务：冻结连接、严格结果、异常转人工，不覆盖当前硬边界。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import uuid4

from src.config.model_catalog import ModelCatalog, PermissionReviewConfig, read_catalog
from src.config.runtime_settings import RuntimeModelBinding, resolve_permission_review_binding

from .models import ActionSnapshot, AuthorizationDecision
from .prompt_provider import SmartGuardPrompt, load_smartguard_prompt, render_smartguard_user
from .review_contract import ReviewEvidence, parse_smartguard_token, smartguard_input

_DISCONNECT_TYPES = {
    "ConnectError", "ConnectTimeout", "ConnectionError", "ConnectionResetError",
    "RemoteProtocolError", "ReadError", "WriteError", "BrokenPipeError", "ProtocolError",
}


def _transport_failure(exc: BaseException) -> str:
    """把供应商故障收成限流、断流或连接撤销，不把正文带进决策。"""

    status = getattr(exc, "status_code", None)
    if status in {401, 403}:
        return "revoked"
    if status == 429:
        return "rate_limit"
    if type(exc).__name__ in _DISCONNECT_TYPES:
        return "disconnect"
    return "other"


@dataclass(frozen=True)
class PermissionReviewBinding:
    """新 Run 的独立审核快照；只持有 primary，连接不进入公开元数据。"""

    model_binding: RuntimeModelBinding = field(repr=False)
    config: PermissionReviewConfig
    prompt: SmartGuardPrompt = field(default_factory=load_smartguard_prompt)

    @property
    def version(self) -> str:
        return f"{self.model_binding.catalog.revision}:{self.config.model_config_id}:{self.prompt.version}:{self.prompt.digest[:12]}"


def freeze_permission_review(*, catalog: ModelCatalog | None = None) -> PermissionReviewBinding:
    """发送入口解析显式审核连接，与主执行模型独立。"""
    binding = resolve_permission_review_binding(catalog=catalog)
    config = binding.catalog.permission_review
    assert config is not None
    return PermissionReviewBinding(binding, config, load_smartguard_prompt())


def review_connection_available(binding: PermissionReviewBinding) -> bool:
    """目录参数修改不改本轮快照；删除、停用、连接/凭据撤销立即失效。"""
    catalog, _ = read_catalog()
    primary = binding.model_binding.primary
    entry = next((item for item in catalog.models if item.id == binding.config.model_config_id), None)
    provider = next((item for item in catalog.providers if item.id == primary.connection.id), None)
    return bool(entry and provider and entry.enabled and provider.enabled
                and entry.provider_id == primary.connection.id and entry.model == primary.entry.model
                and provider == primary.connection)


class PermissionReviewer:
    """同动作去重由权威 service 负责；此类每次只调用一个模型一次。"""

    def __init__(self, *, binding: PermissionReviewBinding,
                 evidence: Callable[[ActionSnapshot], Awaitable[ReviewEvidence]],
                 transport: Callable[..., Awaitable] | None = None,
                 available: Callable[[PermissionReviewBinding], bool] = review_connection_available,
                 observe: Callable[[dict], None] | None = None):
        self.binding = binding
        self.evidence = evidence
        self.available = available
        self.observe = observe
        if transport is None:
            from src.agents.permission_review_model import call_permission_review_model
            transport = call_permission_review_model
        self.transport = transport

    async def __call__(self, action: ActionSnapshot) -> AuthorizationDecision:
        started = time.perf_counter()
        primary = self.binding.model_binding.primary
        record = {"actionFingerprint": action.fingerprint, "runId": action.authorization.run_id,
                  "actorId": action.authorization.actor_id, "callId": action.call_id,
                  "modelConfigId": self.binding.config.model_config_id, "providerId": primary.provider,
                  "modelName": primary.default_model, "promptVersion": self.binding.prompt.version,
                  "promptDigest": self.binding.prompt.digest, "inputTokens": 0, "outputTokens": 0,
                  "modelCalled": False, "outcome": "not_called"}
        source, verdict, code, reason = "model_error", "needs_human", "review_unavailable", "自动审核不可用，请人工判断"
        try:
            if not self.available(self.binding):
                raise ValueError("审核模型连接已撤销")
            evidence = await self.evidence(action)
            if not evidence.complete or not evidence.user_goal.strip() or evidence.missing:
                code, reason = "insufficient_evidence", "行动证据不完整，请人工判断"
            else:
                payload = smartguard_input(
                    action,
                    user_goal=evidence.user_goal,
                    execution_context=" ".join(evidence.trusted_facts),
                    user_restrictions=evidence.user_restrictions,
                )
                prompt = render_smartguard_user(
                    self.binding.prompt,
                    trusted_json=json.dumps(payload["trusted"], ensure_ascii=False, sort_keys=True),
                    action=action.normalized_input_json,
                )
                record["modelCalled"] = True
                async with asyncio.timeout(self.binding.config.timeout_seconds):
                    response = await self.transport(
                        self.binding.model_binding,
                        system=self.binding.prompt.system,
                        prompt=prompt,
                        max_output_tokens=self.binding.config.max_output_tokens,
                    )
                record.update(inputTokens=response.input_tokens, outputTokens=response.output_tokens)
                if not self.available(self.binding):
                    raise ValueError("审核期间连接已撤销")
                text = response.text or ""
                if not text.strip():
                    record["outcome"] = "empty_or_truncated"
                    code, reason = "invalid_token", "审核模型没有返回有效决定，请人工判断"
                else:
                    try:
                        token = parse_smartguard_token(text)
                    except ValueError:
                        record["outcome"] = "invalid_token"
                        code, reason = "invalid_token", "审核模型没有返回有效决定，请人工判断"
                    else:
                        record["outcome"] = "token"
                        source, verdict = "model", token
                        code = f"smartguard_{token}"
                        reason = {"allow": "审核模型允许这一次", "deny": "审核模型拒绝这一次",
                                  "needs_human": "审核模型要求人工判断"}[token]
        except TimeoutError:
            record["outcome"] = "timeout"
            code, reason = "review_timeout", "自动审核超时，请人工判断"
        except asyncio.CancelledError:
            record.update(verdict="cancelled", reasonCode="review_cancelled", outcome="cancelled")
            raise
        except Exception as exc:
            # 原异常可能包含供应商响应或请求，审计只保留状态码，不保存原文。
            status = getattr(exc, "status_code", None)
            message = str(exc)
            kind = _transport_failure(exc)
            if status in {401, 403} or kind == "revoked":
                record["outcome"] = "provider_error"
                code, reason = "review_unavailable", "自动审核模型连接被拒绝或额度已失效，请到模型页更换模型"
            elif status == 429 or kind == "rate_limit":
                record["outcome"] = "provider_error"
                code, reason = "review_unavailable", "自动审核模型被限流，请稍后再试或人工判断"
            elif kind == "disconnect":
                record["outcome"] = "provider_error"
                code, reason = "review_failed", "自动审核连接中断，请人工判断"
            else:
                record["outcome"] = "empty_or_truncated" if "截断" in message or "中断" in message else "provider_error"
                code, reason = "review_failed", "自动审核服务或返回格式异常，请人工判断"
        finally:
            record.setdefault("verdict", verdict)
            record.setdefault("reasonCode", code)
            record["durationMs"] = round((time.perf_counter() - started) * 1000)
            if self.observe is not None:
                try:
                    self.observe(record)
                except Exception:
                    # 观测失败不能改写已完成的结论，也不能吞掉取消或触发第二次计费。
                    pass
        return AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=action.fingerprint,
                                     verdict=verdict, source=source, reasonCode=code, reason=reason,
                                     missingEvidence=tuple(evidence.missing) if 'evidence' in locals() else (),
                                     doNotRetry=False)
