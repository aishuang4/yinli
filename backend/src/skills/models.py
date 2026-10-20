"""文件 Skill 的不可变领域合同；目录不保存或序列化说明书正文。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SkillError(ValueError):
    """所有入口共用的可识别加载错误。"""

    def __init__(self, code: str, message: str, alternatives: tuple[str, ...] = ()):
        super().__init__(message)
        self.code = code
        self.alternatives = alternatives

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "alternatives": list(self.alternatives)}


class FrozenModel(BaseModel):
    """仅包含不可变字段的值对象。"""

    model_config = ConfigDict(frozen=True)


class SkillContext(FrozenModel):
    """明确运行归属，绝不从 cwd 推导 Project。"""

    tenant_root: Path
    tenant_id: str = "local"
    workspace_id: str = "default"
    project_id: str = ""
    project_root: Path | None = None
    home_root: Path | None = None
    hosted: bool = False
    allowed_roots: tuple[Path, ...] = ()

    @property
    def policy_path(self) -> Path:
        """Tenant 全局 Skill policy。Workspace / Session 不再各存一份開關。"""

        return self.tenant_root / "policies" / "skills-policy.json"


class SkillSourceRoot(FrozenModel):
    """逻辑根身份和实际定位分离；priority 数字越小优先级越高。"""

    key: str
    owner_key: str
    path: Path
    priority: int
    scope: str
    kind: str
    enabled: bool = True
    managed: bool = False


class SkillDiagnostic(FrozenModel):
    """扫描和迁移诊断，不冒充可执行包。"""

    code: str
    message: str
    source: str = ""
    reference: str = ""


class SkillCandidate(FrozenModel):
    """已从真实文件解析的候选；安装与解析状态互相独立。"""

    id: str
    name: str
    description: str = ""
    trigger: str = ""
    category: str = "外部导入"
    tags: tuple[str, ...] = ()
    root: SkillSourceRoot
    relative_path: str
    package_path: Path
    source_path: Path
    content_hash: str = ""
    resources: tuple[str, ...] = ()
    load_name: str
    enabled: bool = True
    shared: bool = True
    installed: bool = True
    state: Literal["resolved", "shadowed", "ambiguous", "invalid", "missing"] = "resolved"
    error_code: str = ""
    reason: str = ""
    shadowed_by: tuple[str, ...] = ()
    legacy_aliases: tuple[str, ...] = ()

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.name, self.package_path.name, self.relative_path, *self.legacy_aliases)))


class SkillCatalogSnapshot(FrozenModel):
    """一次 Run 使用的目录；读取时仍校验真实字节版本与源路径。"""

    context: SkillContext
    roots: tuple[SkillSourceRoot, ...]
    candidates: tuple[SkillCandidate, ...]
    diagnostics: tuple[SkillDiagnostic, ...] = ()
    policy_version: int = 0
    signature: str


class ResolvedSkill(FrozenModel):
    """解析和授权均已通过的目标。"""

    candidate: SkillCandidate
    catalog_signature: str
    policy_path: Path
    policy_version: int


class SkillRead(FrozenModel):
    """显式读取的说明书结果，支持文件只列举路径。"""

    candidate: SkillCandidate
    markdown: str
    content_hash: str
    resources: tuple[str, ...]
