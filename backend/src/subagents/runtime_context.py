"""Subagent 运行上下文构建。

该模块负责把 Subagent frontmatter 中的可复用上下文能力解析为 system prompt
片段，包括原生 Skills 路径和 Subagent 维度记忆注入。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.memory.retrieval import MemoryRetrievalContext, build_memory_injection_text
from src.runtime_context import RuntimeContext as PlatformRuntimeContext
from src.project_resources import resolve_project_resource_access
from src.server.routes.skills import Skill, discover_external_skills, resolve_installed_skills, resolve_runtime_skill_paths
from src.subagents.models import SubagentDefinition


@dataclass(frozen=True)
class SubagentRuntimeContext:
    """Subagent 子 Agent 运行前注入上下文。"""

    skill_paths: list[str] = field(default_factory=list)
    memory_prompt: str = ""
    loaded_skills: list[Skill] = field(default_factory=list)
    missing_skill_refs: list[str] = field(default_factory=list)
    memory_agent_id: str = ""
    errors: list[str] = field(default_factory=list)

    def audit_payload(self, definition: SubagentDefinition) -> dict:
        """生成写入 Runtime Event 的轻量审计 payload。"""

        return {
            "subagentName": definition.name,
            "skillRefs": list(definition.skills),
            "loadedSkills": [
                {
                    "id": skill.id,
                    "name": skill.name,
                }
                for skill in self.loaded_skills
            ],
            "missingSkillRefs": list(self.missing_skill_refs),
            "memoryAgentId": self.memory_agent_id,
            "memoryInjected": bool(self.memory_prompt.strip()),
            "errors": list(self.errors),
        }


def build_subagent_runtime_context(
    definition: SubagentDefinition,
    platform_context: PlatformRuntimeContext | None = None,
) -> SubagentRuntimeContext:
    """为 Subagent 构建 Skill 与记忆注入上下文。"""

    errors: list[str] = []
    try:
        external_skills = discover_external_skills(platform_context.project_root) if platform_context else []
        loaded_skills, missing_skill_refs = resolve_installed_skills(
            definition.skills,
            external_skills=external_skills,
        )
        if platform_context is not None:
            allowed_skill_ids = resolve_project_resource_access(platform_context).skill_ids
            denied_refs = [skill.name for skill in loaded_skills if skill.id not in allowed_skill_ids]
            loaded_skills = [
                skill
                for skill in loaded_skills
                if skill.id in allowed_skill_ids or skill.source_scope == "project"
            ]
            missing_skill_refs.extend(denied_refs)
        skill_paths = resolve_runtime_skill_paths(loaded_skills, runtime_context=platform_context)
    except Exception as exc:
        loaded_skills = []
        missing_skill_refs = list(definition.skills)
        skill_paths = []
        errors.append(f"Skill 预加载失败: {exc}")

    memory_agent_id = f"subagent:{definition.name}"
    try:
        memory_prompt = build_memory_injection_text(
            MemoryRetrievalContext(
                agent_id=memory_agent_id,
                agent_name=f"Subagent {definition.name}",
                workspace_id=platform_context.workspace_id if platform_context else "default",
                project_id=platform_context.project_id if platform_context else "default",
            ),
        )
    except Exception as exc:
        memory_prompt = ""
        errors.append(f"记忆注入失败: {exc}")

    return SubagentRuntimeContext(
        skill_paths=skill_paths,
        memory_prompt=memory_prompt,
        loaded_skills=loaded_skills,
        missing_skill_refs=missing_skill_refs,
        memory_agent_id=memory_agent_id,
        errors=errors,
    )
