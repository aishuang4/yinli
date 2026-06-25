"""
Team management routes.

This models persistent Agent teams. Runtime coordination tools can be layered
on top of these records later.
"""

from pathlib import Path
from typing import Literal
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from src.server.routes.agents import get_registered_agent, list_registered_agents
from src.storage.database import (
    delete_json_record,
    get_json_record,
    list_json_records,
    list_teams as db_list_teams,
    migrate_json_list_file,
    replace_teams,
    upsert_team,
    upsert_json_record,
    delete_team as db_delete_team,
)

router = APIRouter()

TEAMS_FILE = Path("data/teams.json")
TEAMS_NAMESPACE = "teams"
TEAM_TEMPLATES_NAMESPACE = "team_templates"

TeamMemberRole = Literal["leader", "worker"]
TeamStatus = Literal["idle", "running", "ended"]
TeamExecutionMode = Literal["parallel", "depends_on"]


class TeamMember(BaseModel):
    """Team member bound to an Agent profile."""

    id: str
    agent_id: str = Field(alias="agentId")
    name: str
    role: TeamMemberRole
    description: str = ""
    execution_mode: TeamExecutionMode = Field(default="parallel", alias="executionMode")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    handoff_prompt: str = Field(default="", alias="handoffPrompt")
    status: str = "idle"

    model_config = {"populate_by_name": True}


class TeamRecord(BaseModel):
    """Persistent Team record."""

    id: str
    name: str
    description: str = ""
    leader_agent_id: str = Field(alias="leaderAgentId")
    members: list[TeamMember] = Field(default_factory=list)
    status: TeamStatus = "idle"
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class TeamCreate(BaseModel):
    """Create Team request."""

    name: str = Field(..., min_length=1, max_length=80)
    description: str = ""
    leader_agent_id: str = Field(alias="leaderAgentId")
    worker_agent_ids: list[str] = Field(default_factory=list, alias="workerAgentIds")

    model_config = {"populate_by_name": True}


class TeamUpdate(BaseModel):
    """Update Team request."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None
    leader_agent_id: str | None = Field(default=None, alias="leaderAgentId")
    worker_agent_ids: list[str] | None = Field(default=None, alias="workerAgentIds")
    members: list[TeamMember] | None = None
    status: TeamStatus | None = None

    model_config = {"populate_by_name": True}


class TeamExportPackage(BaseModel):
    """Portable Team export package."""

    schema_version: str = Field(default="agent-workstation.team.v1", alias="schemaVersion")
    exported_at: str = Field(alias="exportedAt")
    team: TeamRecord

    model_config = {"populate_by_name": True}


class TeamImportRequest(BaseModel):
    """Import one Team from a portable package."""

    package: TeamExportPackage
    conflict_strategy: str = Field(default="new_id", alias="conflictStrategy")

    model_config = {"populate_by_name": True}


class TeamImportResponse(BaseModel):
    """Result of importing one Team."""

    team: TeamRecord
    warnings: list[str] = Field(default_factory=list)


class TeamTemplateMember(BaseModel):
    """Reusable member blueprint for a Team template."""

    name: str
    role: TeamMemberRole
    description: str = ""
    agent_id: str = Field(default="", alias="agentId")
    keywords: list[str] = Field(default_factory=list)
    execution_mode: TeamExecutionMode = Field(default="parallel", alias="executionMode")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    handoff_prompt: str = Field(default="", alias="handoffPrompt")

    model_config = {"populate_by_name": True}


class TeamTemplate(BaseModel):
    """Reusable Team preset template."""

    id: str
    name: str
    description: str = ""
    category: str = "custom"
    builtin: bool = False
    members: list[TeamTemplateMember]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class TeamTemplateInstantiateRequest(BaseModel):
    """Create a Team from one template."""

    name: str | None = Field(default=None, max_length=80)


class TeamTemplateSaveResponse(BaseModel):
    """Saved template response."""

    template: TeamTemplate


_teams: dict[str, TeamRecord] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_teams() -> None:
    migrate_json_list_file(TEAMS_NAMESPACE, TEAMS_FILE)
    records = db_list_teams()
    if not records:
        legacy = list_json_records(TEAMS_NAMESPACE)
        replace_teams({str(item["id"]): item for item in legacy if item.get("id")})
        records = db_list_teams()
    for item in records:
        team = TeamRecord.model_validate(item)
        _teams[team.id] = team


def _save_teams() -> None:
    replace_teams({
        team.id: team.model_dump(by_alias=True)
        for team in _teams.values()
    })


def get_registered_team(team_id: str) -> TeamRecord | None:
    """Return a persisted Team configuration by id."""

    return _teams.get(team_id)


def _build_members(
    leader_agent_id: str,
    worker_agent_ids: list[str],
) -> list[TeamMember]:
    leader_agent = get_registered_agent(leader_agent_id)
    members = [
        TeamMember.model_validate({
            "id": uuid.uuid4().hex,
            "agentId": leader_agent_id,
            "name": leader_agent.name if leader_agent else "Leader",
            "role": "leader",
            "description": leader_agent.description if leader_agent else "Team leader session",
            "executionMode": "parallel",
            "dependsOn": [],
            "handoffPrompt": "",
            "status": "idle",
        })
    ]
    for index, agent_id in enumerate(worker_agent_ids, start=1):
        if agent_id == leader_agent_id:
            continue
        worker_agent = get_registered_agent(agent_id)
        members.append(TeamMember.model_validate({
            "id": uuid.uuid4().hex,
            "agentId": agent_id,
            "name": worker_agent.name if worker_agent else f"Worker {index}",
            "role": "worker",
            "description": worker_agent.description if worker_agent else "Team worker session",
            "executionMode": "parallel",
            "dependsOn": [],
            "handoffPrompt": "",
            "status": "idle",
        }))
    return members


_load_teams()


def _builtin_team_templates() -> list[TeamTemplate]:
    now = "2026-06-14T00:00:00+00:00"
    return [
        TeamTemplate.model_validate({
            "id": "builtin-research-team",
            "name": "调研分析团队",
            "description": "适合资料检索、趋势调研、事实核查和结论汇总。",
            "category": "research",
            "builtin": True,
            "createdAt": now,
            "updatedAt": now,
            "members": [
                {"name": "调研 Leader", "role": "leader", "description": "拆解问题并汇总调研结论。", "keywords": ["调研", "研究", "头脑风暴", "leader"]},
                {"name": "资料检索 Worker", "role": "worker", "description": "检索资料和外部信息。", "keywords": ["调研", "搜索", "知识", "检索"]},
                {"name": "风险检查 Worker", "role": "worker", "description": "检查事实风险和不确定性。", "keywords": ["审查", "风险", "验证"]},
            ],
        }),
        TeamTemplate.model_validate({
            "id": "builtin-development-review-team",
            "name": "开发审查团队",
            "description": "适合代码实现、架构判断、测试验证和代码审查。",
            "category": "development",
            "builtin": True,
            "createdAt": now,
            "updatedAt": now,
            "members": [
                {"name": "开发 Leader", "role": "leader", "description": "规划实现路径并整合结果。", "keywords": ["架构", "开发", "头脑风暴", "leader"]},
                {"name": "开发 Worker", "role": "worker", "description": "执行代码实现。", "keywords": ["开发", "代码", "python", "react"]},
                {"name": "代码审查 Worker", "role": "worker", "description": "审查代码质量和回归风险。", "keywords": ["审查", "review", "测试"]},
                {"name": "测试 Worker", "role": "worker", "description": "设计并执行验证。", "keywords": ["测试", "验证", "质量"]},
            ],
        }),
        TeamTemplate.model_validate({
            "id": "builtin-knowledge-base-team",
            "name": "知识库问答团队",
            "description": "适合内部知识库检索、文档整理和最终问答。",
            "category": "knowledge",
            "builtin": True,
            "createdAt": now,
            "updatedAt": now,
            "members": [
                {"name": "知识库 Leader", "role": "leader", "description": "协调检索并生成最终答案。", "keywords": ["知识", "党建", "leader"]},
                {"name": "知识检索 Worker", "role": "worker", "description": "调用知识库检索工具。", "keywords": ["知识", "检索", "党建"]},
                {"name": "文档整理 Worker", "role": "worker", "description": "整理材料为结构化答案。", "keywords": ["文档", "写作", "整理"]},
            ],
        }),
        TeamTemplate.model_validate({
            "id": "builtin-data-analysis-team",
            "name": "数据分析团队",
            "description": "适合业务数据理解、指标拆解、分析和报告输出。",
            "category": "analysis",
            "builtin": True,
            "createdAt": now,
            "updatedAt": now,
            "members": [
                {"name": "分析 Leader", "role": "leader", "description": "定义分析目标并汇总洞察。", "keywords": ["数据", "分析", "leader"]},
                {"name": "数据处理 Worker", "role": "worker", "description": "清洗和处理数据。", "keywords": ["数据", "python", "开发"]},
                {"name": "指标分析 Worker", "role": "worker", "description": "分析指标和趋势。", "keywords": ["分析", "指标", "业务"]},
                {"name": "报告 Worker", "role": "worker", "description": "输出分析报告。", "keywords": ["文档", "报告", "写作"]},
            ],
        }),
    ]


def _custom_team_templates() -> list[TeamTemplate]:
    return [
        TeamTemplate.model_validate(item)
        for item in list_json_records(TEAM_TEMPLATES_NAMESPACE)
    ]


def _find_template(template_id: str) -> TeamTemplate | None:
    for template in _builtin_team_templates():
        if template.id == template_id:
            return template
    record = get_json_record(TEAM_TEMPLATES_NAMESPACE, template_id)
    return TeamTemplate.model_validate(record) if record else None


def _match_agent_for_template_member(
    member: TeamTemplateMember,
    used_agent_ids: set[str],
) -> str | None:
    if member.agent_id and get_registered_agent(member.agent_id):
        return member.agent_id
    candidates = list_registered_agents()
    keywords = [item.lower() for item in member.keywords if item.strip()]
    for agent in candidates:
        if agent.id in used_agent_ids:
            continue
        searchable = " ".join([agent.name, agent.description, " ".join(agent.tags)]).lower()
        if any(keyword in searchable for keyword in keywords):
            return agent.id
    for agent in candidates:
        if agent.id not in used_agent_ids:
            return agent.id
    return None


def _instantiate_template_members(template: TeamTemplate) -> tuple[str, list[TeamMember], list[str]]:
    used_agent_ids: set[str] = set()
    warnings: list[str] = []
    members: list[TeamMember] = []
    leader_agent_id = ""
    for item in template.members:
        agent_id = _match_agent_for_template_member(item, used_agent_ids)
        if not agent_id:
            warnings.append(f"模板成员 {item.name} 没有可匹配的 Agent，已跳过。")
            continue
        used_agent_ids.add(agent_id)
        agent = get_registered_agent(agent_id)
        if item.role == "leader" and not leader_agent_id:
            leader_agent_id = agent_id
        members.append(TeamMember.model_validate({
            "id": uuid.uuid4().hex,
            "agentId": agent_id,
            "name": agent.name if agent else item.name,
            "role": item.role,
            "description": item.description or (agent.description if agent else ""),
            "executionMode": item.execution_mode,
            "dependsOn": item.depends_on,
            "handoffPrompt": item.handoff_prompt,
            "status": "idle",
        }))
    if not leader_agent_id and members:
        leader_agent_id = members[0].agent_id
        members[0] = members[0].model_copy(update={"role": "leader"})
    return leader_agent_id, members, warnings


@router.get("", response_model=list[TeamRecord])
async def list_teams() -> list[TeamRecord]:
    """List Teams."""
    return sorted(_teams.values(), key=lambda item: item.updated_at, reverse=True)


@router.get("/templates", response_model=list[TeamTemplate])
async def list_team_templates() -> list[TeamTemplate]:
    """List built-in and custom Team templates."""
    return _builtin_team_templates() + sorted(
        _custom_team_templates(),
        key=lambda item: item.updated_at,
        reverse=True,
    )


@router.post("/templates/from-team/{team_id}", response_model=TeamTemplateSaveResponse, status_code=201)
async def save_team_as_template(team_id: str) -> TeamTemplateSaveResponse:
    """Save an existing Team as a custom template."""
    if team_id not in _teams:
        raise HTTPException(status_code=404, detail="Team not found")
    source = _teams[team_id]
    now = _utc_now()
    template = TeamTemplate.model_validate({
        "id": f"custom-{uuid.uuid4().hex}",
        "name": f"{source.name} 模板",
        "description": source.description,
        "category": "custom",
        "builtin": False,
        "createdAt": now,
        "updatedAt": now,
        "members": [
            {
                "name": member.name,
                "role": member.role,
                "description": member.description,
                "agentId": member.agent_id,
                "keywords": [member.name],
                "executionMode": member.execution_mode,
                "dependsOn": member.depends_on,
                "handoffPrompt": member.handoff_prompt,
            }
            for member in source.members
        ],
    })
    upsert_json_record(TEAM_TEMPLATES_NAMESPACE, template.id, template.model_dump(by_alias=True))
    return TeamTemplateSaveResponse(template=template)


@router.delete("/templates/{template_id}", status_code=204)
async def delete_team_template(template_id: str) -> None:
    """Delete one custom Team template."""
    template = _find_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Team template not found")
    if template.builtin:
        raise HTTPException(status_code=409, detail="Built-in Team template cannot be deleted")
    delete_json_record(TEAM_TEMPLATES_NAMESPACE, template_id)


@router.post("/templates/{template_id}/instantiate", response_model=TeamImportResponse, status_code=201)
async def instantiate_team_template(
    template_id: str,
    data: TeamTemplateInstantiateRequest,
) -> TeamImportResponse:
    """Create a Team from a template."""
    template = _find_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Team template not found")
    leader_agent_id, members, warnings = _instantiate_template_members(template)
    if not leader_agent_id or not members:
        raise HTTPException(status_code=400, detail="No available Agents matched this template")
    now = _utc_now()
    team = TeamRecord.model_validate({
        "id": uuid.uuid4().hex,
        "name": data.name.strip() if data.name and data.name.strip() else template.name,
        "description": template.description,
        "leaderAgentId": leader_agent_id,
        "members": members,
        "status": "idle",
        "createdAt": now,
        "updatedAt": now,
    })
    _teams[team.id] = team
    upsert_team(team.model_dump(by_alias=True))
    return TeamImportResponse(team=team, warnings=warnings)


@router.get("/{team_id}/export", response_model=TeamExportPackage)
async def export_team(team_id: str) -> TeamExportPackage:
    """Export one Team as a portable JSON package."""
    if team_id not in _teams:
        raise HTTPException(status_code=404, detail="Team not found")
    return TeamExportPackage(
        exportedAt=_utc_now(),
        team=_teams[team_id],
    )


@router.post("/import", response_model=TeamImportResponse, status_code=201)
async def import_team(data: TeamImportRequest) -> TeamImportResponse:
    """Import one Team package, creating a new Team by default."""
    if data.package.schema_version != "agent-workstation.team.v1":
        raise HTTPException(status_code=400, detail="Unsupported Team export schema")
    if data.conflict_strategy not in {"new_id", "keep_id"}:
        raise HTTPException(status_code=400, detail="Unsupported conflict strategy")

    source = data.package.team
    warnings: list[str] = []
    now = _utc_now()
    next_id = source.id
    if data.conflict_strategy == "new_id" or next_id in _teams:
        if next_id in _teams:
            warnings.append("Team ID already exists; imported with a new ID.")
        next_id = uuid.uuid4().hex

    next_name = source.name
    if any(team.name == next_name for team in _teams.values()):
        next_name = f"{source.name} Copy"
        warnings.append("Team name already exists; imported name was suffixed with Copy.")

    missing_agents = [
        member.agent_id
        for member in source.members
        if get_registered_agent(member.agent_id) is None
    ]
    if missing_agents:
        warnings.append(f"Missing referenced Agents: {', '.join(sorted(set(missing_agents)))}")

    imported_members = [
        member.model_copy(update={
            "id": uuid.uuid4().hex,
            "status": "idle",
        })
        for member in source.members
    ]
    imported = source.model_copy(update={
        "id": next_id,
        "name": next_name,
        "members": imported_members,
        "status": "idle",
        "created_at": now,
        "updated_at": now,
    })
    _teams[imported.id] = imported
    upsert_team(imported.model_dump(by_alias=True))
    return TeamImportResponse(team=imported, warnings=warnings)


@router.post("", response_model=TeamRecord, status_code=201)
async def create_team(data: TeamCreate) -> TeamRecord:
    """Create a Team."""
    now = _utc_now()
    team = TeamRecord.model_validate({
        "id": uuid.uuid4().hex,
        "name": data.name,
        "description": data.description,
        "leaderAgentId": data.leader_agent_id,
        "members": _build_members(data.leader_agent_id, data.worker_agent_ids),
        "status": "idle",
        "createdAt": now,
        "updatedAt": now,
    })
    _teams[team.id] = team
    upsert_team(team.model_dump(by_alias=True))
    return team


@router.get("/{team_id}", response_model=TeamRecord)
async def get_team(team_id: str) -> TeamRecord:
    """Get one Team."""
    if team_id not in _teams:
        raise HTTPException(status_code=404, detail="Team not found")
    return _teams[team_id]


@router.put("/{team_id}", response_model=TeamRecord)
async def update_team(team_id: str, data: TeamUpdate) -> TeamRecord:
    """Update a Team."""
    if team_id not in _teams:
        raise HTTPException(status_code=404, detail="Team not found")

    current = _teams[team_id]
    team_dict = current.model_dump(by_alias=True)
    update_data = data.model_dump(by_alias=True, exclude_unset=True)
    if "leaderAgentId" in update_data or "workerAgentIds" in update_data:
        leader_id = update_data.pop("leaderAgentId", current.leader_agent_id)
        worker_ids = update_data.pop(
            "workerAgentIds",
            [
                member.agent_id
                for member in current.members
                if member.role == "worker"
            ],
        )
        team_dict["leaderAgentId"] = leader_id
        team_dict["members"] = _build_members(leader_id, worker_ids)
    elif "members" in update_data:
        team_dict["members"] = update_data.pop("members")

    team_dict.update(update_data)
    team_dict["updatedAt"] = _utc_now()
    team = TeamRecord.model_validate(team_dict)
    _teams[team_id] = team
    upsert_team(team.model_dump(by_alias=True))
    return team


@router.delete("/{team_id}", status_code=204)
async def delete_team(team_id: str) -> None:
    """Delete a Team."""
    if team_id not in _teams:
        raise HTTPException(status_code=404, detail="Team not found")
    _teams.pop(team_id)
    db_delete_team(team_id)
