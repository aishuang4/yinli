"""权限 V2 公开展示边界；执行参数只在服务端现场，不沿 SSE 或 Trace 分片泄露。"""

import copy
import json

from src.security.authorization.display_input import display_call_input

MAX_BUFFERED_INPUT = 1_000_000


def _configured_secrets(agent) -> tuple[str, ...]:
    """展示时使用本轮冻结连接与当前目录密钥，不把密钥写入事件。"""
    from src.config.model_catalog import read_catalog
    from src.config.runtime_settings import current_model_binding

    catalog, _ = read_catalog()
    values = [provider.api_key for provider in catalog.providers if provider.api_key]
    binding = current_model_binding()
    if binding is not None:
        values.extend(provider.api_key for provider in binding.catalog.providers if provider.api_key)
    authorization = getattr(agent, "authorization_binding", None)
    builder = getattr(authorization, "action_builder", None)
    for connection in (getattr(builder, "mcp_connections", None) or {}).values():
        values.extend(str(value) for value in (connection.get("headers") or {}).values() if value)
    return tuple(values)


def public_authorization_event(agent, event_type: str, data: dict) -> dict:
    """平台授权启用双表示；AskUser 保留人类问题的原合同。"""
    if getattr(agent, "authorization_binding", None) is None:
        return data
    result = copy.deepcopy(data)
    result["platformAuthorization"] = True
    call_id = str(result.get("tool_call_id") or "")
    buffers = getattr(agent, "_permission_display_inputs", None)
    if buffers is None:
        buffers = {}
        agent._permission_display_inputs = buffers
    if event_type == "TOOL_CALL_START" and call_id:
        buffers[call_id] = ""
    if event_type == "TOOL_CALL_DELTA":
        # 片段内凭据可能跨 chunk；在生成结束时统一解析脱敏，不能丢弃普通参数。
        buffered = buffers.get(call_id, "")
        if buffered is not None:
            buffered += str(result.get("delta") or "")
            buffers[call_id] = buffered if len(buffered) <= MAX_BUFFERED_INPUT else None
        result["delta"] = ""
    if event_type == "TOOL_CALL_END":
        buffered = buffers.pop(call_id, "")
        if result.get("input") is None and result.get("arguments") is None:
            result["input"] = buffered or "{}"
            if buffered is None:
                result["input"] = json.dumps({"说明": "工具参数超过展示上限"}, ensure_ascii=False)
    secrets = _configured_secrets(agent) if event_type in {
        "TOOL_CALL_END", "TOOL_RESULT_START", "TOOL_RESULT_END", "REQUIRE_USER_CONFIRM", "REQUIRE_EXTERNAL_EXECUTION",
    } else ()
    if event_type in {"TOOL_CALL_END", "TOOL_RESULT_START", "TOOL_RESULT_END"}:
        for field in ("input", "arguments"):
            if field in result:
                result[field] = public_call_input(result[field], secrets=secrets)
    calls = result.get("tool_calls")
    if isinstance(calls, list):
        for call in calls:
            if isinstance(call, dict) and call.get("name") != "AskUser":
                call["input"] = public_call_input(call.get("input", "{}"), secrets=secrets)
                call["suggested_rules"] = []
    details = getattr(agent, "authorization_details", None)
    if call_id and callable(details):
        authorization = details(call_id)
        if authorization:
            result["authorization"] = authorization
    if event_type in {"REPLY_END", "EXCEED_MAX_ITERS"}:
        buffers.clear()
    return result


def public_call_input(raw, *, secrets: tuple[str, ...] = ()) -> str:
    """展示普通参数及结构，凭据仍由统一规则脱敏。"""
    return display_call_input(raw, secrets=secrets)
