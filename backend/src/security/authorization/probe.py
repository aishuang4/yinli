"""模型页保存 SmartGuard 前的一次无害探测。失败不写入新配置。"""

from __future__ import annotations

import asyncio
import json

from src.config.model_catalog import CatalogError, ModelCatalog, PermissionReviewConfig, read_catalog
from src.config.runtime_settings import RuntimeModelBinding, resolve_model_binding

from .prompt_provider import load_smartguard_prompt, render_smartguard_user
from .review_contract import parse_smartguard_token

PROBE_BUDGET_REASON = "该模型在审核预算内无法给出结果"
PROBE_TOKEN_REASON = "该模型没有返回合法审核结果"
PROBE_CONNECTION_REASON = "无法连接该审核模型"

_PROBE_TRUSTED = {
    "riskClass": "trusted_readonly",
    "riskRule": "保存前探测，不代表任何用户行动",
    "tool": "Read",
    "effect": "read",
    "target": "README.md",
    "executionContext": "smartguard-probe",
    "userGoal": "确认审核模型能在输出预算内返回一个词",
    "userRestrictions": [],
}
_PROBE_ACTION = json.dumps({"tool": "Read", "input": {"path": "README.md"}}, ensure_ascii=False)


def _review_config(payload: dict) -> PermissionReviewConfig | None:
    raw = payload.get("permissionReview")
    if not raw:
        return None
    return PermissionReviewConfig.model_validate(raw)


def _same_passed_config(previous: PermissionReviewConfig | None, incoming: PermissionReviewConfig) -> bool:
    return bool(
        previous
        and previous.probe_status == "passed"
        and previous.model_config_id == incoming.model_config_id
        and previous.max_output_tokens == incoming.max_output_tokens
        and previous.timeout_seconds == incoming.timeout_seconds
    )


async def probe_smartguard(catalog: ModelCatalog, config: PermissionReviewConfig, *, transport=None) -> str:
    """调用一次固定只读动作。返回合法 token；失败抛 CatalogError。"""

    binding = resolve_model_binding(config.model_config_id, catalog=catalog, include_fallbacks=False)
    runtime = RuntimeModelBinding(catalog, (binding.primary,))
    prompt = load_smartguard_prompt()
    user = render_smartguard_user(
        prompt,
        trusted_json=json.dumps(_PROBE_TRUSTED, ensure_ascii=False, sort_keys=True),
        action=_PROBE_ACTION,
    )
    caller = transport
    if caller is None:
        from src.agents.permission_review_model import call_permission_review_model
        caller = call_permission_review_model
    try:
        async with asyncio.timeout(config.timeout_seconds):
            response = await caller(
                runtime,
                system=prompt.system,
                prompt=user,
                max_output_tokens=config.max_output_tokens,
            )
    except TimeoutError:
        raise CatalogError("审核模型探测超时") from None
    except ValueError as exc:
        if "截断" in str(exc) or "中断" in str(exc):
            raise CatalogError(PROBE_BUDGET_REASON) from None
        raise CatalogError(PROBE_TOKEN_REASON) from None
    except Exception:
        raise CatalogError(PROBE_CONNECTION_REASON) from None
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise CatalogError(PROBE_BUDGET_REASON)
    try:
        return parse_smartguard_token(text)
    except ValueError:
        raise CatalogError(PROBE_TOKEN_REASON) from None


async def stamp_smartguard_probe(payload: dict, *, transport=None) -> dict:
    """模型页保存前探测。未改且已通过的配置不重复调用。"""

    incoming = _review_config(payload)
    if incoming is None:
        return payload
    current, _ = read_catalog()
    if _same_passed_config(current.permission_review, incoming):
        payload["permissionReview"] = {**payload["permissionReview"], "probeStatus": "passed"}
        return payload
    keys = {provider.id: provider.api_key for provider in current.providers}
    data = dict(payload)
    data.pop("etag", None)
    data.pop("modelsPath", None)
    data["providers"] = [
        {**{key: value for key, value in item.items() if key != "apiKeyConfigured"},
         "apiKey": item.get("apiKey", keys.get(item.get("id"), ""))}
        for item in data.get("providers", [])
    ]
    catalog = ModelCatalog.model_validate(data)
    await probe_smartguard(catalog, catalog.permission_review, transport=transport)  # type: ignore[arg-type]
    payload["permissionReview"] = {**payload["permissionReview"], "probeStatus": "passed"}
    return payload
