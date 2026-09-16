"""Runs 页进度与结果摘要：终态 100%，进行中按 Snapshot 估算。"""

from src.server.routes.chat.run_dashboard import summarize_run_dashboard


def test_terminal_run_is_full_progress_without_snapshot():
    summary = summarize_run_dashboard(status="completed", agent_name="助手")
    assert summary["progress"] == 100
    assert summary["currentStage"] == "已完成"
    assert summary["resultPreview"] == ""
    assert summary["activeActors"] == ["助手"]


def test_failed_run_is_full_progress():
    summary = summarize_run_dashboard(status="failed", agent_name="助手")
    assert summary["progress"] == 100
    assert summary["currentStage"] == "已失败"


def test_running_without_snapshot_is_not_zero():
    summary = summarize_run_dashboard(status="running", agent_name="助手")
    assert 0 < summary["progress"] < 100
    assert summary["currentStage"] == "正在运行"


def test_single_agent_uses_text_and_tools():
    snapshot = {
        "order": ["msg", "tool-1", "tool-2", "text-1"],
        "blocksById": {
            "msg": {"id": "msg", "kind": "message", "phase": "open", "data": {}},
            "tool-1": {
                "id": "tool-1",
                "kind": "tool",
                "phase": "closed",
                "outcome": "completed",
                "data": {"name": "WebFetch"},
            },
            "tool-2": {
                "id": "tool-2",
                "kind": "tool",
                "phase": "open",
                "data": {"name": "Read"},
            },
            "text-1": {"id": "text-1", "kind": "text", "phase": "open", "data": "武汉今天多云。"},
        },
    }
    summary = summarize_run_dashboard(status="running", agent_name="助手", snapshot=snapshot)
    assert summary["progress"] == 53
    assert summary["currentStage"] == "正在调用 Read"
    assert summary["resultPreview"] == "武汉今天多云。"
    assert summary["resultLength"] == len("武汉今天多云。")
    assert summary["latestEvent"] == "Read"


def test_team_progress_from_tasks():
    snapshot = {
        "order": ["team", "m1", "m2", "t1", "t2"],
        "blocksById": {
            "team": {
                "id": "team",
                "kind": "team",
                "phase": "open",
                "data": {"counts": {"tasks": 2, "succeeded": 1}},
            },
            "m1": {
                "id": "m1",
                "kind": "team-member",
                "phase": "closed",
                "outcome": "completed",
                "data": {"name": "研究员", "status": "completed"},
            },
            "m2": {
                "id": "m2",
                "kind": "team-member",
                "phase": "open",
                "data": {"name": "撰稿", "status": "running"},
            },
            "t1": {
                "id": "t1",
                "kind": "team-task",
                "phase": "closed",
                "outcome": "completed",
                "data": {"status": "completed"},
            },
            "t2": {
                "id": "t2",
                "kind": "team-task",
                "phase": "open",
                "data": {"status": "running"},
            },
        },
    }
    summary = summarize_run_dashboard(
        status="running",
        agent_name="Leader",
        mode="team",
        snapshot=snapshot,
    )
    assert summary["progress"] == 50
    assert summary["currentStage"] == "Team 协作中 · 1/2 个任务"
    assert summary["memberTotal"] == 2
    assert summary["memberCompleted"] == 1
    assert summary["activeActors"] == ["撰稿"]


def test_plan_steps_drive_progress():
    snapshot = {
        "order": ["plan"],
        "blocksById": {
            "plan": {
                "id": "plan",
                "kind": "plan",
                "phase": "open",
                "data": {
                    "steps": [
                        {"id": "s1", "status": "completed"},
                        {"id": "s2", "status": "running"},
                        {"id": "s3", "status": "pending"},
                    ],
                },
            },
        },
    }
    summary = summarize_run_dashboard(status="running", snapshot=snapshot)
    assert summary["progress"] == 37
    assert summary["currentStage"] == "计划执行中 · 1/3 步"


def test_waiting_keeps_partial_progress():
    snapshot = {
        "order": ["tool-1"],
        "blocksById": {
            "tool-1": {
                "id": "tool-1",
                "kind": "tool",
                "phase": "closed",
                "outcome": "completed",
                "data": {"name": "AskUser"},
            },
        },
    }
    summary = summarize_run_dashboard(status="waiting_user", snapshot=snapshot)
    assert summary["progress"] == 90
    assert summary["currentStage"] == "等待用户补充"


def test_error_block_fills_error_field():
    snapshot = {
        "order": ["err"],
        "blocksById": {
            "err": {
                "id": "err",
                "kind": "error",
                "phase": "closed",
                "outcome": "failed",
                "data": {"message": "模型未配置"},
            },
        },
    }
    summary = summarize_run_dashboard(status="failed", snapshot=snapshot)
    assert summary["error"] == "模型未配置"
    assert summary["progress"] == 100
