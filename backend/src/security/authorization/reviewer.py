"""独立权限审核服务：冻结连接、严格结果、异常转人工，不覆盖当前硬边界。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import uuid4

from src.config.model_catalog import ModelCatalog, PermissionReviewConfig, read_catalog
from src.config.runtime_settings import RuntimeModelBinding, resolve_permission_review_binding

from .models import ActionSnapshot, AuthorizationDecision
from .review_contract import PERMISSION_REVIEW_SYSTEM, ReviewEvidence, parse_permission_review, review_prompt


@dataclass(frozen=True)
class PermissionReviewBinding:
    """新 Run 的独立审核快照；只持有 primary，连接不进入公开元数据。"""

    model_binding: RuntimeModelBinding = field(repr=False)
    config: PermissionReviewConfig

    @property
    def version(self) -> str:
        return f"{self.model_binding.catalog.revision}:{self.config.model_config_id}:{self.config.prompt_version}"


def freeze_permission_review(*, catalog: ModelCatalog | None = None) -> PermissionReviewBinding:
    """发送入口解析显式审核连接，与主执行模型独立。"""
    binding = resolve_permission_review_binding(catalog=catalog)
    config = binding.catalog.permission_review
    assert config is not None
    return PermissionReviewBinding(binding, config)


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
                  "modelName": primary.default_model, "promptVersion": self.binding.config.prompt_version,
                  "schemaVersion": self.binding.config.schema_version, "inputTokens": 0, "outputTokens": 0,
                  "modelCalled": False}
        source, verdict, code, reason = "model_error", "needs_human", "review_unavailable", "自动审核不可用，请人工判断"
        try:
            if not self.available(self.binding):
                raise ValueError("审核模型连接已撤销")
            evidence = await self.evidence(action)
            secrets = tuple(provider.api_key for provider in self.binding.model_binding.catalog.providers if provider.api_key)
            prompt, complete = review_prompt(action, evidence, secrets=secrets)
            if not complete:
                code, reason = "insufficient_evidence", "行动证据不完整或含需隐藏的内容，请人工判断"
            else:
                record["modelCalled"] = True
                async with asyncio.timeout(self.binding.config.timeout_seconds):
                    response = await self.transport(self.binding.model_binding, system=PERMISSION_REVIEW_SYSTEM,
                                                    prompt=prompt, max_output_tokens=self.binding.config.max_output_tokens)
                record.update(inputTokens=response.input_tokens, outputTokens=response.output_tokens)
                if not self.available(self.binding):
                    raise ValueError("审核期间连接已撤销")
                result = parse_permission_review(response.text)
                if (result.verdict == "deny" and
                    ((result.reason_code == "explicit_restriction" and not evidence.user_restrictions) or
                     (result.reason_code == "known_risk" and not evidence.trusted_facts))):
                    code, reason = "insufficient_evidence", "模型拒绝缺少对应用户限制或平台事实，请人工判断"
                elif any(secret and secret in result.reason for secret in secrets):
                    code, reason = "invalid_response", "审核结果包含敏感内容，请人工判断"
                else:
                    source, verdict, code, reason = "model", result.verdict, result.reason_code, result.reason
        except TimeoutError:
            code, reason = "review_timeout", "自动审核超时，请人工判断"
        except asyncio.CancelledError:
            record.update(verdict="cancelled", reasonCode="review_cancelled")
            raise
        except Exception as exc:
            # 原异常可能包含供应商响应或请求，审计只保留状态码，不保存原文。
            status = getattr(exc, "status_code", None)
            if status in {401, 403}:
                code, reason = "review_unavailable", "自动审核模型连接被拒绝或额度已失效，请到模型页更换模型"
            elif status == 429:
                code, reason = "review_unavailable", "自动审核模型被限流，请稍后再试或人工判断"
            else:
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
                                     doNotRetry=verdict == "deny")
