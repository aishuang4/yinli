"""Configurable local tools."""

from datetime import datetime, timezone
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.server.routes.agents import remove_knowledge_tools_from_agents
from src.storage.database import (
    delete_json_record,
    list_json_records,
    upsert_json_record,
)


router = APIRouter()

KNOWLEDGE_TOOLS_NAMESPACE = "knowledge_tools"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_payload() -> dict[str, Any]:
    return {
        "query": "",
        "filename_pattern": "",
        "filterCategories": [],
        "searchMode": "",
    }


class KnowledgeToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="检索外部知识库并返回命中文档片段。", max_length=1000)
    url: str = Field(default="http://localhost:8080/search/knowledgeBaseSearch")
    authorization: str = Field(default="", description="Authorization header value")
    headers: dict[str, str] = Field(default_factory=dict)
    default_payload: dict[str, Any] = Field(default_factory=_default_payload, alias="defaultPayload")
    parameter_instructions: str = Field(
        default=(
            "参数说明：query 是检索问题；filename_pattern 用于按文件名过滤；"
            "filterCategories 是分类过滤数组；searchMode 是检索模式。"
        ),
        alias="parameterInstructions",
    )
    enabled: bool = True

    model_config = {"populate_by_name": True}


class KnowledgeToolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    url: str | None = None
    authorization: str | None = None
    headers: dict[str, str] | None = None
    default_payload: dict[str, Any] | None = Field(default=None, alias="defaultPayload")
    parameter_instructions: str | None = Field(default=None, alias="parameterInstructions")
    enabled: bool | None = None

    model_config = {"populate_by_name": True}


class KnowledgeToolResponse(KnowledgeToolCreate):
    id: str
    created_at: str
    updated_at: str


def _records() -> list[KnowledgeToolResponse]:
    return [
        KnowledgeToolResponse.model_validate(item)
        for item in list_json_records(KNOWLEDGE_TOOLS_NAMESPACE)
    ]


def get_enabled_knowledge_tools(tool_ids: list[str]) -> list[dict[str, Any]]:
    selected = set(tool_ids)
    return [
        item.model_dump(by_alias=True)
        for item in _records()
        if item.enabled and item.id in selected
    ]


@router.get("/knowledge-base", response_model=list[KnowledgeToolResponse])
async def list_knowledge_tools() -> list[KnowledgeToolResponse]:
    return _records()


@router.post("/knowledge-base", response_model=KnowledgeToolResponse, status_code=201)
async def create_knowledge_tool(data: KnowledgeToolCreate) -> KnowledgeToolResponse:
    now = _utc_now()
    tool = KnowledgeToolResponse.model_validate({
        **data.model_dump(by_alias=True),
        "id": str(uuid.uuid4()),
        "created_at": now,
        "updated_at": now,
    })
    upsert_json_record(KNOWLEDGE_TOOLS_NAMESPACE, tool.id, tool.model_dump(by_alias=True))
    return tool


@router.put("/knowledge-base/{tool_id}", response_model=KnowledgeToolResponse)
async def update_knowledge_tool(tool_id: str, data: KnowledgeToolUpdate) -> KnowledgeToolResponse:
    existing = next((item for item in _records() if item.id == tool_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="Knowledge tool not found")

    update_data = data.model_dump(exclude_unset=True, by_alias=True)
    next_data = existing.model_dump(by_alias=True)
    next_data.update(update_data)
    next_data["updated_at"] = _utc_now()
    tool = KnowledgeToolResponse.model_validate(next_data)
    upsert_json_record(KNOWLEDGE_TOOLS_NAMESPACE, tool.id, tool.model_dump(by_alias=True))
    return tool


@router.delete("/knowledge-base/{tool_id}", status_code=204)
async def delete_knowledge_tool(tool_id: str) -> None:
    existing = next((item for item in _records() if item.id == tool_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="Knowledge tool not found")

    delete_json_record(KNOWLEDGE_TOOLS_NAMESPACE, tool_id)
    remove_knowledge_tools_from_agents({tool_id})
