"""Workspace Skill 策略：跨线程/进程串行的版本校验与原子保存。"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.skills.models import SkillError


class RootConfig(BaseModel):
    """外部根以配置 ID 保持稳定身份。"""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    path: str


class SkillOverride(BaseModel):
    """只存策略，不复制包的元数据。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)
    enabled: bool | None = None
    shared: bool | None = Field(default=None, alias="availableToAllAgents")
    installed: bool | None = None


class SkillPolicy(BaseModel):
    """别名与 legacyOwnership 仅是来源证明，不声明包存在。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: int = Field(default=1, alias="schemaVersion", ge=1, le=1)
    version: int = Field(default=0, ge=0)
    create_dir: RootConfig | None = Field(default=None, alias="createDir")
    external_dirs: list[RootConfig] = Field(default_factory=list, alias="externalDirs", max_length=64)
    overrides: dict[str, SkillOverride] = Field(default_factory=dict, max_length=10000)
    legacy_aliases: dict[str, list[str]] = Field(default_factory=dict, alias="legacyAliases", max_length=10000)
    legacy_ownership: dict[str, str] = Field(default_factory=dict, alias="legacyOwnership", max_length=10000)
    migration: dict = Field(default_factory=dict)


_lock = RLock()


def read_policy(path: Path) -> SkillPolicy:
    """损坏配置 fail closed；只有完全不存在时采用默认策略。"""

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return SkillPolicy()
    except OSError as exc:
        raise SkillError("skill_policy_invalid", "无法读取 Skill 配置") from exc
    try:
        return SkillPolicy.model_validate_json(raw)
    except (ValueError, ValidationError) as exc:
        raise SkillError("skill_policy_invalid", "Skill 配置损坏，请修复 skills-policy.json") from exc


@contextmanager
def policy_transaction(path: Path):
    """独立锁文件不随 os.replace 换 inode；Windows 与 POSIX 均串行。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.with_suffix(".lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def update_policy(path: Path, mutate: Callable[[SkillPolicy], None], *, expected_version: int | None = None) -> SkillPolicy:
    """锁住完整 read-modify-write，回调失败或保存失败时保留旧文件。"""

    with policy_transaction(path):
        policy = read_policy(path)
        if expected_version is not None and policy.version != expected_version:
            raise SkillError("skill_policy_conflict", "Skill 配置已被其他操作更新，请刷新重试")
        mutate(policy)
        policy.version += 1
        policy = SkillPolicy.model_validate(policy.model_dump(by_alias=True))
        fd, temp = tempfile.mkstemp(prefix=".skills-policy-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(policy.model_dump(by_alias=True), ensure_ascii=False, indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)
        return policy


def set_skill_policy(path: Path, skill_id: str, **changes) -> SkillPolicy:
    """只更新指定字段，避免启停和共享互相覆盖。"""

    def mutate(policy: SkillPolicy) -> None:
        old = policy.overrides.get(skill_id, SkillOverride()).model_dump(by_alias=True)
        old.update(changes)
        policy.overrides[skill_id] = SkillOverride.model_validate(old)
    return update_policy(path, mutate)


def remove_skill_policy(path: Path, skill_id: str, *, relative_path: str = "") -> SkillPolicy:
    """删除受管包时同步清理策略和旧引用，避免生成幽灵 Skill。"""

    def mutate(policy: SkillPolicy) -> None:
        policy.overrides.pop(skill_id, None)
        for alias, targets in list(policy.legacy_aliases.items()):
            remaining = [target for target in targets if target != skill_id]
            if remaining:
                policy.legacy_aliases[alias] = remaining
            else:
                policy.legacy_aliases.pop(alias, None)
        if relative_path:
            policy.legacy_ownership.pop(relative_path, None)

    return update_policy(path, mutate)
