"""登记、上传和下载工作室产物。目录本身在 `files.py`。"""
from __future__ import annotations

from src.group_studio.access import _assert_project, _load_studio, _new_id
from src.group_studio.errors import GROUP_STUDIO_NOT_FOUND, GroupStudioError
from src.group_studio.events import emit_envelope
from src.group_studio.files import ensure_studio_dirs, save_studio_upload
from src.group_studio.models import GroupArtifact
from src.group_studio.orchestrator import assert_can_mutate
from src.storage.records.group_studio_records import (
    insert_group_studio_artifact,
    list_group_studio_artifacts,
)


def list_studio_files(group_id: str) -> list[dict]:
    """列出工作室产物。"""

    _load_studio(group_id)
    return list_group_studio_artifacts(group_id)


def resolve_studio_file(group_id: str, artifact_id: str) -> dict[str, object]:
    """解析工作室产物，限制在该会话的 group-studios 目录内。"""

    studio = _load_studio(group_id)
    artifact = next(
        (item for item in list_group_studio_artifacts(group_id) if str(item.get("id")) == artifact_id),
        None,
    )
    if artifact is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    studio_root = ensure_studio_dirs(studio.chat_session_id, group_id).resolve()
    candidate = (studio_root / str(artifact.get("path") or "")).resolve()
    if not candidate.is_relative_to(studio_root) or not candidate.is_file():
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    return {**artifact, "absolutePath": candidate}


async def upload_studio_file(
    group_id: str,
    upload,
    *,
    project_slug: str = "",
    created_by: str = "human",
) -> GroupArtifact:
    """上传到工作室 inputs/。产物入库。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    assert_can_mutate(studio.status)
    stored = await save_studio_upload(
        session_id=studio.chat_session_id,
        group_id=group_id,
        upload=upload,
    )
    row = insert_group_studio_artifact({
        "id": _new_id("gart"),
        "groupId": group_id,
        "title": stored["title"],
        "path": stored["path"],
        "mediaType": stored["mediaType"],
        "byteSize": stored["byteSize"],
        "createdBy": created_by,
    })
    artifact = GroupArtifact.model_validate(row)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_FILE",
        payload=artifact.model_dump(by_alias=True),
    )
    return artifact
