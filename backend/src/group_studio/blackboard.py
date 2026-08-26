"""黑板合并规则：未决问题不得被摘要抹掉。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.group_studio.errors import OPEN_QUESTIONS_DROPPED, GroupStudioError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def merge_blackboard(
    current: dict[str, Any],
    patch: dict[str, Any],
    *,
    asked_by: str = "host",
    source_message_id: str | None = None,
    source_run_id: str | None = None,
) -> dict[str, Any]:
    """把 Host/系统补丁合并进黑板。

    若 patch 携带 `openQuestions` 全量列表，必须覆盖当前所有未 resolved 的问题，
    否则抛 `open_questions_dropped`。
    """

    decisions = list(current.get("decisions") or [])
    questions = list(current.get("openQuestions") or [])
    artifacts = list(current.get("artifacts") or [])

    for item in patch.get("addDecisions") or []:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        decisions.append({
            "id": str(item.get("id") or _new_id("gdec")),
            "text": text,
            "sourceMessageId": item.get("sourceMessageId") or source_message_id,
            "sourceRunId": item.get("sourceRunId") or source_run_id,
            "createdAt": str(item.get("createdAt") or _utc_now()),
        })

    for item in patch.get("addQuestions") or []:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        questions.append({
            "id": str(item.get("id") or _new_id("gqst")),
            "text": text,
            "askedBy": str(item.get("askedBy") or asked_by),
            "resolved": False,
            "resolution": "",
        })

    resolve_by_id = {
        str(item.get("id") or ""): str(item.get("resolution") or "").strip()
        for item in patch.get("resolveQuestions") or []
        if item.get("id")
    }
    if resolve_by_id:
        next_questions = []
        for question in questions:
            qid = str(question.get("id") or "")
            if qid in resolve_by_id:
                next_questions.append({
                    **question,
                    "resolved": True,
                    "resolution": resolve_by_id[qid] or str(question.get("resolution") or ""),
                })
            else:
                next_questions.append(question)
        questions = next_questions

    replacement = patch.get("openQuestions")
    if replacement is not None:
        if not isinstance(replacement, list):
            raise GroupStudioError(422, OPEN_QUESTIONS_DROPPED)
        replacement_ids = {
            str(item.get("id") or "")
            for item in replacement
            if isinstance(item, dict)
        }
        unresolved = [
            str(item.get("id") or "")
            for item in questions
            if not item.get("resolved") and item.get("id")
        ]
        missing = [qid for qid in unresolved if qid not in replacement_ids]
        if missing:
            raise GroupStudioError(422, OPEN_QUESTIONS_DROPPED)
        questions = [
            {
                "id": str(item.get("id") or _new_id("gqst")),
                "text": str(item.get("text") or ""),
                "askedBy": str(item.get("askedBy") or asked_by),
                "resolved": bool(item.get("resolved")),
                "resolution": str(item.get("resolution") or ""),
            }
            for item in replacement
            if isinstance(item, dict)
        ]

    if patch.get("addArtifactIds"):
        for artifact_id in patch["addArtifactIds"]:
            value = str(artifact_id or "")
            if value and value not in artifacts:
                artifacts.append(value)

    return {
        **current,
        "decisions": decisions,
        "openQuestions": questions,
        "artifacts": artifacts,
        "goal": patch.get("goal", current.get("goal") or ""),
        "constraints": patch.get("constraints", current.get("constraints") or []),
        "taskCardId": current.get("taskCardId") or "",
    }
