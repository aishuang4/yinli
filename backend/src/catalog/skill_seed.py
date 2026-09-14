"""系统 Skill 包 seed。"""

from __future__ import annotations

import shutil
from pathlib import Path

from src.catalog.skills import SYSTEM_SKILL_IDS
from src.config.runtime_paths import INSTALL_PROJECT_ROOT, SKILL_PACKAGES_ROOT

YINLI_CATALOG_SKILL_ID = "yinli-catalog"
YINLI_CATALOG_SOURCE = INSTALL_PROJECT_ROOT / "artifacts" / "skills" / "yinli-catalog"
_ARTIFACTS_SKILLS = INSTALL_PROJECT_ROOT / "artifacts" / "skills"


def yinli_catalog_package_dir() -> Path:
    """运行时 Skill 包目录。"""

    return Path(SKILL_PACKAGES_ROOT) / YINLI_CATALOG_SKILL_ID


def seed_yinli_catalog_skill() -> Path:
    """兼容旧调用：只 seed catalog 说明书。"""

    seed_system_skills()
    return yinli_catalog_package_dir()


def seed_system_skills() -> list[Path]:
    """把系统 Skill 包覆盖复制到 SKILL_PACKAGES_ROOT。用户同名目录会被覆盖。"""

    seeded: list[Path] = []
    Path(SKILL_PACKAGES_ROOT).mkdir(parents=True, exist_ok=True)
    for skill_id in SYSTEM_SKILL_IDS:
        source = _ARTIFACTS_SKILLS / skill_id
        target = Path(SKILL_PACKAGES_ROOT) / skill_id
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            continue
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        seeded.append(target)
    return seeded
