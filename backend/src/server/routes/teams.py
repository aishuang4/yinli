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
from src.server.routes.agents import get_registered_agent
from src.storage.database import (
    list_json_records,
    list_teams as db_list_teams,
    migrate_json_list_file,
    replace_teams,
    upsert_team,
    delete_team as db_delete_team,
)

router = APIRouter()

TEAMS_FILE = Path("data/teams.json")
TEAMS_NAMESPACE = "teams"

TeamMemberRole = Literal["leader", "worker"]
TeamStatus = Literal["idle", "running", "ended"]


class TeamMember(BaseModel):
    """Team member bound to an Agent profile."""

    id: str
    agent_id: str = Field(alias="agentId")
    name: str
    role: TeamMemberRole
    description: str = ""
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
    status: TeamStatus | None = None

    model_config = {"populate_by_name": True}


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
            "status": "idle",
        }))
    return members


_load_teams()


@router.get("", response_model=list[TeamRecord])
async def list_teams() -> list[TeamRecord]:
    """List Teams."""
    return sorted(_teams.values(), key=lambda item: item.updated_at, reverse=True)


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
