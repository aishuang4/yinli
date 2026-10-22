"""房间读取。进群、成员、发言和文件都从这里取房间。"""
from __future__ import annotations

import uuid

from src.group_studio.errors import CROSS_PROJECT_SESSION, GROUP_STUDIO_NOT_FOUND, GroupStudioError
from src.group_studio.models import GroupStudio
from src.storage.records.group_studio_records import get_group_studio_snapshot


def _new_id(prefix: str) -> str:
    """生成带前缀的短 id。"""

    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _snapshot_to_model(raw: dict) -> GroupStudio:
    """存储快照转对外模型。"""

    return GroupStudio.model_validate(raw)


def get_studio(group_id: str) -> GroupStudio:
    """按 id 取房间；不存在则 404。"""

    raw = get_group_studio_snapshot(group_id)
    if raw is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    return _snapshot_to_model(raw)


def _load_studio(group_id: str) -> GroupStudio:
    """内部读房间别名。"""

    return get_studio(group_id)


def _assert_project(studio: GroupStudio, project_slug: str) -> None:
    """请求 project_slug 必须与房间一致。"""

    if project_slug and project_slug != studio.project_slug:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION)
