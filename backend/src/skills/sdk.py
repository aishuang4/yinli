"""AgentScope 适配：统一元数据与精确加载名，正文只在 viewer 访问时读取。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from agentscope.skill import Skill as AgentScopeSkill, SkillLoaderBase

from src.skills.models import ResolvedSkill, SkillCandidate, SkillSourceRoot
from src.skills.parser import parse_manual
from src.skills.reader import read_skill


class SkillRuntimePath(str):
    """兼容既有 list[str] 传递，同时携带本轮不可变解析结果。"""

    def __new__(cls, resolved: ResolvedSkill):
        value = super().__new__(cls, str(resolved.candidate.package_path))
        value.resolved_skill = resolved
        return value


def resolved_path(path: str) -> ResolvedSkill:
    """底层显式路径兼容；应用 Runtime 必须传递 SkillRuntimePath。"""

    selected = getattr(path, "resolved_skill", None)
    if selected:
        return selected
    from src.skills.catalog import current_skill_context, stable_skill_id
    from src.skills.policy import read_policy
    context = current_skill_context()
    directory = Path(path).resolve()
    raw = (directory / "SKILL.md").read_bytes()
    manual = parse_manual(raw, directory.name)
    root = SkillSourceRoot(key="explicit-" + hashlib.sha256(str(directory.parent).encode()).hexdigest()[:16],
                           owner_key=f"tenant:{context.tenant_id}", path=directory.parent,
                           priority=0, scope="explicit", kind="explicit")
    candidate = SkillCandidate(id=stable_skill_id(root, directory.name), name=manual.name, description=manual.description,
                               root=root, relative_path=directory.name, package_path=directory,
                               source_path=directory / "SKILL.md", content_hash=manual.content_hash,
                               load_name=manual.name)
    return ResolvedSkill(candidate=candidate, catalog_signature=manual.content_hash,
                         policy_path=context.policy_path, policy_version=read_policy(context.policy_path).version)


class FileAgentScopeSkill(AgentScopeSkill):
    """SDK 索引只访问元数据；Skill 工具访问 markdown 时复核文件版本。

    `name` 保持精确加载名（内置 viewer 按它查找）；`id` 是全局稳定 Skill ID，
    `display_name` 是 SKILL.md 声明的显示名称，二者只用于 catalog 展示与 Job 选择。
    """

    def __init__(self, resolved: ResolvedSkill, directory: str):
        self.resolved = resolved
        self.id = resolved.candidate.id
        self.name = resolved.candidate.load_name
        self.display_name = resolved.candidate.name
        self.description = resolved.candidate.description
        self.dir = directory
        self.updated_at = 0.0

    @property
    def markdown(self):
        return read_skill(self.resolved).markdown


class FileSkillLoader(SkillLoaderBase):
    """从应用快照创建 SDK 对象，不触发 LocalSkillLoader 二次解析。"""

    def __init__(self, path: str, runtime_directory: str | None = None):
        self.resolved = resolved_path(path)
        self.directory = runtime_directory or str(self.resolved.candidate.package_path)

    async def list_skills(self):
        return [FileAgentScopeSkill(self.resolved, self.directory)]


def runtime_package_name(path: str) -> str:
    """Docker 目录以稳定身份区分同 basename 包。"""
    return resolved_path(path).candidate.id
