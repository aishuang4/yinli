"""记忆系统运行时策略设置。

当前只持久化可安全热更新的准入策略；向量库 provider、schema 和 embedding 维度仍由
VectorStorePolicy 管理，不能在这里修改。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.storage.database import get_json_record, upsert_json_record

MEMORY_SETTINGS_NAMESPACE = "memory_settings"
ADMISSION_POLICY_KEY = "admission_policy"


@dataclass(frozen=True)
class MemoryAdmissionPolicy:
    """MemoryAdmissionGate 可持久化策略。"""

    history_threshold: float = 0.35
    markdown_threshold: float = 0.62
    skill_threshold: float = 0.48
    min_user_chars: int = 6
    min_assistant_chars: int = 8
    reject_sensitive: bool = True
    enable_redaction: bool = True
    enable_model_scoring: bool = False
    model_score_weight: float = 0.45
    model_timeout_seconds: int = 20
    summary_max_items_per_section: int = 8
    summary_max_chars: int = 1200
    markdown_write_retries: int = 2
    # user / global 层 Markdown 记忆影响用户所有会话：模型提炼后默认先进待确认队列，采纳后才写文件。
    # agent 层是 Agent 自己的工作笔记，始终直接写。
    confirm_user_markdown: bool = True
    confirm_global_markdown: bool = True

    def requires_confirmation(self, scope: str) -> bool:
        if scope == "user":
            return self.confirm_user_markdown
        if scope == "global_preference":
            return self.confirm_global_markdown
        return False

    def to_api_dict(self) -> dict[str, Any]:
        """转换为前端 API 使用的 camelCase 结构。"""

        return {
            "historyThreshold": self.history_threshold,
            "markdownThreshold": self.markdown_threshold,
            "skillThreshold": self.skill_threshold,
            "minUserChars": self.min_user_chars,
            "minAssistantChars": self.min_assistant_chars,
            "rejectSensitive": self.reject_sensitive,
            "enableRedaction": self.enable_redaction,
            "enableModelScoring": self.enable_model_scoring,
            "modelScoreWeight": self.model_score_weight,
            "modelTimeoutSeconds": self.model_timeout_seconds,
            "summaryMaxItemsPerSection": self.summary_max_items_per_section,
            "summaryMaxChars": self.summary_max_chars,
            "markdownWriteRetries": self.markdown_write_retries,
            "confirmUserMarkdown": self.confirm_user_markdown,
            "confirmGlobalMarkdown": self.confirm_global_markdown,
            "mode": "model_scoring" if self.enable_model_scoring else "rules_scoring",
        }

    def to_storage_dict(self) -> dict[str, Any]:
        """转换为 json_store 结构。"""

        return asdict(self)


def get_memory_admission_policy() -> MemoryAdmissionPolicy:
    """读取 MemoryAdmissionGate 策略，不存在时返回默认值。"""

    raw = get_json_record(MEMORY_SETTINGS_NAMESPACE, ADMISSION_POLICY_KEY) or {}
    return memory_admission_policy_from_payload(raw)


def save_memory_admission_policy(payload: dict[str, Any]) -> MemoryAdmissionPolicy:
    """保存 MemoryAdmissionGate 策略。"""

    current = get_memory_admission_policy()
    policy = memory_admission_policy_from_payload(payload, base=current)
    upsert_json_record(
        MEMORY_SETTINGS_NAMESPACE,
        ADMISSION_POLICY_KEY,
        policy.to_storage_dict(),
    )
    return policy


def memory_admission_policy_from_payload(
    payload: dict[str, Any],
    *,
    base: MemoryAdmissionPolicy | None = None,
) -> MemoryAdmissionPolicy:
    """从 API 或存储 payload 构造策略，并做边界裁剪。"""

    source = base or MemoryAdmissionPolicy()
    return MemoryAdmissionPolicy(
        history_threshold=_float(payload, "history_threshold", "historyThreshold", source.history_threshold, 0, 1),
        markdown_threshold=_float(payload, "markdown_threshold", "markdownThreshold", source.markdown_threshold, 0, 1),
        skill_threshold=_float(payload, "skill_threshold", "skillThreshold", source.skill_threshold, 0, 1),
        min_user_chars=_int(payload, "min_user_chars", "minUserChars", source.min_user_chars, 1, 200),
        min_assistant_chars=_int(payload, "min_assistant_chars", "minAssistantChars", source.min_assistant_chars, 1, 500),
        reject_sensitive=_bool(payload, "reject_sensitive", "rejectSensitive", source.reject_sensitive),
        enable_redaction=_bool(payload, "enable_redaction", "enableRedaction", source.enable_redaction),
        enable_model_scoring=_bool(payload, "enable_model_scoring", "enableModelScoring", source.enable_model_scoring),
        model_score_weight=_float(payload, "model_score_weight", "modelScoreWeight", source.model_score_weight, 0, 1),
        model_timeout_seconds=_int(
            payload,
            "model_timeout_seconds",
            "modelTimeoutSeconds",
            source.model_timeout_seconds,
            3,
            120,
        ),
        summary_max_items_per_section=_int(
            payload,
            "summary_max_items_per_section",
            "summaryMaxItemsPerSection",
            source.summary_max_items_per_section,
            1,
            50,
        ),
        summary_max_chars=_int(payload, "summary_max_chars", "summaryMaxChars", source.summary_max_chars, 200, 8000),
        markdown_write_retries=_int(
            payload,
            "markdown_write_retries",
            "markdownWriteRetries",
            source.markdown_write_retries,
            0,
            5,
        ),
        confirm_user_markdown=_bool(payload, "confirm_user_markdown", "confirmUserMarkdown", source.confirm_user_markdown),
        confirm_global_markdown=_bool(
            payload, "confirm_global_markdown", "confirmGlobalMarkdown", source.confirm_global_markdown
        ),
    )


def _raw(payload: dict[str, Any], snake: str, camel: str, default: Any) -> Any:
    if snake in payload:
        return payload.get(snake)
    if camel in payload:
        return payload.get(camel)
    return default


def _float(payload: dict[str, Any], snake: str, camel: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(_raw(payload, snake, camel, default))
    except Exception:
        value = default
    return min(max(value, minimum), maximum)


def _int(payload: dict[str, Any], snake: str, camel: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_raw(payload, snake, camel, default))
    except Exception:
        value = default
    return min(max(value, minimum), maximum)


def _bool(payload: dict[str, Any], snake: str, camel: str, default: bool) -> bool:
    value = _raw(payload, snake, camel, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
