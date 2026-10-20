"""租户权限事实表：决定、人工批准与一次执行分别记录。"""

PERMISSION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS permission_decisions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    action_fingerprint TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    approval_mode TEXT NOT NULL CHECK(approval_mode IN ('manual','model_review','full_access')),
    policy_version INTEGER NOT NULL,
    invalidated_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, run_id, actor_id, call_id, action_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_permission_decisions_run
    ON permission_decisions(run_id, actor_id, created_at);
CREATE TABLE IF NOT EXISTS permission_approvals (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE REFERENCES permission_decisions(id),
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','cancelled','expired','interrupted')),
    approved_by TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS permission_executions (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE REFERENCES permission_decisions(id),
    status TEXT NOT NULL CHECK(status IN ('reserved','running','succeeded','failed','unknown')),
    result_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
