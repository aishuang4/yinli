"""意图闭集、库存打标与合同认领。第一批不加 capability_tags 列。"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from src.scheduler.capability_contract import JobCapabilityClaim, JobCapabilityGap

logger = logging.getLogger(__name__)

INTENT_TAGS: frozenset[str] = frozenset({
    "file.read",
    "file.write",
    "email.send",
    "email.read",
    "calendar.read",
    "calendar.write",
    "messaging.send",
    "crm.read",
    "crm.write",
    "knowledge.search",
    "web.fetch",
    "http.call",
    "shell.execute",
    "office.generate",
})

CHANNEL_INTENTS: frozenset[str] = frozenset({
    "email.send",
    "email.read",
    "messaging.send",
    "calendar.read",
    "calendar.write",
    "crm.read",
    "crm.write",
    "knowledge.search",
    "web.fetch",
    "http.call",
})

SKILL_OK_INTENTS: frozenset[str] = frozenset({
    "file.read",
    "file.write",
    "office.generate",
    "shell.execute",
})

# 库存名称层：只扫 tool_name / title。email.send 必须看到 send 类 token。
INVENTORY_NAME_TOKENS: dict[str, tuple[str, ...]] = {
    "email.send": (
        "send_email", "send-email", "send-mail", "sendmail", "send_mail",
        "发信", "发邮件",
    ),
    "email.read": (
        "list_messages", "list-messages", "fetch_mail", "fetch-mail",
        "inbox", "list_labels", "list-labels",
    ),
    "messaging.send": (
        "send_message", "send-message", "post_message", "post-message", "发消息",
    ),
    "crm.write": (
        "create_contact", "update_deal", "create_deal", "update_contact",
    ),
    "crm.read": ("list_contacts", "list_deals", "get_contact"),
    "office.generate": ("周报", "日报", "纪要"),
    "knowledge.search": ("knowledge_search", "search_knowledge", "kb_search"),
    "calendar.write": ("create_event", "update_event"),
    "calendar.read": ("list_events", "get_event"),
}

# 用户原话抽取：可以说「邮件」；不得用这张表给 list_messages 打 email.send。
USER_INTENT_TOKENS: dict[str, tuple[str, ...]] = {
    "email.send": (
        "发邮件", "发信", "邮件", "smtp", "gmail", "outlook", "mailgun",
    ),
    "email.read": ("收件箱", "读邮件"),
    "messaging.send": ("slack", "钉钉", "飞书", "发消息"),
    "crm.write": ("更新客户", "crm"),
    "office.generate": ("周报", "日报", "纪要", "生成文档"),
    "file.write": ("写文件", "生成文档"),
    "http.call": ("调用 http", "通用 api", "http 接口"),
    "web.fetch": ("抓网页", "webfetch", "天气", "weather", "气温", "天气预报"),
    "shell.execute": ("bash", "跑脚本"),
    "knowledge.search": ("知识库", "检索知识"),
}

SEND_USER_TOKENS: tuple[str, ...] = USER_INTENT_TOKENS["email.send"]
GENERATE_USER_TOKENS: tuple[str, ...] = (
    "周报", "日报", "纪要", "生成文档", "写文件", "生成文件",
)
SEND_ONLY_TOKENS: tuple[str, ...] = ("只发送", "不要生成", "不要写文件", "不要写")

HTTP_CATEGORY_TO_INTENT: dict[str, str] = {
    "knowledge_base": "knowledge.search",
    "knowledge": "knowledge.search",
}

BUILTIN_DECLARED_TAGS: dict[str, tuple[str, ...]] = {
    "Read": ("file.read",),
    "Grep": ("file.read",),
    "Glob": ("file.read",),
    "Write": ("file.write",),
    "Edit": ("file.write",),
    "Bash": ("shell.execute",),
    "WebFetch": ("web.fetch",),
    "HttpRequest": ("http.call",),
}


def _normalize(text: str) -> str:
    return str(text or "").strip().lower().replace(" ", "")


def _contains_token(haystack: str, token: str) -> bool:
    if not token:
        return False
    if re.fullmatch(r"[a-z0-9_\-]+", token, flags=re.I):
        return token.lower() in haystack.lower()
    return token in haystack


def extract_required_intents(
    task_intent: str,
    required_intents: Iterable[str] = (),
) -> list[str]:
    """从用户原话抽 required intents；非法标签忽略。"""

    text = str(task_intent or "")
    folded = _normalize(text)
    found: list[str] = []

    def _add(intent: str) -> None:
        if intent in INTENT_TAGS and intent not in found:
            found.append(intent)

    for raw in required_intents or ():
        label = str(raw or "").strip()
        if label in INTENT_TAGS:
            _add(label)
        elif label:
            logger.info("忽略非法 required intent: %s", label)

    for intent, tokens in USER_INTENT_TOKENS.items():
        if any(_contains_token(text, token) or _contains_token(folded, _normalize(token)) for token in tokens):
            _add(intent)

    if any(token in text or _normalize(token) in folded for token in GENERATE_USER_TOKENS):
        _add("file.write")
        _add("office.generate")

    send_hit = any(
        _contains_token(text, token) or _normalize(token) in folded
        for token in SEND_USER_TOKENS
    )
    if send_hit:
        _add("email.send")
        if "http.call" in found:
            found = [item for item in found if item != "http.call"]

    send_only = any(token in text for token in SEND_ONLY_TOKENS)
    generate_hit = any(token in text for token in GENERATE_USER_TOKENS)
    if send_only and not generate_hit and "file.write" in found:
        found = [item for item in found if item != "file.write"]

    if not found:
        found = ["file.write"]
    return found


def tag_inventory_row(
    *,
    kind: str,
    tool_name: str,
    title: str,
    description: str,
    declared_tags: Iterable[str] = (),
) -> tuple[list[str], dict[str, str]]:
    """声明优先于名称 token，名称优先于描述启发式。"""

    tags: list[str] = []
    tag_source: dict[str, str] = {}

    def _add(intent: str, source: str) -> None:
        if intent not in INTENT_TAGS:
            return
        if intent not in tag_source:
            tags.append(intent)
            tag_source[intent] = source

    for raw in declared_tags:
        label = str(raw or "").strip()
        mapped = HTTP_CATEGORY_TO_INTENT.get(label, label)
        _add(mapped, "declared")

    if kind == "builtin":
        for intent in BUILTIN_DECLARED_TAGS.get(tool_name, ()):
            _add(intent, "declared")
        # HttpRequest 不得被打上 email.send
        return tags, tag_source

    name_blob = f"{tool_name} {title}".strip()
    name_folded = _normalize(name_blob)
    desc = str(description or "")

    for intent, tokens in INVENTORY_NAME_TOKENS.items():
        if intent in tag_source:
            continue
        if any(_contains_token(name_blob, token) or _normalize(token) in name_folded for token in tokens):
            _add(intent, "name")

    for intent, tokens in INVENTORY_NAME_TOKENS.items():
        if intent in tag_source:
            continue
        if any(_contains_token(desc, token) for token in tokens):
            _add(intent, "description")

    return tags, tag_source


def claim_capabilities(
    required_intents: Iterable[str],
    inventory: list[Any],
    *,
    read_only: bool = False,
) -> tuple[list[JobCapabilityClaim], list[JobCapabilityGap], list[JobCapabilityGap]]:
    """每个 intent 认领最少一行；通道 intent 默认只要 mcp/http。"""

    claimed: list[JobCapabilityClaim] = []
    claimed_ids: set[str] = set()
    missing: list[JobCapabilityGap] = []
    blocked: list[JobCapabilityGap] = []

    for intent in required_intents:
        candidates: list[Any] = []
        blocked_hits: list[Any] = []
        for raw in inventory:
            row = raw
            tags = set(_row_list(row, "tags"))
            if intent not in tags:
                continue
            kind = _row_str(row, "kind")
            unattended = _row_bool(row, "unattended_ok", "unattendedOk")
            if intent == "knowledge.search":
                if kind not in {"mcp", "http", "knowledge"}:
                    continue
            elif intent in CHANNEL_INTENTS:
                if kind not in {"mcp", "http"}:
                    continue
            if intent in SKILL_OK_INTENTS and kind not in {"skill", "builtin", "mcp", "http"}:
                continue
            if unattended:
                candidates.append(row)
            else:
                blocked_hits.append(row)

        if not candidates:
            if blocked_hits:
                blocked.append(JobCapabilityGap(
                    intent=intent,
                    reason="blocked_by_permission",
                    suggested_action="将 Agent 权限改为 trusted_tools 或补可无人值守通道",
                ))
            else:
                missing.append(JobCapabilityGap(
                    intent=intent,
                    reason="missing",
                    suggested_action="为该 Agent 绑定具备该能力的 MCP / HTTP 工具",
                ))
            continue

        def _sort_key(row: Any) -> tuple[int, str]:
            source_map = _row_dict(row, "tag_source", "tagSource")
            source = str(source_map.get(intent) or "description")
            rank = {"declared": 0, "name": 1, "description": 2}.get(source, 3)
            return (rank, _row_str(row, "capability_id", "capabilityId"))

        winner = sorted(candidates, key=_sort_key)[0]
        cap_id = str(
            getattr(winner, "capability_id", None)
            if not isinstance(winner, dict)
            else winner.get("capability_id") or winner.get("capabilityId") or ""
        )
        if cap_id in claimed_ids:
            continue
        claimed_ids.add(cap_id)
        claimed.append(_claim_from_row(winner))

    if not read_only:
        for raw in inventory:
            kind = _row_str(raw, "kind")
            name = _row_str(raw, "tool_name", "toolName")
            unattended = _row_bool(raw, "unattended_ok", "unattendedOk")
            if kind == "builtin" and name in {"Read", "Write", "Edit"} and unattended:
                cap_id = _row_str(raw, "capability_id", "capabilityId")
                if cap_id not in claimed_ids:
                    claimed_ids.add(cap_id)
                    claimed.append(_claim_from_row(raw))
    return claimed, missing, blocked


def _row_str(row: Any, *keys: str) -> str:
    for key in keys:
        if isinstance(row, dict) and key in row and row[key] is not None:
            return str(row[key] or "")
        if not isinstance(row, dict) and hasattr(row, key):
            value = getattr(row, key)
            if value is not None:
                return str(value or "")
    return ""


def _row_bool(row: Any, *keys: str) -> bool:
    for key in keys:
        if isinstance(row, dict) and key in row:
            return bool(row[key])
        if not isinstance(row, dict) and hasattr(row, key):
            return bool(getattr(row, key))
    return False


def _row_list(row: Any, key: str) -> list[Any]:
    if isinstance(row, dict):
        value = row.get(key) or []
    else:
        value = getattr(row, key, None) or []
    return list(value)


def _row_dict(row: Any, *keys: str) -> dict[str, Any]:
    for key in keys:
        if isinstance(row, dict) and isinstance(row.get(key), dict):
            return dict(row[key])
        if not isinstance(row, dict):
            value = getattr(row, key, None)
            if isinstance(value, dict):
                return dict(value)
    return {}


def _claim_from_row(row: Any) -> JobCapabilityClaim:
    if isinstance(row, JobCapabilityClaim):
        return row
    if isinstance(row, dict):
        return JobCapabilityClaim.model_validate(row)
    return JobCapabilityClaim(
        capability_id=str(getattr(row, "capability_id", "")),
        kind=getattr(row, "kind"),
        resource_id=str(getattr(row, "resource_id", "")),
        server_id=str(getattr(row, "server_id", "") or ""),
        tool_name=str(getattr(row, "tool_name", "") or ""),
        tags=list(getattr(row, "tags", None) or []),
        unattended_ok=bool(getattr(row, "unattended_ok", True)),
    )
