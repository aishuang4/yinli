"""独立审核的用量与延迟归入原 Run，审计不保存模型提示或原始输出。"""

from uuid import uuid4

from src.server.routes.chat.observation_writer import close_observation, open_observation, patch_observation
from src.storage.database import get_trace_observation


def observe_permission_review(run, record: dict) -> None:
    """同步写入短审计步骤；并发审核不跨 await 读改写 Run 汇总。"""
    root_id = f"obs-run-{run.id}"
    row = get_trace_observation(root_id) or {}
    metadata = dict(row.get("metadata") or {})
    total = dict(metadata.get("permissionReviewUsage") or {})
    for name in ("inputTokens", "outputTokens", "durationMs"):
        total[name] = int(total.get(name) or 0) + int(record.get(name) or 0)
    total["calls"] = int(total.get("calls") or 0) + int(bool(record.get("modelCalled")))
    metadata["permissionReviewUsage"] = total
    patch_observation(persist=True, observation_id=root_id, metadata=metadata)
    observation_id = open_observation(
        persist=True, run_id=run.id, session_id=run.session_id, kind="generation",
        name=f"权限审核 · {record['modelName']}", parent_id=root_id,
        workspace_id=run.workspace_id, project_id=run.project_id,
        observation_id=f"obs-review-{uuid4().hex}",
        input_data={"purpose": "permission_review", "model": record["modelName"]},
        metadata={key: value for key, value in record.items() if key not in {"inputTokens", "outputTokens"}},
    )
    close_observation(
        persist=True, observation_id=observation_id,
        status="cancelled" if record.get("verdict") == "cancelled" else "completed",
        output={"verdict": record.get("verdict"), "reasonCode": record.get("reasonCode"),
                "usage": {"inputTokens": record.get("inputTokens", 0), "outputTokens": record.get("outputTokens", 0)},
                "durationMs": record.get("durationMs", 0)},
    )
