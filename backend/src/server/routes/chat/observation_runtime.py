"""把 Chat 源信号映射为闭环 Observation。不参与 SSE 投影。"""

from __future__ import annotations

from typing import Any

from src.server.routes.chat.generation_snapshot import (
    generation_output_payload,
    merge_generation_output,
    parse_tool_arguments,
    tool_output_text,
)
from src.server.routes.chat.observation_writer import (
    close_observation,
    find_open_observation,
    open_observation,
    patch_observation,
)


def open_run_root(
    *,
    persist: bool,
    run_id: str,
    session_id: str,
    task: str,
    workspace_id: str = "default",
    project_id: str = "default",
    agent_name: str = "",
    mode: str = "agent",
) -> str | None:
    """Run 开始时写入根 Observation。"""

    existing = find_open_observation(persist=persist, run_id=run_id, kind="run")
    if existing:
        return existing
    return open_observation(
        persist=persist,
        run_id=run_id,
        session_id=session_id,
        kind="run",
        name=agent_name or "Chat",
        input_data={"message": task, "mode": mode},
        workspace_id=workspace_id,
        project_id=project_id,
        observation_id=f"obs-run-{run_id}",
    )


def close_run_root(
    *,
    persist: bool,
    run_id: str,
    outcome: str,
    output: dict[str, Any] | None = None,
) -> None:
    """Run 终态关闭根节点与仍打开的子步骤；waiting 不得调用。"""

    if outcome not in {"completed", "failed", "cancelled"}:
        return
    _close_remaining_open(persist=persist, run_id=run_id, status=outcome)
    root_id = find_open_observation(persist=persist, run_id=run_id, kind="run") or f"obs-run-{run_id}"
    close_observation(
        persist=persist,
        observation_id=root_id,
        status=outcome,
        output=output or {},
    )


def observe_source_event(
    *,
    persist: bool,
    run_id: str,
    session_id: str,
    event_type: str,
    data: dict[str, Any],
    workspace_id: str = "default",
    project_id: str = "default",
) -> None:
    """按源信号开闭步骤。未知类型忽略。"""

    if not persist or not run_id:
        return
    parent_id = find_open_observation(persist=persist, run_id=run_id, kind="run") or f"obs-run-{run_id}"
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else data

    if event_type == "MODEL_CALL_START":
        _close_open_generation(
            persist=persist,
            run_id=run_id,
            data={"observationOutput": data.get("previousGenerationOutput")},
        )
        input_data = data.get("observationInput")
        if not isinstance(input_data, dict):
            input_data = {"model": str(data.get("model_name") or payload.get("model") or "")}
        open_observation(
            persist=persist,
            run_id=run_id,
            session_id=session_id,
            kind="generation",
            name=str(input_data.get("model") or data.get("model_name") or payload.get("model") or "model"),
            parent_id=parent_id,
            input_data=input_data,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        return
    if event_type == "MODEL_CALL_END":
        generation_id = find_open_observation(persist=persist, run_id=run_id, kind="generation")
        output = _generation_output_from_event(data, payload)
        current = _load_observation(generation_id)
        patch_observation(
            persist=persist,
            observation_id=generation_id,
            output=merge_generation_output(
                current.get("output") if isinstance(current.get("output"), dict) else {},
                output,
            ),
        )
        if data.get("observationText") or data.get("observationOutput") or data.get("closeGeneration"):
            _close_open_generation(persist=persist, run_id=run_id, data=data)
        return
    if event_type == "THINKING_BLOCK_DELTA":
        _append_generation_field(
            persist=persist,
            run_id=run_id,
            field="thinking",
            delta=str(data.get("delta") or payload.get("delta") or ""),
        )
        return
    if event_type in {"TEXT_BLOCK_DELTA", "REASONING_DELTA", "FINAL_ANSWER_DELTA"}:
        _append_generation_field(
            persist=persist,
            run_id=run_id,
            field="text",
            delta=str(data.get("delta") or payload.get("delta") or ""),
        )
        return
    if event_type in {"REPLY_END", "HITL_REQUIRED", "CLARIFICATION_REQUIRED", "TOOL_RESULT_START"}:
        _close_open_generation(persist=persist, run_id=run_id, data=data)
        if event_type in {"REPLY_END", "TOOL_RESULT_START"}:
            return

    if event_type == "TOOL_CALL_START":
        call_id = str(data.get("tool_call_id") or data.get("id") or "")
        name = str(data.get("tool_call_name") or data.get("name") or "Tool")
        open_observation(
            persist=persist,
            run_id=run_id,
            session_id=session_id,
            kind="tool",
            name=name,
            parent_id=parent_id,
            input_data={},
            metadata={"toolCallId": call_id},
            workspace_id=workspace_id,
            project_id=project_id,
            observation_id=_tool_obs_id(run_id, call_id) if call_id else None,
        )
        return
    if event_type == "TOOL_CALL_END":
        call_id = str(data.get("tool_call_id") or data.get("id") or "")
        tool_id = _tool_obs_id(run_id, call_id) if call_id else find_open_observation(
            persist=persist, run_id=run_id, kind="tool",
        )
        if tool_id is None and call_id:
            tool_id = open_observation(
                persist=persist,
                run_id=run_id,
                session_id=session_id,
                kind="tool",
                name=str(data.get("tool_call_name") or data.get("name") or "Tool"),
                parent_id=parent_id,
                metadata={"toolCallId": call_id},
                workspace_id=workspace_id,
                project_id=project_id,
                observation_id=_tool_obs_id(run_id, call_id),
            )
        input_value = parse_tool_arguments(
            data.get("input") if data.get("input") is not None else (
                data.get("arguments") if data.get("arguments") is not None else payload.get("input")
            )
        )
        if input_value not in (None, "", {}):
            patch_observation(
                persist=persist,
                observation_id=tool_id,
                input_data={"arguments": input_value} if not isinstance(input_value, dict) else input_value,
                name=str(data.get("tool_call_name") or data.get("name") or "") or None,
            )
        _append_generation_tool_call(
            persist=persist,
            run_id=run_id,
            tool_call={
                "id": call_id,
                "name": str(data.get("tool_call_name") or data.get("name") or ""),
                "input": input_value if input_value not in (None, "") else {},
            },
        )
        return
    if event_type == "TOOL_RESULT_TEXT_DELTA":
        call_id = str(data.get("tool_call_id") or data.get("id") or "")
        tool_id = _tool_obs_id(run_id, call_id) if call_id else find_open_observation(
            persist=persist, run_id=run_id, kind="tool",
        )
        _append_tool_result(
            persist=persist,
            observation_id=tool_id,
            delta=str(data.get("delta") or payload.get("delta") or ""),
        )
        return
    if event_type == "TOOL_RESULT_END":
        call_id = str(data.get("tool_call_id") or data.get("id") or "")
        tool_id = _tool_obs_id(run_id, call_id) if call_id else find_open_observation(
            persist=persist, run_id=run_id, kind="tool",
        )
        current = _load_observation(tool_id)
        existing = current.get("output") if isinstance(current.get("output"), dict) else {}
        incoming = tool_output_text(
            data.get("output")
            if data.get("output") not in (None, "")
            else data.get("result")
            if data.get("result") not in (None, "")
            else payload.get("output")
            if payload.get("output") not in (None, "")
            else payload.get("content")
        )
        state = str(data.get("state") or existing.get("state") or "").lower()
        close_observation(
            persist=persist,
            observation_id=tool_id,
            status="failed" if state in {"error", "failed", "denied"} else "completed",
            output={
                "result": incoming or str(existing.get("result") or ""),
                "error": str(data.get("error") or existing.get("error") or ""),
                "state": state or "completed",
            },
        )
        return
    if event_type == "AGENT_FINAL_ANSWER_STARTED":
        nested = data.get("payload") if isinstance(data.get("payload"), dict) else data
        _append_generation_tool_call(
            persist=persist,
            run_id=run_id,
            tool_call={
                "id": str(nested.get("tool_call_id") or nested.get("id") or ""),
                "name": str(nested.get("tool_call_name") or nested.get("name") or "FinalAnswerStart"),
                "input": parse_tool_arguments(nested.get("input") or nested.get("arguments") or {}),
            },
        )
        return

    if event_type in {"HITL_REQUIRED", "CLARIFICATION_REQUIRED"}:
        interaction_id = str(payload.get("interactionId") or data.get("id") or "")
        tool_calls = payload.get("toolCalls") if isinstance(payload.get("toolCalls"), list) else []
        names = [
            str(item.get("rawName") or item.get("name") or "")
            for item in tool_calls if isinstance(item, dict)
        ]
        is_ask = any(name.replace(" ", "").lower() in {"askuser"} or name.lower().endswith("askuser") for name in names)
        open_observation(
            persist=persist,
            run_id=run_id,
            session_id=session_id,
            kind="approval",
            name="AskUser" if is_ask or event_type == "CLARIFICATION_REQUIRED" else "权限确认",
            parent_id=parent_id,
            input_data={"interactionId": interaction_id, "type": event_type, "toolNames": names},
            workspace_id=workspace_id,
            project_id=project_id,
            observation_id=_approval_obs_id(run_id, interaction_id) if interaction_id else None,
            status="waiting",
        )
        return
    if event_type == "HITL_RESOLVED":
        interaction_id = str(payload.get("interactionId") or data.get("interactionId") or "")
        approval_id = (
            _approval_obs_id(run_id, interaction_id)
            if interaction_id
            else find_open_observation(persist=persist, run_id=run_id, kind="approval")
        )
        action = str(payload.get("action") or data.get("action") or "approve")
        close_observation(
            persist=persist,
            observation_id=approval_id,
            status="completed" if action != "reject" else "failed",
            output={"action": action},
        )
        return

    if event_type == "SKILL_CONTEXT":
        for raw in data.get("skills") or payload.get("skills") or []:
            item = raw if isinstance(raw, dict) else {"name": str(raw)}
            skill_id = str(item.get("id") or item.get("name") or "skill")
            open_observation(
                persist=persist,
                run_id=run_id,
                session_id=session_id,
                kind="skill",
                name=str(item.get("name") or skill_id),
                parent_id=parent_id,
                input_data={"skillId": skill_id},
                workspace_id=workspace_id,
                project_id=project_id,
                observation_id=_skill_obs_id(run_id, skill_id),
            )
        return
    if event_type == "SKILL_END":
        failed = str(data.get("status") or payload.get("status") or "completed") == "failed"
        for raw in data.get("skills") or payload.get("skills") or []:
            item = raw if isinstance(raw, dict) else {"name": str(raw)}
            skill_id = str(item.get("id") or item.get("name") or "skill")
            close_observation(
                persist=persist,
                observation_id=_skill_obs_id(run_id, skill_id),
                status="failed" if failed else "completed",
                output={"skillId": skill_id},
            )
        if not (data.get("skills") or payload.get("skills")):
            skill_id = find_open_observation(persist=persist, run_id=run_id, kind="skill")
            close_observation(
                persist=persist,
                observation_id=skill_id,
                status="failed" if failed else "completed",
            )
        return

    if event_type in {"SUBAGENT_STARTED", "SUBAGENT_QUEUED"}:
        invocation_id = str(payload.get("invocationId") or "")
        open_observation(
            persist=persist,
            run_id=run_id,
            session_id=session_id,
            kind="subagent",
            name=str(payload.get("subagentName") or "subagent"),
            parent_id=parent_id,
            input_data={"task": payload.get("taskPreview") or payload.get("task") or ""},
            metadata={"invocationId": invocation_id},
            workspace_id=workspace_id,
            project_id=project_id,
            observation_id=_subagent_obs_id(run_id, invocation_id) if invocation_id else None,
        )
        return
    if event_type in {"SUBAGENT_COMPLETED", "SUBAGENT_FAILED", "SUBAGENT_CANCELLED"}:
        invocation_id = str(payload.get("invocationId") or "")
        status = {
            "SUBAGENT_COMPLETED": "completed",
            "SUBAGENT_FAILED": "failed",
            "SUBAGENT_CANCELLED": "cancelled",
        }[event_type]
        close_observation(
            persist=persist,
            observation_id=_subagent_obs_id(run_id, invocation_id) if invocation_id else find_open_observation(
                persist=persist, run_id=run_id, kind="subagent",
            ),
            status=status,
            output={
                "resultPreview": payload.get("resultPreview") or "",
                "error": payload.get("error") or "",
            },
        )
        return

    if event_type == "TEAM_DOMAIN_EVENT":
        _observe_team_event(
            persist=persist,
            run_id=run_id,
            session_id=session_id,
            data=data,
            parent_id=parent_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )


def note_generation_output(*, persist: bool, run_id: str, text: str) -> None:
    """把本轮 reasoning / 过程文本补到当前 generation。"""

    _append_generation_field(persist=persist, run_id=run_id, field="text", delta=text, replace=True)


def _close_open_generation(*, persist: bool, run_id: str, data: dict[str, Any] | None = None) -> None:
    generation_id = find_open_observation(persist=persist, run_id=run_id, kind="generation")
    if not generation_id:
        return
    payload = data or {}
    nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    current = _load_observation(generation_id)
    finished = str(payload.get("finished_reason") or nested.get("finished_reason") or "").lower()
    close_observation(
        persist=persist,
        observation_id=generation_id,
        status="failed" if finished == "interrupted" else "completed",
        output=merge_generation_output(
            current.get("output") if isinstance(current.get("output"), dict) else {},
            _generation_output_from_event(payload, nested),
        ),
    )


def _append_generation_field(
    *,
    persist: bool,
    run_id: str,
    field: str,
    delta: str,
    replace: bool = False,
) -> None:
    if not str(delta or "") and not replace:
        return
    generation_id = find_open_observation(persist=persist, run_id=run_id, kind="generation")
    current = _load_observation(generation_id)
    existing = current.get("output") if isinstance(current.get("output"), dict) else {}
    previous = str(existing.get(field) or "")
    next_value = str(delta or "") if replace else previous + str(delta or "")
    patch_observation(
        persist=persist,
        observation_id=generation_id,
        output=merge_generation_output(existing, {field: next_value}),
    )


def _append_tool_result(*, persist: bool, observation_id: str | None, delta: str) -> None:
    if not observation_id or not str(delta or ""):
        return
    current = _load_observation(observation_id)
    existing = current.get("output") if isinstance(current.get("output"), dict) else {}
    patch_observation(
        persist=persist,
        observation_id=observation_id,
        output={
            **existing,
            "result": str(existing.get("result") or "") + str(delta),
            "state": str(existing.get("state") or "running"),
        },
    )


def _append_generation_tool_call(*, persist: bool, run_id: str, tool_call: dict[str, Any]) -> None:
    if not tool_call.get("name") and not tool_call.get("id"):
        return
    generation_id = find_open_observation(persist=persist, run_id=run_id, kind="generation")
    current = _load_observation(generation_id)
    existing = current.get("output") if isinstance(current.get("output"), dict) else {}
    calls = list(existing.get("toolCalls") or [])
    call_id = str(tool_call.get("id") or "")
    if call_id and any(str(item.get("id") or "") == call_id for item in calls if isinstance(item, dict)):
        calls = [
            {**item, **tool_call} if isinstance(item, dict) and str(item.get("id") or "") == call_id else item
            for item in calls
        ]
    else:
        calls.append(tool_call)
    patch_observation(
        persist=persist,
        observation_id=generation_id,
        output=merge_generation_output(existing, {"toolCalls": calls}),
    )


def _generation_output_from_event(data: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    explicit = data.get("observationOutput")
    if isinstance(explicit, dict):
        output = dict(explicit)
    else:
        output = generation_output_payload(
            text=str(data.get("observationText") or data.get("text") or payload.get("text") or ""),
            thinking=str(data.get("thinking") or payload.get("thinking") or ""),
            tool_calls=data.get("toolCalls") if isinstance(data.get("toolCalls"), list) else [],
            finished_reason=str(data.get("finished_reason") or payload.get("finished_reason") or "completed"),
        )
    usage = output.get("usage") if isinstance(output.get("usage"), dict) else {}
    merged_usage = {**_usage_from_event(data, payload), **usage}
    if merged_usage:
        output["usage"] = merged_usage
    return output


def _usage_from_event(data: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    if raw:
        usage.update(raw)
    for source in (payload, data):
        if source.get("input_tokens") is not None:
            usage["inputTokens"] = int(source.get("input_tokens") or 0)
        if source.get("output_tokens") is not None:
            usage["outputTokens"] = int(source.get("output_tokens") or 0)
        if source.get("inputTokens") is not None:
            usage["inputTokens"] = int(source.get("inputTokens") or 0)
        if source.get("outputTokens") is not None:
            usage["outputTokens"] = int(source.get("outputTokens") or 0)
    return usage


def _load_observation(observation_id: str | None) -> dict[str, Any]:
    if not observation_id:
        return {}
    try:
        from src.storage.database import get_trace_observation
    except Exception as exc:
        print(f"[TraceObservation] load failed id={observation_id}: {exc}")
        return {}
    try:
        return get_trace_observation(observation_id) or {}
    except Exception as exc:
        print(f"[TraceObservation] load failed id={observation_id}: {exc}")
        return {}


def _observe_team_event(
    *,
    persist: bool,
    run_id: str,
    session_id: str,
    data: dict[str, Any],
    parent_id: str | None,
    workspace_id: str,
    project_id: str,
) -> None:
    source_type = str(data.get("type") or "")
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    task_id = str(data.get("taskId") or task.get("id") or "")
    if source_type == "task.created" and task_id:
        open_observation(
            persist=persist,
            run_id=run_id,
            session_id=session_id,
            kind="team-task",
            name=str(task.get("title") or task.get("name") or task_id),
            parent_id=parent_id,
            input_data={"goal": task.get("goal") or task.get("description") or ""},
            metadata={"taskId": task_id},
            workspace_id=workspace_id,
            project_id=project_id,
            observation_id=_team_task_obs_id(run_id, task_id),
        )
        return
    if source_type in {"task.completed", "task.failed", "task.cancelled"} and task_id:
        retryable_fail = (
            source_type == "task.failed"
            and bool(task.get("retryable", True))
            and int(task.get("retryCount") or 0) < 1
        )
        if retryable_fail:
            return
        close_observation(
            persist=persist,
            observation_id=_team_task_obs_id(run_id, task_id),
            status="failed" if source_type == "task.failed" else (
                "cancelled" if source_type == "task.cancelled" else "completed"
            ),
            output={"result": task.get("result") or task.get("summary") or ""},
        )
        return
    if source_type == "member.tool.started":
        tool_call_id = str(payload.get("toolCallId") or "")
        open_observation(
            persist=persist,
            run_id=run_id,
            session_id=session_id,
            kind="tool",
            name=str(payload.get("name") or "Tool"),
            parent_id=_team_task_obs_id(run_id, task_id) if task_id else parent_id,
            input_data={},
            metadata={"toolCallId": tool_call_id, "taskId": task_id},
            workspace_id=workspace_id,
            project_id=project_id,
            observation_id=_tool_obs_id(run_id, f"team:{tool_call_id}") if tool_call_id else None,
        )
        return
    if source_type in {"member.tool.completed", "member.tool.failed", "member.tool.denied"}:
        tool_call_id = str(payload.get("toolCallId") or "")
        close_observation(
            persist=persist,
            observation_id=_tool_obs_id(run_id, f"team:{tool_call_id}") if tool_call_id else None,
            status="completed" if source_type == "member.tool.completed" else "failed",
            output={"result": payload.get("output") or payload.get("reason") or ""},
        )


def _close_remaining_open(*, persist: bool, run_id: str, status: str) -> None:
    if not persist or not run_id:
        return
    try:
        from src.storage.database import list_trace_observations
    except Exception as exc:
        print(f"[TraceObservation] list remaining failed run={run_id}: {exc}")
        return
    try:
        rows = list_trace_observations(run_id)
    except Exception as exc:
        print(f"[TraceObservation] list remaining failed run={run_id}: {exc}")
        return
    for row in rows:
        if row.get("endedAt") or row.get("kind") == "run":
            continue
        close_observation(
            persist=persist,
            observation_id=str(row.get("id") or ""),
            status=status,
            output=row.get("output") if isinstance(row.get("output"), dict) else {},
        )


def _tool_obs_id(run_id: str, call_id: str) -> str:
    return f"obs-tool-{run_id}-{call_id}"


def _approval_obs_id(run_id: str, interaction_id: str) -> str:
    return f"obs-approval-{run_id}-{interaction_id}"


def _skill_obs_id(run_id: str, skill_id: str) -> str:
    return f"obs-skill-{run_id}-{skill_id}"


def _subagent_obs_id(run_id: str, invocation_id: str) -> str:
    return f"obs-subagent-{run_id}-{invocation_id}"


def _team_task_obs_id(run_id: str, task_id: str) -> str:
    return f"obs-team-task-{run_id}-{task_id}"
