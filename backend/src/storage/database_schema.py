"""SQLite 当前基础 Schema 声明。"""

from __future__ import annotations

import sqlite3

# 事实源：丢了就丢了。恢复时按行复制。
FACT_SOURCE_TABLES = (
    "json_store",
    "projects",
    "project_folders",
    "skill_overrides",
    "agents",
    "mcp_servers",
    "http_tools",
    "chat_sessions",
    "chat_messages",
    "plans",
    "plan_steps",
    "plan_events",
    "plan_revisions",
    "run_events",
    "trace_observations",
    "subagent_invocations",
    "subagent_transcript_items",
    "chat_runs",
    "chat_domain_streams",
    "chat_domain_events",
    "projection_streams",
    "projection_frames",
    "workflow_runs",
    "workflow_nodes",
    "workflow_edges",
    "workflow_events",
    "workflow_artifacts",
    "skill_runs",
    "memory_markdown_candidates",
    "session_context_state",
    "memory_nodes",
    "memory_retrieval_feedback",
    "skill_evolution_records",
    "scheduled_jobs",
    "scheduled_job_runs",
    "user_notifications",
    "group_studios",
    "group_task_cards",
    "group_memberships",
    "group_blackboards",
    "group_messages",
    "group_runs",
    "group_approvals",
    "group_artifacts",
    "group_work_items",
    "group_studio_events",
    "permission_decisions",
    "permission_approvals",
    "permission_executions",
)

# 派生表可随时重建。当前还没有 FTS / 聚合表。
DERIVED_TABLES: tuple[str, ...] = ()

# 运行时账本描述「此刻谁在跑」。恢复时不复制。表尚未创建，名字先登记。
LEDGER_TABLES = (
    "run_leases",
    "job_firing_leases",
    "session_admissions",
)

# 恢复时重建，不从源库复制。
META_TABLES = (
    "schema_version",
    "state_meta",
)

# 现有库补列。新库的 CREATE TABLE 已带上这些列时，这里仍然幂等。
SCHEMA_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("permission_decisions", "invalidated_at", "TEXT NOT NULL DEFAULT ''"),
    ("permission_decisions", "expires_at", "TEXT NOT NULL DEFAULT ''"),
    ("permission_decisions", "related_decision_id", "TEXT NOT NULL DEFAULT ''"),
    ("chat_runs", "authorization_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("chat_runs", "authorization_invalidated_at", "TEXT NOT NULL DEFAULT ''"),
    ("chat_sessions", "approval_mode", "TEXT NOT NULL DEFAULT 'manual'"),
    ("scheduled_jobs", "execution_policy", "TEXT NOT NULL DEFAULT '{}'"),
    ("group_messages", "thinking", "TEXT NOT NULL DEFAULT ''"),
    ("group_studios", "pending_speaker_member_id", "TEXT NOT NULL DEFAULT ''"),
    ("group_studios", "awaiting_intake_confirm", "INTEGER NOT NULL DEFAULT 0"),
    ("group_studios", "speaker_queue_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("group_studios", "claim_round_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("group_studios", "last_turn_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("group_studios", "wave_spoken_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("chat_messages", "tool_calls", "TEXT NOT NULL DEFAULT '[]'"),
    ("chat_messages", "thinking", "TEXT NOT NULL DEFAULT ''"),
    ("chat_messages", "model_caches", "TEXT NOT NULL DEFAULT '[]'"),
    ("chat_messages", "runtime_events", "TEXT NOT NULL DEFAULT '[]'"),
    ("chat_messages", "plan_runs", "TEXT NOT NULL DEFAULT '[]'"),
    (
        "chat_messages",
        "display_snapshot",
        "TEXT NOT NULL DEFAULT '{\"version\":4,\"lastSeq\":0,\"streamDone\":false,\"order\":[],\"blocksById\":{}}'",
    ),
    ("chat_sessions", "mode", "TEXT NOT NULL DEFAULT 'agent'"),
    ("chat_sessions", "model_config_id", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "role", "TEXT NOT NULL DEFAULT 'chat'"),
    ("agents", "job_toolkit", "TEXT NOT NULL DEFAULT '{}'"),
    ("agents", "plan_config", "TEXT NOT NULL DEFAULT '{}'"),
    ("agents", "knowledge_tools", "TEXT NOT NULL DEFAULT '[]'"),
    ("chat_sessions", "project_slug", "TEXT NOT NULL DEFAULT 'default'"),
    ("chat_sessions", "cwd", "TEXT NOT NULL DEFAULT ''"),
    ("chat_runs", "project_slug", "TEXT NOT NULL DEFAULT 'default'"),
    ("chat_runs", "cwd", "TEXT NOT NULL DEFAULT ''"),
    ("plans", "current_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("plans", "approved_revision", "INTEGER"),
    ("plans", "review_feedback", "TEXT NOT NULL DEFAULT ''"),
    ("plans", "execution_run_id", "TEXT NOT NULL DEFAULT ''"),
    ("run_events", "project_slug", "TEXT NOT NULL DEFAULT 'default'"),
    ("memory_nodes", "project_slug", "TEXT NOT NULL DEFAULT 'default'"),
    ("subagent_invocations", "project_slug", "TEXT NOT NULL DEFAULT 'default'"),
    ("subagent_invocations", "execution_mode", "TEXT NOT NULL DEFAULT 'foreground'"),
    ("subagent_invocations", "resume_capability", "TEXT NOT NULL DEFAULT 'same_process'"),
    ("subagent_invocations", "process_instance_id", "TEXT NOT NULL DEFAULT ''"),
    ("subagent_invocations", "result_ref", "TEXT NOT NULL DEFAULT ''"),
    ("subagent_invocations", "delivery_status", "TEXT NOT NULL DEFAULT 'none'"),
    ("scheduled_jobs", "capability_contract", "TEXT NOT NULL DEFAULT '{}'"),
    ("chat_runs", "updated_at", "TEXT NOT NULL DEFAULT ''"),
    ("chat_runs", "model_config_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("chat_runs", "trigger_source", "TEXT NOT NULL DEFAULT 'user'"),
    ("chat_runs", "scheduled_job_id", "TEXT NOT NULL DEFAULT ''"),
    ("chat_runs", "scheduled_firing_id", "TEXT NOT NULL DEFAULT ''"),
    ("chat_sessions", "kind", "TEXT NOT NULL DEFAULT 'chat'"),
    ("chat_messages", "files", "TEXT NOT NULL DEFAULT '[]'"),
    ("chat_messages", "clarification_requests", "TEXT NOT NULL DEFAULT '[]'"),
    ("chat_messages", "clarification_resolved", "INTEGER NOT NULL DEFAULT 0"),
    ("scheduled_jobs", "model_config_id", "TEXT NOT NULL DEFAULT ''"),
    ("group_studios", "model_config_id", "TEXT NOT NULL DEFAULT ''"),
    ("group_work_items", "review_status", "TEXT NOT NULL DEFAULT ''"),
    ("group_work_items", "review_note", "TEXT NOT NULL DEFAULT ''"),
    ("group_work_items", "rework_count", "INTEGER NOT NULL DEFAULT 0"),
    ("trace_observations", "payload_path", "TEXT NOT NULL DEFAULT ''"),
    ("trace_observations", "payload_sha256", "TEXT NOT NULL DEFAULT ''"),
)

META_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def reconcile_columns(conn: sqlite3.Connection) -> None:
    """按 SCHEMA_COLUMNS 幂等补列。表还不存在时跳过。"""

    for table, column, definition in SCHEMA_COLUMNS:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if found is None:
            continue
        names = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in names:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


BASE_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS projects (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        primary_path TEXT NOT NULL,
        color TEXT,
        icon TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        archived_at TEXT
    );
    CREATE TABLE IF NOT EXISTS project_folders (
        project_slug TEXT NOT NULL,
        path TEXT NOT NULL,
        access_mode TEXT NOT NULL,
        is_primary INTEGER NOT NULL DEFAULT 0,
        device_id TEXT,
        inode TEXT,
        granted_at TEXT,
        PRIMARY KEY (project_slug, path),
        FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS skill_overrides (
        skill_id TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1,
        available_to_all_agents INTEGER NOT NULL DEFAULT 1,
        installed INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        avatar TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        system_prompt TEXT NOT NULL,
        model_config TEXT NOT NULL,
        mcp_tools TEXT NOT NULL,
        knowledge_tools TEXT NOT NULL DEFAULT '[]',
        skills TEXT NOT NULL,
        tags TEXT NOT NULL,
        plan_config TEXT NOT NULL DEFAULT '{}',
        guardrails TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'chat',
        job_toolkit TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS mcp_servers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL,
        transport TEXT NOT NULL,
        command TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        homepage TEXT NOT NULL DEFAULT '',
        headers TEXT NOT NULL,
        tags TEXT NOT NULL,
        tools TEXT NOT NULL,
        status TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        installed_at TEXT,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS http_tools (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'custom',
        method TEXT NOT NULL DEFAULT 'POST',
        url TEXT NOT NULL,
        auth_type TEXT NOT NULL DEFAULT 'none',
        auth_config TEXT NOT NULL DEFAULT '{}',
        headers TEXT NOT NULL DEFAULT '{}',
        query_template TEXT NOT NULL DEFAULT '{}',
        body_template TEXT NOT NULL DEFAULT '{}',
        parameter_schema TEXT NOT NULL DEFAULT '{}',
        parameter_instructions TEXT NOT NULL DEFAULT '',
        response_mode TEXT NOT NULL DEFAULT 'full',
        response_path TEXT NOT NULL DEFAULT '',
        readonly INTEGER NOT NULL DEFAULT 1,
        approval_policy TEXT NOT NULL DEFAULT 'inherit',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'agent',
        kind TEXT NOT NULL DEFAULT 'chat',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        message_count INTEGER NOT NULL DEFAULT 0,
        project_slug TEXT NOT NULL DEFAULT 'default',
        cwd TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS chat_messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        agent_id TEXT,
        agent_name TEXT,
        model_name TEXT,
        error TEXT,
        thinking TEXT NOT NULL DEFAULT '',
        images TEXT NOT NULL,
        tool_calls TEXT NOT NULL DEFAULT '[]',
        skill_calls TEXT NOT NULL,
        model_caches TEXT NOT NULL DEFAULT '[]',
        runtime_events TEXT NOT NULL DEFAULT '[]',
        plan_runs TEXT NOT NULL DEFAULT '[]',
        display_snapshot TEXT NOT NULL DEFAULT '{"version":4,"lastSeq":0,"streamDone":false,"order":[],"blocksById":{}}',
        created_at TEXT NOT NULL,
        sort_order INTEGER NOT NULL,
        FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS plans (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        title TEXT NOT NULL,
        goal TEXT NOT NULL,
        status TEXT NOT NULL,
        markdown TEXT NOT NULL,
        current_step_id TEXT,
        current_revision INTEGER NOT NULL DEFAULT 0,
        approved_revision INTEGER,
        review_feedback TEXT NOT NULL DEFAULT '',
        execution_run_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS plan_steps (
        id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        step_index INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL,
        assigned_agent_id TEXT,
        evidence TEXT NOT NULL DEFAULT '',
        result TEXT NOT NULL DEFAULT '',
        started_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS plan_events (
        id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        step_id TEXT,
        run_event_id TEXT,
        event_type TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS plan_revisions (
        id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        markdown TEXT NOT NULL,
        steps TEXT NOT NULL,
        feedback TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(plan_id, revision),
        FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_plans_session_updated
        ON plans(session_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_plan_steps_plan_order
        ON plan_steps(plan_id, step_index);
    CREATE INDEX IF NOT EXISTS idx_plan_revisions_plan_revision
        ON plan_revisions(plan_id, revision DESC);
    CREATE TABLE IF NOT EXISTS run_events (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'leader',
        agent_id TEXT,
        agent_name TEXT,
        status TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_run_events_session_order
        ON run_events(session_id, sort_order, created_at);
    CREATE TABLE IF NOT EXISTS trace_observations (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        project_slug TEXT NOT NULL DEFAULT 'default',
        kind TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        parent_id TEXT,
        status TEXT NOT NULL DEFAULT 'running',
        started_at TEXT NOT NULL,
        ended_at TEXT,
        duration_ms INTEGER,
        input_json TEXT NOT NULL DEFAULT '{}',
        output_json TEXT NOT NULL DEFAULT '{}',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        payload_path TEXT NOT NULL DEFAULT '',
        payload_sha256 TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_trace_observations_run_started
        ON trace_observations(run_id, started_at);
    CREATE INDEX IF NOT EXISTS idx_trace_observations_project_started
        ON trace_observations(project_slug, started_at DESC);
    CREATE TABLE IF NOT EXISTS subagent_invocations (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        tool_call_id TEXT NOT NULL DEFAULT '',
        parent_scope TEXT NOT NULL DEFAULT 'single',
        parent_agent_id TEXT NOT NULL,
        parent_agent_name TEXT NOT NULL,
        definition_scope TEXT NOT NULL DEFAULT '',
        definition_path TEXT NOT NULL DEFAULT '',
        subagent_name TEXT NOT NULL,
        status TEXT NOT NULL,
        task TEXT NOT NULL,
        expected_output TEXT NOT NULL DEFAULT '',
        result_preview TEXT NOT NULL DEFAULT '',
        result_ref TEXT NOT NULL DEFAULT '',
        delivery_status TEXT NOT NULL DEFAULT 'none',
        error TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL,
        completed_at TEXT,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_subagent_invocations_run
        ON subagent_invocations(run_id, started_at);
    CREATE INDEX IF NOT EXISTS idx_subagent_invocations_session
        ON subagent_invocations(session_id, started_at);
    CREATE TABLE IF NOT EXISTS subagent_transcript_items (
        id TEXT PRIMARY KEY,
        invocation_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        UNIQUE(invocation_id, seq),
        FOREIGN KEY(invocation_id) REFERENCES subagent_invocations(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_subagent_transcript_invocation_seq
        ON subagent_transcript_items(invocation_id, seq);
    CREATE TABLE IF NOT EXISTS chat_runs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL DEFAULT '',
        agent_name TEXT NOT NULL DEFAULT '',
        task_text TEXT NOT NULL DEFAULT '',
        mode TEXT NOT NULL DEFAULT 'agent',
        status TEXT NOT NULL DEFAULT 'running',
        trigger_source TEXT NOT NULL DEFAULT 'user',
        scheduled_job_id TEXT NOT NULL DEFAULT '',
        scheduled_firing_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        completed_at TEXT,
        updated_at TEXT NOT NULL,
        project_slug TEXT NOT NULL DEFAULT 'default',
        cwd TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_chat_runs_project
        ON chat_runs(project_slug, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_chat_runs_updated
        ON chat_runs(updated_at DESC);
    CREATE TABLE IF NOT EXISTS chat_domain_streams (
        stream_key TEXT PRIMARY KEY,
        run_id TEXT NOT NULL UNIQUE,
        next_ordinal INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS chat_domain_events (
        id TEXT PRIMARY KEY,
        stream_key TEXT NOT NULL,
        stream_ordinal INTEGER NOT NULL,
        run_id TEXT NOT NULL,
        aggregate_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        type TEXT NOT NULL,
        data_version INTEGER NOT NULL,
        revision INTEGER NOT NULL,
        data_json TEXT NOT NULL DEFAULT '{}',
        occurred_at TEXT NOT NULL,
        causation_id TEXT,
        correlation_id TEXT,
        trace_id TEXT,
        UNIQUE(stream_key, stream_ordinal),
        UNIQUE(aggregate_type, aggregate_id, revision),
        FOREIGN KEY(stream_key) REFERENCES chat_domain_streams(stream_key) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_chat_domain_events_stream_order
        ON chat_domain_events(stream_key, stream_ordinal);
    CREATE TABLE IF NOT EXISTS projection_streams (
        stream_key TEXT PRIMARY KEY,
        run_id TEXT NOT NULL UNIQUE,
        scope TEXT NOT NULL DEFAULT 'chat',
        last_domain_ordinal INTEGER NOT NULL DEFAULT 0,
        last_seq INTEGER NOT NULL DEFAULT 0,
        snapshot_seq INTEGER NOT NULL DEFAULT 0,
        snapshot_json TEXT NOT NULL DEFAULT '{"version":4,"lastSeq":0,"streamDone":false,"order":[],"blocksById":{}}',
        snapshot_version INTEGER NOT NULL DEFAULT 4,
        stream_done INTEGER NOT NULL DEFAULT 0,
        runner_fence INTEGER NOT NULL DEFAULT 0,
        lease_owner TEXT,
        lease_expires_at TEXT,
        snapshot_updated_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(stream_key) REFERENCES chat_domain_streams(stream_key) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS projection_frames (
        stream_key TEXT NOT NULL,
        seq INTEGER NOT NULL,
        frame_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(stream_key, seq),
        FOREIGN KEY(stream_key) REFERENCES projection_streams(stream_key) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_projection_frames_stream_order
        ON projection_frames(stream_key, seq);
    CREATE TABLE IF NOT EXISTS workflow_runs (
        id TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL DEFAULT 'workflow.v1',
        title TEXT NOT NULL,
        goal TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        current_node_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS workflow_nodes (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        node_key TEXT NOT NULL,
        node_type TEXT NOT NULL,
        title TEXT NOT NULL,
        instruction TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        agent_id TEXT NOT NULL DEFAULT '',
        team_id TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        output TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        metadata TEXT NOT NULL DEFAULT '{}',
        started_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS workflow_edges (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        from_node_id TEXT NOT NULL,
        to_node_id TEXT NOT NULL,
        condition TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS workflow_events (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        node_id TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS workflow_artifacts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        node_id TEXT NOT NULL DEFAULT '',
        artifact_type TEXT NOT NULL DEFAULT 'text',
        title TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        uri TEXT NOT NULL DEFAULT '',
        metadata TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_workflow_runs_updated
        ON workflow_runs(updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_workflow_nodes_run_position
        ON workflow_nodes(run_id, position ASC);
    CREATE INDEX IF NOT EXISTS idx_workflow_edges_run
        ON workflow_edges(run_id);
    CREATE INDEX IF NOT EXISTS idx_workflow_events_run_order
        ON workflow_events(run_id, sort_order ASC, created_at ASC);
    CREATE TABLE IF NOT EXISTS skill_runs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        skill_name TEXT NOT NULL,
        status TEXT NOT NULL,
        current_step TEXT NOT NULL DEFAULT '',
        collected_inputs TEXT NOT NULL DEFAULT '{}',
        pending_interaction_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_skill_runs_session_agent_status
        ON skill_runs(session_id, agent_id, status, updated_at);
    -- 待用户确认的 Markdown 记忆候选：user / global 层由模型提炼后先进队列，采纳后才写文件。
    CREATE TABLE IF NOT EXISTS memory_markdown_candidates (
        id TEXT PRIMARY KEY,
        scope TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        section TEXT NOT NULL,
        operation TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        exact_text TEXT NOT NULL DEFAULT '',
        replacement_text TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        resolution TEXT NOT NULL DEFAULT '',
        agent_id TEXT NOT NULL DEFAULT '',
        agent_name TEXT NOT NULL DEFAULT '',
        project_slug TEXT NOT NULL DEFAULT 'default',
        source_session_id TEXT NOT NULL DEFAULT '',
        source_run_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_memory_markdown_candidates_status
        ON memory_markdown_candidates(status, created_at);
    -- 会话级 AgentScope 上下文状态：压缩摘要 + middleware 跨轮数据（已启用 Skill、治理计数）。
    -- 进程重启 / 实例淘汰后重建上下文时恢复，避免压缩摘要和"已读过哪些 Skill"丢失。
    CREATE TABLE IF NOT EXISTS session_context_state (
        session_id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        middle_context TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS memory_nodes (
        id TEXT PRIMARY KEY,
        project_slug TEXT NOT NULL DEFAULT 'default',
        agent_id TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT 'default',
        node_date TEXT NOT NULL,
        date_range_start TEXT NOT NULL,
        date_range_end TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        conversation_digest TEXT NOT NULL DEFAULT '',
        search_text TEXT NOT NULL DEFAULT '',
        user_inputs TEXT NOT NULL DEFAULT '[]',
        agent_final_outputs TEXT NOT NULL DEFAULT '[]',
        tags TEXT NOT NULL DEFAULT '[]',
        source TEXT NOT NULL DEFAULT 'chat_run',
        source_session_id TEXT NOT NULL DEFAULT '',
        source_run_id TEXT NOT NULL DEFAULT '',
        source_message_id TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'active',
        pinned INTEGER NOT NULL DEFAULT 0,
        vector_status TEXT NOT NULL DEFAULT 'pending',
        vector_error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_used_at TEXT,
        use_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_memory_nodes_scope_date
        ON memory_nodes(project_slug, agent_id, node_date DESC);
    CREATE INDEX IF NOT EXISTS idx_memory_nodes_status_date
        ON memory_nodes(status, node_date DESC);
    CREATE INDEX IF NOT EXISTS idx_memory_nodes_source_run
        ON memory_nodes(source_run_id);
    CREATE TABLE IF NOT EXISTS memory_retrieval_feedback (
        id TEXT PRIMARY KEY,
        audit_event_id TEXT NOT NULL DEFAULT '',
        node_id TEXT NOT NULL,
        rating TEXT NOT NULL,
        query TEXT NOT NULL DEFAULT '',
        agent_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(node_id) REFERENCES memory_nodes(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_memory_retrieval_feedback_node
        ON memory_retrieval_feedback(node_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_memory_retrieval_feedback_created
        ON memory_retrieval_feedback(created_at DESC);
    CREATE TABLE IF NOT EXISTS skill_evolution_records (
        id TEXT PRIMARY KEY,
        project_slug TEXT NOT NULL DEFAULT 'default',
        agent_id TEXT NOT NULL,
        agent_name TEXT NOT NULL DEFAULT '',
        team_id TEXT NOT NULL DEFAULT '',
        user_id TEXT NOT NULL DEFAULT 'default',
        source_session_id TEXT NOT NULL DEFAULT '',
        source_run_id TEXT NOT NULL DEFAULT '',
        source_message_id TEXT NOT NULL DEFAULT '',
        router_gate TEXT NOT NULL DEFAULT '{}',
        workflow_evidence TEXT NOT NULL DEFAULT '{}',
        reusable INTEGER NOT NULL DEFAULT 0,
        reusable_reason TEXT NOT NULL DEFAULT '',
        trigger_description TEXT NOT NULL DEFAULT '',
        inputs_schema TEXT NOT NULL DEFAULT '{}',
        draft_skill_name TEXT NOT NULL DEFAULT '',
        draft_skill_description TEXT NOT NULL DEFAULT '',
        draft_skill_content TEXT NOT NULL DEFAULT '',
        draft_tags TEXT NOT NULL DEFAULT '[]',
        granularity TEXT NOT NULL DEFAULT '',
        requires_workflow INTEGER NOT NULL DEFAULT 0,
        suggested_split TEXT NOT NULL DEFAULT '[]',
        variable_flow TEXT NOT NULL DEFAULT '[]',
        risk_notes TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'skipped',
        registered_skill_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_skill_evolution_records_status_updated
        ON skill_evolution_records(status, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_skill_evolution_records_source
        ON skill_evolution_records(source_session_id, source_run_id, source_message_id);
    CREATE INDEX IF NOT EXISTS idx_skill_evolution_records_agent
        ON skill_evolution_records(agent_id, updated_at DESC);
    CREATE TABLE IF NOT EXISTS scheduled_jobs (
        id TEXT PRIMARY KEY,
        project_slug TEXT NOT NULL,
        cwd TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        pause_reason TEXT NOT NULL DEFAULT '',
        agent_id TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'agent',
        schedule_kind TEXT NOT NULL,
        schedule_expr TEXT NOT NULL,
        timezone TEXT NOT NULL,
        next_run_at TEXT,
        last_run_at TEXT,
        frozen_prompt TEXT NOT NULL,
        skill_ids TEXT NOT NULL DEFAULT '[]',
        tool_scope TEXT NOT NULL DEFAULT '{"allow_bash":false,"allow_http":false,"allow_web":false,"allow_mcp":false,"allow_knowledge":false}',
        capability_contract TEXT NOT NULL DEFAULT '{}',
        input_spec TEXT NOT NULL DEFAULT '{}',
        output_spec TEXT NOT NULL DEFAULT '{}',
        missed_policy TEXT NOT NULL DEFAULT 'catch_up_once',
        failure_policy TEXT NOT NULL DEFAULT 'notify_keep_schedule',
        delivery_policy TEXT NOT NULL DEFAULT 'notify',
        delivery_channel_ref TEXT NOT NULL DEFAULT '',
        recipient_ids TEXT NOT NULL DEFAULT '[]',
        unattended_permission TEXT NOT NULL DEFAULT 'overlay_workspace_write',
        auto_send_enabled_at TEXT,
        required_successes INTEGER NOT NULL DEFAULT 3,
        consecutive_successes INTEGER NOT NULL DEFAULT 0,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        max_consecutive_failures INTEGER NOT NULL DEFAULT 3,
        max_firings_per_day INTEGER NOT NULL DEFAULT 8,
        created_from_session_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        archived_at TEXT,
        model_config_id TEXT NOT NULL DEFAULT '',
        execution_policy TEXT NOT NULL DEFAULT '{}',
        UNIQUE (project_slug, name)
    );
    CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_due
        ON scheduled_jobs(status, next_run_at);
    CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_project
        ON scheduled_jobs(project_slug, status, updated_at DESC);
    CREATE TABLE IF NOT EXISTS scheduled_job_runs (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        project_slug TEXT NOT NULL,
        trigger TEXT NOT NULL,
        scheduled_for TEXT NOT NULL,
        status TEXT NOT NULL,
        chat_session_id TEXT NOT NULL DEFAULT '',
        chat_run_id TEXT NOT NULL DEFAULT '',
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        output_manifest TEXT NOT NULL DEFAULT '{}',
        delivery_status TEXT NOT NULL DEFAULT 'not_applicable',
        started_at TEXT,
        completed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (job_id, scheduled_for, trigger),
        FOREIGN KEY(job_id) REFERENCES scheduled_jobs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_scheduled_job_runs_job
        ON scheduled_job_runs(job_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_scheduled_job_runs_status
        ON scheduled_job_runs(status, updated_at);
    CREATE TABLE IF NOT EXISTS user_notifications (
        id TEXT PRIMARY KEY,
        project_slug TEXT NOT NULL DEFAULT '',
        job_id TEXT NOT NULL DEFAULT '',
        firing_id TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}',
        read_at TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_user_notifications_project_created
        ON user_notifications(project_slug, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_user_notifications_unread
        ON user_notifications(project_slug, read_at, created_at DESC);
    CREATE TABLE IF NOT EXISTS group_studios (
        id TEXT PRIMARY KEY,
        project_slug TEXT NOT NULL,
        chat_session_id TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        phase_before_pause TEXT,
        current_task_card_id TEXT NOT NULL,
        awaiting_restatement INTEGER NOT NULL DEFAULT 0,
        awaiting_intake_confirm INTEGER NOT NULL DEFAULT 0,
        orchestrator_meta TEXT NOT NULL DEFAULT '{}',
        budget_json TEXT NOT NULL,
        current_run_id TEXT NOT NULL DEFAULT '',
        pending_speaker_member_id TEXT NOT NULL DEFAULT '',
        speaker_queue_json TEXT NOT NULL DEFAULT '[]',
        claim_round_json TEXT NOT NULL DEFAULT '[]',
        last_turn_json TEXT NOT NULL DEFAULT '{}',
        wave_spoken_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        archived_at TEXT,
        FOREIGN KEY(project_slug) REFERENCES projects(slug)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_group_studios_active_chat
        ON group_studios(chat_session_id)
        WHERE status NOT IN ('archived', 'failed', 'deleted');
    CREATE TABLE IF NOT EXISTS group_task_cards (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        status TEXT NOT NULL,
        goal TEXT NOT NULL,
        background TEXT NOT NULL DEFAULT '',
        constraints_json TEXT NOT NULL DEFAULT '[]',
        dod_json TEXT NOT NULL DEFAULT '[]',
        seed_from_chat INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(group_id) REFERENCES group_studios(id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_group_task_cards_one_current
        ON group_task_cards(group_id)
        WHERE status IN ('draft', 'active', 'frozen_for_close');
    CREATE TABLE IF NOT EXISTS group_memberships (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        role TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        status TEXT NOT NULL,
        muted INTEGER NOT NULL DEFAULT 0,
        last_spoke_at TEXT,
        crash_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(group_id) REFERENCES group_studios(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS group_blackboards (
        group_id TEXT PRIMARY KEY,
        revision INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(group_id) REFERENCES group_studios(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS group_messages (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        run_id TEXT NOT NULL DEFAULT '',
        speaker_id TEXT NOT NULL,
        role TEXT NOT NULL,
        intent TEXT NOT NULL,
        body TEXT NOT NULL,
        thinking TEXT NOT NULL DEFAULT '',
        mentions_json TEXT NOT NULL DEFAULT '[]',
        attachments_json TEXT NOT NULL DEFAULT '[]',
        visible_in_timeline INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        sort_order INTEGER NOT NULL,
        FOREIGN KEY(group_id) REFERENCES group_studios(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_group_messages_group_order
        ON group_messages(group_id, sort_order);
    CREATE TABLE IF NOT EXISTS group_runs (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        speaker_member_id TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        token_input INTEGER NOT NULL DEFAULT 0,
        token_output INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(group_id) REFERENCES group_studios(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS group_approvals (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        member_id TEXT NOT NULL,
        reply_id TEXT NOT NULL,
        tool_calls_json TEXT NOT NULL,
        arguments_preview TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        FOREIGN KEY(group_id) REFERENCES group_studios(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_group_approvals_run
        ON group_approvals(run_id, status);
    CREATE TABLE IF NOT EXISTS group_artifacts (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        title TEXT NOT NULL,
        path TEXT NOT NULL,
        media_type TEXT NOT NULL DEFAULT '',
        byte_size INTEGER NOT NULL DEFAULT 0,
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(group_id) REFERENCES group_studios(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS group_work_items (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        task_card_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        assignee_member_id TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL,
        source_run_id TEXT NOT NULL DEFAULT '',
        claim_run_id TEXT NOT NULL DEFAULT '',
        claimed_at TEXT,
        completed_at TEXT,
        review_status TEXT NOT NULL DEFAULT '',
        review_note TEXT NOT NULL DEFAULT '',
        rework_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(group_id) REFERENCES group_studios(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_group_work_items_group_status
        ON group_work_items(group_id, status, sort_order);
    CREATE TABLE IF NOT EXISTS group_studio_events (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        run_id TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        sort_order INTEGER NOT NULL,
        FOREIGN KEY(group_id) REFERENCES group_studios(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_group_studio_events_group_order
        ON group_studio_events(group_id, sort_order);
"""
