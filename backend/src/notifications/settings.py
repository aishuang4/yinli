"""通知通道运行时设置。持久化在 json_store，不存放投递密钥。"""
from __future__ import annotations


from typing import Any

from src.config.runtime_settings import SYSTEM_SETTINGS_NAMESPACE
from src.notifications.models import ChannelConfigField, ChannelSpec
from src.storage.config.json_document_store import (
    get_json_record,
    upsert_json_record,
)

NOTIFICATIONS_SETTINGS_KEY = "notifications"

CHANNEL_CATALOG: tuple[ChannelSpec, ...] = (
    ChannelSpec(
        id="in_app",
        display_name="应用内通知",
        description="在 Chat 右上角铃铛展示，定时任务完成后即时推送。",
        implemented=True,
        always_on=True,
    ),
    ChannelSpec(
        id="email",
        display_name="邮箱",
        description="任务完成后发送邮件。通道已预留，尚未接入投递。",
        implemented=False,
        config_fields=(
            ChannelConfigField(
                key="address",
                label="收件邮箱",
                input="text",
                placeholder="you@example.com",
            ),
        ),
    ),
    ChannelSpec(
        id="wechat",
        display_name="微信",
        description="通过企业微信或服务号推送。通道已预留，尚未接入投递。",
        implemented=False,
        config_fields=(
            ChannelConfigField(
                key="webhookUrl",
                label="Webhook URL",
                input="url",
                placeholder="https://",
            ),
        ),
    ),
    ChannelSpec(
        id="feishu",
        display_name="飞书",
        description="通过飞书机器人 Webhook 推送。通道已预留，尚未接入投递。",
        implemented=False,
        config_fields=(
            ChannelConfigField(
                key="webhookUrl",
                label="Webhook URL",
                input="url",
                placeholder="https://",
            ),
        ),
    ),
)

CHANNEL_BY_ID = {item.id: item for item in CHANNEL_CATALOG}


class NotificationSettingsError(ValueError):
    """通道设置不合法。"""


def _empty_config(spec: ChannelSpec) -> dict[str, str]:
    return {field.key: "" for field in spec.config_fields}


def _default_channel_state(spec: ChannelSpec) -> dict[str, Any]:
    state: dict[str, Any] = {"enabled": bool(spec.always_on or spec.id == "in_app")}
    state.update(_empty_config(spec))
    return state


def _read_stored() -> dict[str, Any]:
    raw = get_json_record(SYSTEM_SETTINGS_NAMESPACE, NOTIFICATIONS_SETTINGS_KEY) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _stored_channels(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    channels = raw.get("channels")
    if not isinstance(channels, dict):
        return {}
    return {
        str(channel_id): dict(value)
        for channel_id, value in channels.items()
        if isinstance(value, dict)
    }


def get_channel_state(channel_id: str) -> dict[str, Any]:
    """读取单个通道的 enabled 与配置，缺省走目录默认值。"""

    spec = CHANNEL_BY_ID.get(channel_id)
    if spec is None:
        raise NotificationSettingsError(f"未知通知通道: {channel_id}")
    stored = _stored_channels(_read_stored()).get(channel_id, {})
    state = _default_channel_state(spec)
    if "enabled" in stored:
        state["enabled"] = bool(stored.get("enabled"))
    for field in spec.config_fields:
        if field.key in stored and stored[field.key] is not None:
            state[field.key] = str(stored[field.key])
    if spec.always_on:
        state["enabled"] = True
    return state


def is_channel_enabled(channel_id: str) -> bool:
    """通道是否应对本条消息投递。always_on 通道始终为 True。"""

    spec = CHANNEL_BY_ID.get(channel_id)
    if spec is None:
        return False
    if spec.always_on:
        return True
    return bool(get_channel_state(channel_id).get("enabled"))


def list_channel_settings() -> list[dict[str, Any]]:
    """设置页目录：通道元数据 + 当前配置。"""

    items: list[dict[str, Any]] = []
    for spec in CHANNEL_CATALOG:
        state = get_channel_state(spec.id)
        config = {field.key: str(state.get(field.key) or "") for field in spec.config_fields}
        items.append({
            "id": spec.id,
            "displayName": spec.display_name,
            "description": spec.description,
            "implemented": spec.implemented,
            "alwaysOn": spec.always_on,
            "enabled": bool(state.get("enabled")),
            "config": config,
            "configFields": [
                {
                    "key": field.key,
                    "label": field.label,
                    "input": field.input,
                    "placeholder": field.placeholder,
                }
                for field in spec.config_fields
            ],
        })
    return items


def save_channel_settings(patches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按通道 id 合并更新。未实现通道不得启用。"""

    stored = _read_stored()
    channels = _stored_channels(stored)
    for spec in CHANNEL_CATALOG:
        channels[spec.id] = {
            **_default_channel_state(spec),
            **channels.get(spec.id, {}),
        }

    for patch in patches:
        channel_id = str(patch.get("id") or "").strip()
        spec = CHANNEL_BY_ID.get(channel_id)
        if spec is None:
            raise NotificationSettingsError(f"未知通知通道: {channel_id}")
        current = dict(channels.get(channel_id) or _default_channel_state(spec))
        if "enabled" in patch and patch["enabled"] is not None:
            enabled = bool(patch["enabled"])
            if enabled and not spec.implemented:
                raise NotificationSettingsError(f"{spec.display_name}通道尚未实现，不能启用")
            if spec.always_on:
                enabled = True
            current["enabled"] = enabled
        config = patch.get("config")
        if isinstance(config, dict):
            allowed = {field.key for field in spec.config_fields}
            for key, value in config.items():
                if key not in allowed:
                    raise NotificationSettingsError(f"{spec.display_name}不支持配置项 {key}")
                current[key] = "" if value is None else str(value).strip()
        if spec.always_on:
            current["enabled"] = True
        channels[channel_id] = current

    stored["channels"] = channels
    upsert_json_record(SYSTEM_SETTINGS_NAMESPACE, NOTIFICATIONS_SETTINGS_KEY, stored)
    return list_channel_settings()
