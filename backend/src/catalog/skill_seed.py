"""系统 Skill 包 seed。"""

from __future__ import annotations

import shutil
from pathlib import Path

from src.catalog.skills import SYSTEM_SKILL_IDS
from src.config.runtime_paths import INSTALL_PROJECT_ROOT, skill_packages_root

YINLI_CATALOG_SKILL_ID = "yinli-catalog"
YINLI_CATALOG_SOURCE = INSTALL_PROJECT_ROOT / "artifacts" / "skills" / "yinli-catalog"
_ARTIFACTS_SKILLS = INSTALL_PROJECT_ROOT / "artifacts" / "skills"


def yinli_catalog_package_dir() -> Path:
    """运行时 Skill 包目录。"""

    return skill_packages_root() / YINLI_CATALOG_SKILL_ID


def seed_yinli_catalog_skill() -> Path:
    """兼容旧调用：只 seed catalog 说明书。"""

    seed_system_skills()
    return yinli_catalog_package_dir()


def _signature(directory: Path) -> str:
    """系统包内容签名不使用 mtime，用户修改不会被读操作覆盖。"""
    import hashlib
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def seed_system_skills() -> list[Path]:
    """仅启动/升级调用；重复 seed 不写盘，保护可编辑系统包。"""
    import json
    import os
    import tempfile
    from src.skills.policy import policy_transaction
    seeded = []
    root = skill_packages_root()
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / ".seed-signatures.json"
    with policy_transaction(root / ".seed.json"):
        try:
            state = json.loads(state_path.read_text())
        except FileNotFoundError:
            state = {}
        for skill_id in SYSTEM_SKILL_IDS:
            source, target = _ARTIFACTS_SKILLS / skill_id, root / skill_id
            if not (source / "SKILL.md").is_file():
                continue
            signature = _signature(source)
            if target.is_dir():
                actual = _signature(target)
                if actual == signature:
                    state[skill_id] = signature
                    seeded.append(target)
                    continue
                if skill_id != "yinli-catalog" and actual != state.get(skill_id):
                    # 升级包另存隐藏候选，保留用户改过的正文及脚本。
                    upgrade = root / ".upgrades" / skill_id / signature
                    if not upgrade.exists():
                        shutil.copytree(source, upgrade)
                    seeded.append(target)
                    continue
            stage = Path(tempfile.mkdtemp(prefix=".seed-", dir=root))
            try:
                shutil.copytree(source, stage, dirs_exist_ok=True)
                if target.exists():
                    old = root / f".old-{skill_id}"
                    if old.exists():
                        shutil.rmtree(old)
                    target.rename(old)
                    stage.rename(target)
                    shutil.rmtree(old)
                else:
                    stage.rename(target)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
            state[skill_id] = signature
            seeded.append(target)
        encoded = json.dumps(state, sort_keys=True)
        if not state_path.exists() or state_path.read_text() != encoded:
            from src.catalog.paths import atomic_write_text
            atomic_write_text(state_path, encoded)
    return seeded
