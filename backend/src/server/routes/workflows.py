"""Dynamic Workflow 独立路由。

该模块提供与 Chat Runtime 隔离的任务流 API。当前 MVP 先提供可持久化
Workflow Run、节点状态和事件流，后续 LangGraph 编排器通过同一事件契约接入。
"""

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.storage.records.workflow_records import (
    append_workflow_event,
    create_workflow_run,
    get_workflow_run,
    list_workflow_runs,
    update_workflow_node_status,
    update_workflow_run_status,
)

router = APIRouter()

WorkflowStatus = str
NodeStatus = str
_ACTIVE_STATUSES = {"draft", "running", "waiting", "failed"}
_SUBSCRIBERS: dict[str, set[asyncio.Queue[dict]]] = defaultdict(set)
_RUN_TASKS: dict[str, asyncio.Task] = {}


class WorkflowNodeDraft(BaseModel):
    """Workflow 初始节点草稿。"""

    key: str = Field(..., min_length=1, max_length=64)
    type: str = Field(default="agent", max_length=32)
    title: str = Field(..., min_length=1, max_length=80)
    instruction: str = Field(default="", max_length=2000)
    agent_id: str = Field(default="", alias="agentId")
    team_id: str = Field(default="", alias="teamId")
    metadata: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class WorkflowEdgeDraft(BaseModel):
    """Workflow 初始边草稿。"""

    from_node: str = Field(..., alias="from")
    to_node: str = Field(..., alias="to")
    condition: str = ""

    model_config = {"populate_by_name": True}


class WorkflowCreateRequest(BaseModel):
    """创建 Dynamic Workflow Run 的请求。"""

    goal: str = Field(..., min_length=1, max_length=4000)
    title: str | None = Field(default=None, max_length=120)
    nodes: list[WorkflowNodeDraft] = Field(default_factory=list)
    edges: list[WorkflowEdgeDraft] = Field(default_factory=list)


class WorkflowStartResponse(BaseModel):
    """Workflow 启动响应。"""

    run_id: str = Field(alias="runId")
    status: WorkflowStatus

    model_config = {"populate_by_name": True}


@router.get("")
async def list_workflows(limit: int = 50) -> dict:
    """列出最近的 Dynamic Workflow Run。"""

    return {"runs": list_workflow_runs(limit=max(1, min(limit, 100)))}


@router.post("")
async def create_workflow(request: WorkflowCreateRequest) -> dict:
    """创建一个 Dynamic Workflow Run，并写入初始事件。"""

    script = _script_for_goal(request.goal, request.title)
    draft_nodes = request.nodes or _nodes_for_script(script)
    draft_edges = request.edges or _default_edges_for_nodes(draft_nodes)
    run = create_workflow_run(
        {
            "schemaVersion": "workflow.v1",
            "title": script["title"],
            "goal": request.goal,
            "status": "draft",
            "nodes": [node.model_dump(by_alias=True) for node in draft_nodes],
            "edges": [edge.model_dump(by_alias=True) for edge in draft_edges],
        },
    )
    event = append_workflow_event(
        run["id"],
        "WORKFLOW_CREATED",
        status="draft",
        title="任务流已创建",
        content="已生成可审查的 workflow script 草稿。",
        payload={"nodeCount": len(draft_nodes), "script": script},
    )
    await _publish(run["id"], event)
    review_event = append_workflow_event(
        run["id"],
        "SCRIPT_REVIEW_REQUIRED",
        status="draft",
        title="等待审查脚本",
        content="请先审查阶段、并发、预算、权限和原始 JSON，再批准运行。",
        payload={"script": script},
    )
    await _publish(run["id"], review_event)
    return _with_script(get_workflow_run(run["id"]) or run)


@router.get("/{run_id}")
async def get_workflow(run_id: str) -> dict:
    """读取一个 Dynamic Workflow Run 的完整快照。"""

    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return _with_script(run)


@router.post("/{run_id}/start", response_model=WorkflowStartResponse)
async def start_workflow(run_id: str) -> WorkflowStartResponse:
    """启动 Workflow Run。

    当前 MVP 使用轻量模拟执行器推进节点状态，后续替换为 LangGraph 编排器。
    """

    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    if run["status"] not in _ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="Workflow run is already terminal")
    task = _RUN_TASKS.get(run_id)
    if task and not task.done():
        return WorkflowStartResponse(runId=run_id, status="running")
    _RUN_TASKS[run_id] = asyncio.create_task(_run_demo_workflow(run_id))
    return WorkflowStartResponse(runId=run_id, status="running")


@router.get("/{run_id}/events")
async def stream_workflow_events(run_id: str, request: Request, replay: bool = True) -> StreamingResponse:
    """订阅 Dynamic Workflow Run 事件。"""

    if not get_workflow_run(run_id):
        raise HTTPException(status_code=404, detail="Workflow run not found")
    from src.server.auth.presence import iter_with_presence
    from src.server.auth.routes import token_from_request

    return StreamingResponse(
        iter_with_presence(_event_stream(run_id, replay=replay), token_from_request(request)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_demo_workflow(run_id: str) -> None:
    """MVP 演示执行器：按脚本推进阶段、fan-out、barrier 和聚合事件。"""

    run = _with_script(get_workflow_run(run_id))
    if not run:
        return

    approved_event = append_workflow_event(
        run_id,
        "SCRIPT_APPROVED",
        status="running",
        title="脚本已批准",
        content="Runtime 将按 workflow script 执行，而不是按固定节点播放。",
        payload={"script": run["script"]},
    )
    await _publish(run_id, approved_event)
    started_event = append_workflow_event(
        run_id,
        "WORKFLOW_STARTED",
        status="running",
        title="Runtime Cockpit 已启动",
        content="开始执行阶段、并发 Agent task、变量聚合和验证流程。",
    )
    update_workflow_run_status(run_id, "running")
    await _publish(run_id, started_event)

    for node in run.get("nodes", []):
        now = _utc_now()
        update_workflow_run_status(run_id, "running", current_node_id=node["id"])
        updated = update_workflow_node_status(node["id"], "running", started_at=now)
        started = append_workflow_event(
            run_id,
            "STAGE_STARTED",
            node_id=node["id"],
            status="running",
            title=node["title"],
            content=f"{node['title']} 阶段开始。",
            payload={"node": updated or node, "stage": node.get("metadata") or {}},
        )
        await _publish(run_id, started)
        await asyncio.sleep(0.45)

        if node["type"] == "fanout":
            for item in (node.get("metadata") or {}).get("items", []):
                task_started = append_workflow_event(
                    run_id,
                    "AGENT_TASK_STARTED",
                    node_id=node["id"],
                    status="running",
                    title=f"{item.get('agentName', item.get('agentId', 'Agent'))} 开始",
                    content=str(item.get("instruction") or ""),
                    payload={"task": {**item, "status": "running", "stageId": node["id"]}},
                )
                await _publish(run_id, task_started)
            await asyncio.sleep(0.65)
            for item in (node.get("metadata") or {}).get("items", []):
                task_completed = append_workflow_event(
                    run_id,
                    "AGENT_TASK_COMPLETED",
                    node_id=node["id"],
                    status="completed",
                    title=f"{item.get('agentName', item.get('agentId', 'Agent'))} 完成",
                    content=f"{item.get('key')} 扫描结果已写入变量 scanReports。",
                    payload={"task": {**item, "status": "completed", "stageId": node["id"]}},
                )
                await _publish(run_id, task_completed)
            barrier = append_workflow_event(
                run_id,
                "BARRIER_WAITING",
                node_id=node["id"],
                status="waiting",
                title="等待 fan-out 汇合",
                content="所有并发 Agent task 已完成，正在聚合 scanReports。",
                payload={"variable": "scanReports", "taskCount": len((node.get("metadata") or {}).get("items", []))},
            )
            await _publish(run_id, barrier)
            variable = append_workflow_event(
                run_id,
                "VARIABLE_UPDATED",
                node_id=node["id"],
                status="completed",
                title="变量已更新",
                content="scanReports 已聚合为阶段输入。",
                payload={"name": "scanReports", "scope": "stage", "preview": "backend/frontend/docs/tests 四路扫描摘要"},
            )
            await _publish(run_id, variable)
            await asyncio.sleep(0.35)

        completed_at = _utc_now()
        summary = _demo_node_summary(node)
        completed_node = update_workflow_node_status(
            node["id"],
            "completed",
            summary=summary,
            output=summary,
            completed_at=completed_at,
        )
        completed = append_workflow_event(
            run_id,
            "STAGE_COMPLETED",
            node_id=node["id"],
            status="completed",
            title=node["title"],
            content=summary,
            payload={"node": completed_node or node, "stage": node.get("metadata") or {}},
        )
        await _publish(run_id, completed)
        await asyncio.sleep(0.25)

    completed_at = _utc_now()
    update_workflow_run_status(run_id, "completed", current_node_id="", completed_at=completed_at)
    finished = append_workflow_event(
        run_id,
        "WORKFLOW_COMPLETED",
        status="completed",
        title="任务流已完成",
        content="所有节点已完成，后续可接入 AgentScope 真实节点执行。",
    )
    await _publish(run_id, finished)


async def _event_stream(run_id: str, *, replay: bool) -> AsyncGenerator[str, None]:
    """生成 Workflow SSE 文本流。"""

    queue: asyncio.Queue[dict] = asyncio.Queue()
    _SUBSCRIBERS[run_id].add(queue)
    try:
        if replay:
            run = get_workflow_run(run_id)
            for event in (run or {}).get("events", []):
                yield _format_sse(event["type"], event)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
                yield _format_sse(event["type"], event)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        _SUBSCRIBERS[run_id].discard(queue)
        if not _SUBSCRIBERS[run_id]:
            _SUBSCRIBERS.pop(run_id, None)


async def _publish(run_id: str, event: dict) -> None:
    """把事件发布给当前进程内订阅者。"""

    for queue in list(_SUBSCRIBERS.get(run_id, set())):
        await queue.put(event)


def _format_sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _script_for_goal(goal: str, title: str | None = None) -> dict:
    """生成 MVP 可审查 workflow script，后续替换为模型 Planner。"""

    normalized_title = title or _title_from_goal(goal)
    return {
        "schemaVersion": "workflow.script.v1",
        "title": normalized_title,
        "goal": goal,
        "summary": "并行扫描关键范围，经过汇合、验证和聚合后输出可执行报告。",
        "concurrency": {
            "maxAgents": 4,
            "failurePolicy": "pause_on_stage_failure",
            "retryLimit": 1,
        },
        "estimate": {
            "agents": 4,
            "tokens": 18000,
            "minutes": 6,
            "riskLevel": "medium",
        },
        "permissions": {
            "filesystem": "read_only",
            "network": "inherit_agent",
            "writes": "requires_approval",
        },
        "stages": [
            {
                "id": "plan",
                "type": "planner",
                "title": "规划审计范围",
                "instruction": "拆解目标、确定扫描范围、输出 fan-out 任务。",
            },
            {
                "id": "fanout_scan",
                "type": "fanout",
                "title": "并发扫描",
                "collectAs": "scanReports",
                "items": [
                    {"key": "backend", "agentId": "code-analyst", "agentName": "Backend Analyst", "instruction": "审计后端 runtime、存储和权限链路。"},
                    {"key": "frontend", "agentId": "frontend-reviewer", "agentName": "Frontend Reviewer", "instruction": "审计前端页面、状态和交互体验。"},
                    {"key": "docs", "agentId": "doc-auditor", "agentName": "Doc Auditor", "instruction": "审计文档与实现是否一致。"},
                    {"key": "tests", "agentId": "test-reviewer", "agentName": "Test Reviewer", "instruction": "审计测试覆盖、回归风险和验证缺口。"},
                ],
            },
            {
                "id": "verify",
                "type": "verifier",
                "title": "交叉验证",
                "input": "$scanReports",
                "instruction": "去重、交叉验证、按风险排序，输出 verifiedRisks。",
            },
            {
                "id": "report",
                "type": "summary",
                "title": "生成报告",
                "input": "$verifiedRisks",
                "instruction": "生成最终报告、修复计划和开放问题。",
                "outputSchema": {
                    "risks": "array",
                    "fixPlan": "array",
                    "openQuestions": "array",
                },
            },
        ],
    }


def _nodes_for_script(script: dict) -> list[WorkflowNodeDraft]:
    return [
        WorkflowNodeDraft(
            key=str(stage.get("id") or f"stage_{index}"),
            type=str(stage.get("type") or "agent"),
            title=str(stage.get("title") or stage.get("id") or f"阶段 {index + 1}"),
            instruction=str(stage.get("instruction") or ""),
            metadata=stage,
        )
        for index, stage in enumerate(script.get("stages") or [])
    ]


def _default_edges_for_nodes(nodes: list[WorkflowNodeDraft]) -> list[WorkflowEdgeDraft]:
    return [
        WorkflowEdgeDraft(from_node=nodes[index].key, to_node=nodes[index + 1].key)
        for index in range(max(0, len(nodes) - 1))
    ]


def _title_from_goal(goal: str) -> str:
    normalized = " ".join(goal.split())
    return normalized[:36] or "Dynamic Workflow"


def _demo_node_summary(node: dict) -> str:
    title = str(node.get("title") or "节点")
    node_type = str(node.get("type") or "stage")
    if node_type == "fanout":
        return f"{title} 已完成。并发 Agent task 已汇合，scanReports 变量已更新。"
    if node_type == "verifier":
        return f"{title} 已完成。verifiedRisks 已完成去重、交叉验证和排序。"
    return f"{title} 已完成。Runtime 已记录 {node_type} 阶段输出。"


def _with_script(run: dict | None) -> dict | None:
    if not run:
        return None
    script = _script_from_events(run) or _script_for_goal(run.get("goal") or "", run.get("title"))
    return {**run, "script": script}


def _script_from_events(run: dict) -> dict | None:
    for event in reversed(run.get("events") or []):
        payload = event.get("payload") or {}
        script = payload.get("script")
        if isinstance(script, dict):
            return script
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
