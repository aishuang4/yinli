"""舊 Workspace / Project 欄位名。運行時模組不得再寫出這些字面量。"""

from __future__ import annotations

LEGACY_SCOPE_FIELDS = frozenset({"projectId", "workspaceId", "rootPath"})
LEGACY_SESSION_COLUMNS = ("workspace_id", "project_id")
