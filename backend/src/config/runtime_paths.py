"""运行时可写路径。

桌面端通过 ``AGENT_WORKSTATION_DATA_DIR`` 传入 Electron ``userData`` 根目录；
所有应用运行数据必须从该目录派生，不能写入安装目录或 PyInstaller 临时解压目录。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


INSTALL_BACKEND_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PROJECT_ROOT = INSTALL_BACKEND_ROOT.parent
USER_HOME_ROOT = Path.home().resolve()
USING_CUSTOM_DATA_ROOT = bool(os.getenv("AGENT_WORKSTATION_DATA_DIR", "").strip())


def _resolve_user_data_root() -> Path:
    configured = os.getenv("AGENT_WORKSTATION_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return INSTALL_BACKEND_ROOT


USER_DATA_ROOT = _resolve_user_data_root()
DATA_ROOT = USER_DATA_ROOT / "data"
LOG_ROOT = USER_DATA_ROOT / "logs"
TEMP_ROOT = USER_DATA_ROOT / "tmp"
MEMORY_ROOT = DATA_ROOT / "memory"
VECTOR_STORE_ROOT = DATA_ROOT / "vector_store"
TEAM_RUNTIME_ROOT = DATA_ROOT / "team-runtime" / "runs"
SKILL_PACKAGES_ROOT = USER_DATA_ROOT / "skills"
LEGACY_SKILL_PACKAGES_ROOT = DATA_ROOT / "skill_packages"
SUBAGENT_ROOT = USER_DATA_ROOT / "subagents"
LEGACY_SUBAGENT_ROOT = DATA_ROOT / "subagents"
USER_SUBAGENT_ROOT = SUBAGENT_ROOT
SANDBOX_ROOT = DATA_ROOT / "sandboxes"

# 桌面安装版的默认 Project 必须是稳定目录；开发模式继续使用仓库根目录。
DEFAULT_PROJECT_ROOT = (
    USER_DATA_ROOT / "workspace"
    if USING_CUSTOM_DATA_ROOT
    else INSTALL_PROJECT_ROOT
)


def ensure_runtime_directories() -> None:
    """创建桌面运行所需的稳定顶层目录。"""

    for path in (
        USER_DATA_ROOT,
        DATA_ROOT,
        LOG_ROOT,
        TEMP_ROOT,
        DEFAULT_PROJECT_ROOT,
        SKILL_PACKAGES_ROOT,
        SUBAGENT_ROOT,
    ):
        path.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_skill_packages()
    _migrate_legacy_subagents()


def _migrate_legacy_skill_packages() -> None:
    """把旧 data/skill_packages 包安全迁移到运行时根目录 skills。"""

    if not LEGACY_SKILL_PACKAGES_ROOT.is_dir():
        return
    for source in LEGACY_SKILL_PACKAGES_ROOT.iterdir():
        target = SKILL_PACKAGES_ROOT / source.name
        if target.exists():
            continue
        try:
            shutil.move(str(source), str(target))
        except OSError:
            # 单个包迁移失败不阻塞服务启动，后续仍可由记录逻辑修复。
            continue


def _migrate_legacy_subagents() -> None:
    """把旧 data/subagents 定义安全迁移到运行时根目录 subagents。"""

    if not LEGACY_SUBAGENT_ROOT.is_dir():
        return
    for source in LEGACY_SUBAGENT_ROOT.iterdir():
        target = SUBAGENT_ROOT / source.name
        if target.exists():
            continue
        try:
            shutil.move(str(source), str(target))
        except OSError:
            # 单个定义迁移失败不阻塞服务启动，后续仍可手动处理。
            continue
