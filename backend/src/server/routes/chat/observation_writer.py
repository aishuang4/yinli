"""Chat Trace Observation 写入：失败只记日志，不打断 Run。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from src.storage.observation_records import OBSERVATION_KINDS

MAX_OBSERVATION_TEXT = 16000
MAX_OBSERVATION_JSON_CHARS = 120000


def open_observation(
    *,
    persist: bool,
    run_id: str,
    session_id: str,
    kind: str,
    name: str,
    parent_id: str | None = None,
    input_data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    project_slug: str = "default",
    observation_id: str | None = None,
    status: str = "running",
) -> str | None:
    """打开一步 Observation；persist=false 或失败时返回 None。"""

    if not persist or not run_id or kind not in OBSERVATION_KINDS:
        return None
    try:
        from src.storage.database import get_trace_observation, insert_trace_observation

        if observation_id:
            existing = get_trace_observation(observation_id)
            if existing:
                return str(existing.get("id") or observation_id)

        created = insert_trace_observation({
            "id": observation_id or f"obs-{uuid.uuid4().hex}",
            "runId": run_id,
            "sessionId": session_id,
            "projectSlug": project_slug,
            "kind": kind,
            "name": name,
            "parentId": parent_id,
            "status": status,
            "input": _trim_payload(input_data),
            "metadata": _trim_payload(metadata),
        })
        return str(created.get("id") or "")
    except Exception as exc:
        print(f"[TraceObservation] open failed kind={kind} run={run_id}: {exc}")
        return None


def close_observation(
    *,
    persist: bool,
    observation_id: str | None,
    status: str,
    output: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """关闭一步 Observation；失败只打日志。"""

    if not persist or not observation_id:
        return
    try:
        from src.storage.database import close_trace_observation

        close_trace_observation(
            observation_id,
            status=status,
            output=_trim_payload(output),
            metadata=_trim_payload(metadata) if metadata else None,
        )
    except Exception as exc:
        print(f"[TraceObservation] close failed id={observation_id}: {exc}")


def patch_observation(
    *,
    persist: bool,
    observation_id: str | None,
    input_data: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    name: str | None = None,
) -> None:
    """给未关闭步骤补 input/output；失败只打日志。"""

    if not persist or not observation_id:
        return
    try:
        from src.storage.database import update_trace_observation

        update_trace_observation(
            observation_id,
            input_data=_trim_payload(input_data) if input_data is not None else None,
            output=_trim_payload(output) if output is not None else None,
            metadata=_trim_payload(metadata) if metadata else None,
            name=name,
        )
    except Exception as exc:
        print(f"[TraceObservation] patch failed id={observation_id}: {exc}")


def find_open_observation(
    *,
    persist: bool,
    run_id: str,
    kind: str,
    name: str | None = None,
) -> str | None:
    """查找仍打开的步骤 id。"""

    if not persist or not run_id:
        return None
    try:
        from src.storage.database import find_open_trace_observation

        row = find_open_trace_observation(run_id=run_id, kind=kind, name=name)
        return str(row.get("id") or "") if row else None
    except Exception as exc:
        print(f"[TraceObservation] find_open failed run={run_id} kind={kind}: {exc}")
        return None


def _trim_payload(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    trimmed = _trim_json_value(value)
    if not isinstance(trimmed, dict):
        return {"value": trimmed}
    encoded = json.dumps(trimmed, ensure_ascii=False, default=str)
    if len(encoded) <= MAX_OBSERVATION_JSON_CHARS:
        return trimmed
    if isinstance(trimmed.get("messages"), list) and trimmed["messages"]:
        messages = list(trimmed["messages"])
        while messages and len(json.dumps({**trimmed, "messages": messages}, ensure_ascii=False, default=str)) > MAX_OBSERVATION_JSON_CHARS:
            messages.pop(0)
        trimmed = {**trimmed, "messages": messages, "truncatedMessages": True}
        encoded = json.dumps(trimmed, ensure_ascii=False, default=str)
        if len(encoded) <= MAX_OBSERVATION_JSON_CHARS:
            return trimmed
    return {"truncated": True, "preview": encoded[:MAX_OBSERVATION_TEXT]}


def _trim_json_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= MAX_OBSERVATION_TEXT:
            return value
        return value[:MAX_OBSERVATION_TEXT] + "..."
    if isinstance(value, list):
        return [_trim_json_value(item) for item in value[:48]]
    if isinstance(value, dict):
        return {
            str(key): _trim_json_value(item)
            for key, item in list(value.items())[:48]
        }
    return value
