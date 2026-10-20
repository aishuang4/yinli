"""群聊每一波發言凍結 owner Session 的目錄、資源與模型。

已開始的 Worker Run 讀凍結結果；下一波才重新讀取。
"""

from __future__ import annotations

import json
from typing import Any


def capture_wave_grant(session_id: str, *, model_config_id: str = "") -> dict[str, Any]:
    """讀取當下 Session grant。沒有列代表歷史任務不因此失敗。"""

    roots: list[dict[str, str]] = []
    resources: list[dict[str, str]] = []
    if session_id:
        from src.storage import database

        database.ensure_db()
        with database._lock, database._connect() as conn:
            for row in conn.execute(
                """
                SELECT normalized_path, access_mode
                FROM session_allowed_roots
                WHERE session_id = ?
                ORDER BY normalized_path
                """,
                (session_id,),
            ):
                roots.append({
                    "normalizedPath": str(row["normalized_path"]),
                    "accessMode": str(row["access_mode"]),
                })
            for row in conn.execute(
                """
                SELECT resource_type, resource_id
                FROM session_resource_grants
                WHERE session_id = ? AND enabled = 1
                ORDER BY resource_type, resource_id
                """,
                (session_id,),
            ):
                resources.append({
                    "resourceType": str(row["resource_type"]),
                    "resourceId": str(row["resource_id"]),
                })
    return {
        "sessionId": session_id,
        "modelConfigId": model_config_id,
        "roots": roots,
        "resources": resources,
    }


def store_wave_grant(run_id: str, grant: dict[str, Any]) -> None:
    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        conn.execute(
            "UPDATE group_runs SET wave_grant_json = ? WHERE id = ?",
            (json.dumps(grant, ensure_ascii=False), run_id),
        )
        conn.commit()


def load_wave_grant(run_id: str) -> dict[str, Any] | None:
    if not run_id:
        return None
    from src.storage import database

    database.ensure_db()
    with database._lock, database._connect() as conn:
        row = conn.execute(
            "SELECT wave_grant_json FROM group_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    raw = row["wave_grant_json"]
    if not raw:
        return None
    loaded = json.loads(str(raw))
    return loaded if isinstance(loaded, dict) else None
