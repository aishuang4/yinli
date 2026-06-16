"""Sandbox test case routes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.storage.database import delete_json_record, get_json_record, list_json_records, upsert_json_record

router = APIRouter()

SANDBOX_CASES_NAMESPACE = "sandbox_cases"


class SandboxCasePayload(BaseModel):
    """Runtime target and prompt for one sandbox test case."""

    mode: str = Field(pattern="^(agent|team)$")
    agent_id: str = Field(default="", alias="agentId")
    agent_name: str = Field(default="", alias="agentName")
    team_id: str | None = Field(default=None, alias="teamId")
    team_name: str | None = Field(default=None, alias="teamName")
    message: str

    model_config = {"populate_by_name": True}


class SandboxCaseCreate(BaseModel):
    """Create a reusable sandbox case."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    payload: SandboxCasePayload


class SandboxCaseUpdate(BaseModel):
    """Update a reusable sandbox case."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    payload: SandboxCasePayload | None = None


class SandboxCase(SandboxCaseCreate):
    """Persisted reusable sandbox case."""

    id: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/cases", response_model=list[SandboxCase])
async def list_cases() -> list[SandboxCase]:
    """List reusable sandbox cases."""
    return [
        SandboxCase.model_validate(item)
        for item in sorted(
            list_json_records(SANDBOX_CASES_NAMESPACE),
            key=lambda record: str(record.get("updatedAt", "")),
            reverse=True,
        )
    ]


@router.post("/cases", response_model=SandboxCase)
async def create_case(data: SandboxCaseCreate) -> SandboxCase:
    """Create one reusable sandbox case."""
    now = _utc_now()
    case = SandboxCase(
        id=f"case-{uuid.uuid4().hex}",
        name=data.name.strip(),
        description=data.description.strip(),
        payload=data.payload,
        createdAt=now,
        updatedAt=now,
    )
    upsert_json_record(SANDBOX_CASES_NAMESPACE, case.id, case.model_dump(by_alias=True))
    return case


@router.patch("/cases/{case_id}", response_model=SandboxCase)
async def update_case(case_id: str, data: SandboxCaseUpdate) -> SandboxCase:
    """Update one reusable sandbox case."""
    existing = get_json_record(SANDBOX_CASES_NAMESPACE, case_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Sandbox case not found")
    next_data = dict(existing)
    if data.name is not None:
        next_data["name"] = data.name.strip()
    if data.description is not None:
        next_data["description"] = data.description.strip()
    if data.payload is not None:
        next_data["payload"] = data.payload.model_dump(by_alias=True)
    next_data["updatedAt"] = _utc_now()
    upsert_json_record(SANDBOX_CASES_NAMESPACE, case_id, next_data)
    return SandboxCase.model_validate(next_data)


@router.delete("/cases/{case_id}", status_code=204)
async def delete_case(case_id: str) -> None:
    """Delete one reusable sandbox case."""
    existing = get_json_record(SANDBOX_CASES_NAMESPACE, case_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Sandbox case not found")
    delete_json_record(SANDBOX_CASES_NAMESPACE, case_id)
