"""群聊工作室测试辅助：把 drafting 工作室确认进 discussing。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.storage.database import DEFAULT_PROJECT_ID, update_group_studio_state


def confirm_intake_for_tests(
    client: TestClient,
    group_id: str,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    goal: str | None = None,
) -> dict:
    """写入 goal（可选）后置 awaiting 并 POST confirm-intake。"""

    if goal is not None:
        put = client.put(f"/api/group-studio/{group_id}/task-card", json={
            "projectId": project_id,
            "goal": goal,
        })
        assert put.status_code == 200, put.text
    update_group_studio_state(group_id, awaiting_intake_confirm=True)
    response = client.post(
        f"/api/group-studio/{group_id}/confirm-intake",
        json={"projectId": project_id},
    )
    assert response.status_code == 200, response.text
    return response.json()
