"""SessionError HTTP 映射。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.server.routes.session_errors import session_error_http
from src.sessions.errors import SessionError

_ROWS = [
    ("session_not_found", 404),
    ("session_archived", 409),
    ("directory_unavailable", 422),
    ("session_not_migrated", 409),
    ("resource_not_found", 409),
    ("resource_disabled", 409),
    ("agent_ineligible", 409),
    ("tenant_mismatch", 409),
    ("session_has_active_runs", 409),
    ("hosted_path_rejected", 409),
    ("snapshot_required", 500),
    ("legacy_scope_retired", 410),
]


@pytest.mark.parametrize(("code", "status"), _ROWS)
def test_listed_codes_use_fixed_status(code: str, status: int) -> None:
    response = session_error_http(SessionError(code, "說明"), default_status=499)
    assert isinstance(response, HTTPException)
    assert response.status_code == status
    assert response.detail == {"code": code, "message": "說明"}


def test_unlisted_code_uses_default_status() -> None:
    response = session_error_http(SessionError("other", "其他"), default_status=409)
    assert response.status_code == 409
    assert response.detail == {"code": "other", "message": "其他"}
