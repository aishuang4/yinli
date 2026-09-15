"""群聊工作室归档后的记忆收口。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.group_studio import archive_memory
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.service import get_studio, patch_blackboard
from src.group_studio.settings import set_group_studio_settings
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import DEFAULT_PROJECT_ID, ensure_db


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


@pytest.fixture()
def client(isolated_db):
    ensure_db()
    set_group_studio_settings(enabled=True)
    app = FastAPI()
    app.include_router(group_studio.router, prefix="/api/group-studio")
    app.include_router(settings_routes.router, prefix="/api/settings")
    with TestClient(app) as test_client:
        yield test_client


def _studio_with_conclusions(client: TestClient):
    from test.group_studio_helpers import confirm_intake_for_tests

    created = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": "sess-archive",
        "agentId": "default",
    })
    assert created.status_code == 200, created.text
    group_id = created.json()["id"]
    confirm_intake_for_tests(client, group_id, goal="为 Q3 销售数据做一份按地区汇总的分析报告")
    patch_blackboard(
        group_id,
        {"addDecisions": [
            {"text": "按地区拆成华东 / 华北 / 华南三张表"},
            {"text": "异常值单独列表附在报告末尾"},
            {"text": "以后所有销售分析报告默认用中文并附异常值列表，这是整个工作台的约定"},
        ]},
        asked_by="tester",
    )
    return get_studio(group_id)


def test_build_archive_digest_uses_conclusions_not_transcript(client) -> None:
    studio = _studio_with_conclusions(client)

    goal_text, minutes = archive_memory.build_archive_digest(studio)

    assert goal_text.startswith("群聊工作室目标：为 Q3 销售数据做一份按地区汇总的分析报告")
    assert "已定结论：" in minutes
    assert "按地区拆成华东 / 华北 / 华南三张表" in minutes
    assert "异常值单独列表附在报告末尾" in minutes
    assert "参与成员：" in minutes
    agent_id, _name = archive_memory.owner_agent_for_archive(studio)
    assert agent_id == "default"
    assert agent_id != GROUP_STUDIO_HOST_AGENT_ID


def test_run_archive_memory_persists_node_and_reflects_with_worker_agent(client, monkeypatch) -> None:
    studio = _studio_with_conclusions(client)
    seen: dict = {}

    async def fake_reflect(item, *, model_name, rule_admission=None, **_kwargs):
        seen["item"] = item
        seen["model_name"] = model_name
        seen["rule_admission"] = rule_admission
        from src.memory.reflection import MemoryReflectionResult

        return MemoryReflectionResult(ignored_reason="")

    monkeypatch.setattr("src.memory.reflection.reflect_completed_run_memory_with_agents", fake_reflect)

    summary = asyncio.run(archive_memory.run_archive_memory(studio))

    assert summary["agentId"] == "default"
    assert summary["nodeId"]
    node = database.get_memory_node(summary["nodeId"])
    assert node["source_run_id"] == f"group:{studio.id}"
    assert node["source_session_id"] == "sess-archive"
    assert "按地区拆成华东" in node["agent_final_outputs"][0]
    # 提炼归属 Worker 的 Agent，准入结果与节点共用
    assert seen["item"].agent_id == "default"
    # 归档结论放行到 Markdown 提炼（user / global 仍走待确认），历史准入沿用规则结果
    assert seen["rule_admission"].should_write_markdown is True
    assert seen["rule_admission"].should_persist_history is True
    audits = database.list_run_events_by_type("MEMORY_AUDIT", limit=50)
    actions = {
        str((event.get("payload") or {}).get("memoryType") or "")
        for event in audits
        if str(event.get("sessionId") or event.get("session_id") or "") == "sess-archive"
    }
    assert {"group_archive_memory", "group_archive_reflection"} <= actions


def test_review_work_item_accept_and_rework_flow(client) -> None:
    """验收：accept 记录通过；rework 退回 open、保留原认领人、带意见并计数。"""

    from src.group_studio.workboard import claim_item, complete_item, publish_work_items, review_item
    from src.storage.database import list_group_studio_work_items

    studio = _studio_with_conclusions(client)
    worker = next(member for member in studio.members if member.role == "worker")
    created, _enqueue = publish_work_items(
        studio.id,
        [{"title": "写汇总表"}, {"title": "写异常值附录"}],
        run_id="run-host",
        assigned_member_ids=[],
    )
    first, second = created
    for item in (first, second):
        claim_item(studio.id, item["id"], member_id=worker.id, run_id="run-w")
        complete_item(studio.id, item["id"], member_id=worker.id, run_id="run-w")

    accepted = review_item(studio.id, first["id"], verdict="accept", note="齐了", run_id="run-host2")
    assert accepted["status"] == "done" and accepted["reviewStatus"] == "accepted"

    reworked = review_item(studio.id, second["id"], verdict="rework", note="缺华南数据", run_id="run-host2")
    assert reworked["status"] == "open"
    assert reworked["assigneeMemberId"] == worker.id
    assert reworked["reviewStatus"] == "rework"
    assert reworked["reviewNote"] == "缺华南数据"
    assert reworked["reworkCount"] == 1

    # 退回后同一工人可以再认领 / 完成，再次验收通过
    claim_item(studio.id, second["id"], member_id=worker.id, run_id="run-w2")
    complete_item(studio.id, second["id"], member_id=worker.id, run_id="run-w2")
    done_again = review_item(studio.id, second["id"], verdict="accept", run_id="run-host3")
    assert done_again["reviewStatus"] == "accepted" and done_again["reworkCount"] == 1

    # 非 done 项不能验收
    with pytest.raises(Exception):
        review_item(studio.id, "nope", verdict="accept")
    statuses = {item["id"]: item["reviewStatus"] for item in list_group_studio_work_items(studio.id)}
    assert statuses == {first["id"]: "accepted", second["id"]: "accepted"}


def test_host_tools_include_review_and_prompt_lists_pending_items(client) -> None:
    from src.group_studio.host_runtime import _host_turn_user_message, build_host_runtime_spec

    studio = _studio_with_conclusions(client)
    host = next(member for member in studio.members if member.role == "host")
    _spec, _ctx, tools = build_host_runtime_spec(studio.model_dump(by_alias=True), host.model_dump(by_alias=True))
    assert "group_review_work_item" in {tool.name for tool in tools}

    snapshot = studio.model_dump(by_alias=True)
    snapshot["status"] = "reviewing"
    snapshot["workItems"] = [
        {"id": "wi-1", "title": "汇总表", "status": "done", "reviewStatus": None},
        {"id": "wi-2", "title": "附录", "status": "open", "reviewStatus": "rework"},
    ]
    prompt = _host_turn_user_message(snapshot)
    assert "group_review_work_item" in prompt
    assert "wi-1:汇总表" in prompt and "wi-2:附录" in prompt


def test_worker_spec_hard_blocks_destructive_shell(client) -> None:
    from src.group_studio.worker_runtime import WORKER_SHELL_BLACKLIST, build_worker_runtime_spec

    studio = _studio_with_conclusions(client)
    worker = next(member for member in studio.members if member.role == "worker")
    spec, _ctx, _tools = build_worker_runtime_spec(studio.model_dump(by_alias=True), worker.model_dump(by_alias=True))
    for item in WORKER_SHELL_BLACKLIST:
        assert item in spec.guardrails.shell_blacklist
    assert len(spec.guardrails.shell_blacklist) >= len(WORKER_SHELL_BLACKLIST)


def test_blackboard_folds_old_decisions_past_cap() -> None:
    from src.group_studio.blackboard import (
        DECISION_FOLD_BATCH,
        FOLDED_DECISION_PREFIX,
        MAX_BLACKBOARD_DECISIONS,
        merge_blackboard,
    )

    board = {"decisions": [], "openQuestions": [], "artifacts": [], "goal": "g", "constraints": []}
    for index in range(MAX_BLACKBOARD_DECISIONS + 3):
        board = merge_blackboard(board, {"addDecisions": [{"text": f"决定 {index} " + "细节" * 30}]})

    decisions = board["decisions"]
    assert len(decisions) <= MAX_BLACKBOARD_DECISIONS
    folded = [item for item in decisions if item["text"].startswith(FOLDED_DECISION_PREFIX)]
    assert len(folded) == 1
    assert f"（{DECISION_FOLD_BATCH} 条）" in folded[0]["text"]
    assert "决定 0" in folded[0]["text"] and "…" in folded[0]["text"]
    # 最新的结论保持原文
    assert decisions[-1]["text"].startswith(f"决定 {MAX_BLACKBOARD_DECISIONS + 2}")
    # 未决问题不受归并影响
    board = merge_blackboard(board, {"addQuestions": [{"text": "还差什么？"}]})
    assert len(board["openQuestions"]) == 1
