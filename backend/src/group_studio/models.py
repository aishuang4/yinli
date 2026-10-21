"""群聊工作室 Pydantic 模型。对外 JSON 使用 camelCase；存储层转换在 records。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

GROUP_STUDIO_HOST_AGENT_ID = "group-studio-host"

GroupStudioStatus = Literal[
    "open",
    "paused",
    "done",
    "deleted",
]
MemberStatus = Literal["active", "crashed", "removed"]
GroupRunStatus = Literal[
    "queued",
    "running",
    "waiting_permission",
    "completed",
    "cancelled",
    "failed",
]
MessageIntent = Literal[
    "discuss",
    "propose_decision",
    "ask_question",
    "report_progress",
    "request_help",
    "restatement",
    "human_directive",
    "system_notice",
    "host_assign",
]
GroupStudioEventType = Literal[
    "GROUP_STUDIO_STATE",
    "GROUP_STATUS",
    "GROUP_TASK_CARD",
    "GROUP_BLACKBOARD",
    "GROUP_MEMBER",
    "GROUP_FLOOR",
    "GROUP_MESSAGE_OPEN",
    "GROUP_MESSAGE_DELTA",
    "GROUP_MESSAGE_CLOSE",
    "GROUP_ACTIVITY_OPEN",
    "GROUP_ACTIVITY_DELTA",
    "GROUP_ACTIVITY_CLOSE",
    "GROUP_BUDGET",
    "GROUP_CIRCUIT",
    "GROUP_INTERRUPT",
    "GROUP_APPROVAL",
    "GROUP_FILE",
    "GROUP_PIN",
    "GROUP_ERROR",
    "GROUP_RUN_ENDED",
    "GROUP_SPEAKER_QUEUE",
]


class CamelModel(BaseModel):
    """请求/响应用模型：接受 camelCase 与 snake_case。"""

    model_config = {"populate_by_name": True}


class DodItem(CamelModel):
    """V1 遗留：任务卡验收条目。快照兼容保留，V2 不展示。"""

    id: str
    text: str
    checked: bool = False
    auto_checked: bool = Field(default=False, alias="autoChecked")
    evidence_artifact_id: str | None = Field(default=None, alias="evidenceArtifactId")


class TaskCard(CamelModel):
    """V1 任务卡。V2 对外用 `GroupPin`，本模型只服务存储兼容。"""

    id: str
    group_id: str = Field(alias="groupId")
    revision: int
    status: Literal["draft", "active", "frozen_for_close", "archived"]
    goal: str
    background: str = ""
    constraints: list[str] = Field(default_factory=list)
    dod_checklist: list[DodItem] = Field(default_factory=list, alias="dodChecklist")
    seed_from_chat: bool = Field(default=False, alias="seedFromChat")
    updated_at: str = Field(alias="updatedAt")


class Membership(CamelModel):
    """Host 或 Worker 席位。Host 行 agent_id 恒为 group-studio-host。"""

    id: str
    group_id: str = Field(alias="groupId")
    role: Literal["host", "worker"]
    agent_id: str = Field(alias="agentId")
    display_name: str = Field(alias="displayName")
    status: MemberStatus
    muted: bool = False
    last_spoke_at: str | None = Field(default=None, alias="lastSpokeAt")
    joined_at: str | None = Field(default=None, alias="joinedAt")
    crash_count: int = Field(default=0, alias="crashCount")


class GroupMessage(CamelModel):
    """结构化群消息。"""

    id: str
    group_id: str = Field(alias="groupId")
    run_id: str | None = Field(default=None, alias="runId")
    speaker_id: str = Field(alias="speakerId")
    role: Literal["human", "host", "worker", "system"]
    intent: MessageIntent
    body: str
    thinking: str = ""
    mentions: list[str] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    visible_in_timeline: bool = Field(default=True, alias="visibleInTimeline")
    created_at: str = Field(alias="createdAt")


class GroupBudget(CamelModel):
    """P0 只熔断 turn / 无进展。"""

    max_turns: int = Field(default=40, alias="maxTurns")
    max_no_progress_turns: int = Field(default=3, alias="maxNoProgressTurns")
    used_turns: int = Field(default=0, alias="usedTurns")
    no_progress_turns: int = Field(default=0, alias="noProgressTurns")


class GroupApproval(CamelModel):
    """Worker 工具权限待批。独立于 Chat HITL。"""

    id: str
    group_id: str = Field(alias="groupId")
    run_id: str = Field(alias="runId")
    member_id: str = Field(alias="memberId")
    reply_id: str = Field(alias="replyId")
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, alias="toolCalls")
    tool_names: list[str] = Field(default_factory=list, alias="toolNames")
    arguments_preview: str = Field(default="", alias="argumentsPreview")
    status: Literal["pending", "approved", "denied", "cancelled"]
    created_at: str = Field(alias="createdAt")


class GroupArtifact(CamelModel):
    """共享文件库条目。"""

    id: str
    group_id: str = Field(alias="groupId")
    title: str
    path: str
    media_type: str = Field(default="", alias="mediaType")
    byte_size: int = Field(default=0, alias="byteSize")
    created_by: str = Field(default="", alias="createdBy")
    created_at: str = Field(alias="createdAt")


class GroupPin(CamelModel):
    """置顶备注。空 Goal 不挡发言。"""

    title: str = ""
    goal: str = ""
    notes: str = ""
    updated_at: str = Field(default="", alias="updatedAt")


class GroupLastTurn(CamelModel):
    """上一波人类点名，供再来一轮重算队列。"""

    kind: Literal["assign", "all_round"]
    mentions: list[str] = Field(default_factory=list)


class GroupStudio(CamelModel):
    """GET /{id} 与 GROUP_STUDIO_STATE 的完整快照。"""

    id: str
    project_slug: str = Field(alias="projectSlug")
    chat_session_id: str = Field(alias="chatSessionId")
    model_config_id: str = Field(default="", alias="modelConfigId")
    title: str
    status: GroupStudioStatus
    pause_reason: str | None = Field(default=None, alias="pauseReason")
    phase_before_pause: str | None = Field(default=None, alias="phaseBeforePause")
    current_task_card_id: str = Field(alias="currentTaskCardId")
    task_card: TaskCard = Field(alias="taskCard")
    pin: GroupPin = Field(default_factory=GroupPin)
    members: list[Membership] = Field(default_factory=list)
    budget: GroupBudget
    current_run_id: str | None = Field(default=None, alias="currentRunId")
    current_speaker_member_id: str | None = Field(default=None, alias="currentSpeakerMemberId")
    pending_speaker_member_id: str | None = Field(default=None, alias="pendingSpeakerMemberId")
    speaker_queue: list[str] = Field(default_factory=list, alias="speakerQueue")
    last_turn: GroupLastTurn | None = Field(default=None, alias="lastTurn")
    can_rerun: bool = Field(default=False, alias="canRerun")
    current_approval: GroupApproval | None = Field(default=None, alias="currentApproval")
    messages: list[GroupMessage] = Field(default_factory=list)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ChatSessionGroupStudioHint(CamelModel):
    """catalog 可选字段，不进 send body。"""

    id: str
    status: GroupStudioStatus
    title: str


class GroupStudioFromChatRequest(CamelModel):
    """从当前 Chat 会话升级或回到工作室。"""

    project_slug: str = Field(alias="projectSlug")
    chat_session_id: str = Field(alias="chatSessionId", min_length=1)
    model_config_id: str = Field(default="", alias="modelConfigId")
    agent_id: str = Field(default="default", alias="agentId")
    draft_text: str = Field(default="", alias="draftText", max_length=8000)
    file_paths: list[str] = Field(default_factory=list, alias="filePaths")
    title: str | None = None
    reuse_blank_draft: bool = Field(default=False, alias="reuseBlankDraft")  # 已废弃，忽略


class GroupStudioModelUpdateRequest(CamelModel):
    """更新尚未开始运行的工作室模型。"""

    project_slug: str = Field(alias="projectSlug")
    model_config_id: str = Field(alias="modelConfigId", min_length=1)


class GroupPauseRequest(CamelModel):
    project_slug: str = Field(alias="projectSlug")


class GroupResumeRequest(CamelModel):
    project_slug: str = Field(alias="projectSlug")
    max_turns: int | None = Field(default=None, alias="maxTurns")


class GroupMessageCreateRequest(CamelModel):
    """人类输入框唯一写入口。"""

    project_slug: str = Field(alias="projectSlug")
    body: str = Field(min_length=1, max_length=20_000)
    mentions: list[str] = Field(default_factory=list)


class GroupInterruptRequest(CamelModel):
    """只停不说话（顶栏停止）。"""

    project_slug: str = Field(alias="projectSlug")


class GroupRerunRequest(CamelModel):
    """按上一波点名再来一轮，不写人类气泡。"""

    project_slug: str = Field(alias="projectSlug")


class GroupTaskCardUpdateRequest(CamelModel):
    project_slug: str = Field(alias="projectSlug")
    goal: str | None = None
    background: str | None = None
    constraints: list[str] | None = None


class GroupMemberCreateRequest(CamelModel):
    project_slug: str = Field(alias="projectSlug")
    agent_id: str = Field(alias="agentId")


class GroupMemberMuteRequest(CamelModel):
    project_slug: str = Field(alias="projectSlug")
    muted: bool


class GroupMemberRemoveRequest(CamelModel):
    project_slug: str = Field(alias="projectSlug")


class GroupCloseRequest(CamelModel):
    project_slug: str = Field(alias="projectSlug")
    confirm: bool = False


class GroupConfirmRestatementRequest(CamelModel):
    project_slug: str = Field(alias="projectSlug")


class GroupConfirmIntakeRequest(CamelModel):
    """人类确认 intake 密度，进入 discussing。"""

    project_slug: str = Field(alias="projectSlug")


class GroupApprovalDecisionRequest(CamelModel):
    project_slug: str = Field(alias="projectSlug")
    approval_id: str = Field(alias="approvalId")
    decision: Literal["approve", "deny"]


class GroupStartPhaseRequest(CamelModel):
    project_slug: str = Field(alias="projectSlug")


class GroupStudioEnvelope(CamelModel):
    """SSE 公共信封。"""

    seq: int | None = None
    group_id: str = Field(alias="groupId")
    run_id: str | None = Field(default=None, alias="runId")
    type: GroupStudioEventType
    status: GroupStudioStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(alias="createdAt")
