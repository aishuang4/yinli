"""运行时可写路径。

桌面端通过 ``AGENT_WORKSTATION_DATA_DIR`` 传入 Electron ``userData`` 根目录；
所有应用运行数据必须从该目录派生，不能写入安装目录或 PyInstaller 临时解压目录。

hosted 下与租户数据相关的路径一律通过本模块的函数解析（读取 ``current_tenant().data_root``），
不在模块顶层求值：import 时固定成某一个租户就是串数据。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from threading import RLock

INSTALL_BACKEND_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PROJECT_ROOT = INSTALL_BACKEND_ROOT.parent
USER_HOME_ROOT = Path.home().resolve()
USING_CUSTOM_DATA_ROOT = bool(os.getenv("AGENT_WORKSTATION_DATA_DIR", "").strip())


def _resolve_user_data_root() -> Path:
    configured = os.getenv("AGENT_WORKSTATION_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return INSTALL_BACKEND_ROOT


# 进程级根：hosted 下即部署的 ROOT（control/ shared/ tenants/ 的父目录）。
USER_DATA_ROOT = _resolve_user_data_root()
LOG_ROOT = USER_DATA_ROOT / "logs"
TEMP_ROOT = USER_DATA_ROOT / "tmp"


def tenants_root() -> Path:
    """hosted 下全部租户数据根的父目录。"""

    return USER_DATA_ROOT / "tenants"


def control_root() -> Path:
    """hosted 控制面（账号、会话、租户表）所在目录。"""

    return USER_DATA_ROOT / "control"


def shared_root() -> Path:
    """hosted 进程级共享文件（平台模型能力表）所在目录。"""

    return USER_DATA_ROOT / "shared"


def _tenant_root():
    from src.tenancy.context import current_tenant

    return current_tenant().data_root


def _is_hosted() -> bool:
    from src.tenancy.context import current_tenant

    return current_tenant().is_hosted


def tenant_root() -> Path:
    """当前租户的数据根；hosted 下没有租户上下文会抛 TenantMissing。"""

    return _tenant_root()


def data_root() -> Path:
    return tenant_root() / "data"


def memory_root() -> Path:
    return data_root() / "memory"


def vector_store_root() -> Path:
    return data_root() / "vector_store"


def team_runtime_root() -> Path:
    return data_root() / "team-runtime" / "runs"


def sandbox_root() -> Path:
    return data_root() / "sandboxes"


def skill_packages_root() -> Path:
    return tenant_root() / "skills"


def legacy_skill_packages_root() -> Path:
    return data_root() / "skill_packages"


def subagent_root() -> Path:
    return tenant_root() / "subagents"


def legacy_subagent_root() -> Path:
    return data_root() / "subagents"


# 与默认 Project 根 `workspace`（单数）区分：这里是 Workspace catalog 权威树。
def workspaces_catalog_root() -> Path:
    return tenant_root() / "workspaces"


def projects_root() -> Path:
    """hosted 下用户 Project 的父目录；桌面 Project 可在任意路径，不使用本函数。"""

    return tenant_root() / "projects"


def default_project_root() -> Path:
    """默认 Project 根：hosted 在租户目录内；桌面保持现有逻辑。"""

    if _is_hosted():
        return projects_root() / "default"
    # 桌面安装版的默认 Project 必须是稳定目录；开发模式继续使用仓库根目录。
    return USER_DATA_ROOT / "workspace" if USING_CUSTOM_DATA_ROOT else INSTALL_PROJECT_ROOT


def user_home_root() -> Path | None:
    """用户 HOME；hosted 下返回 None，调用方必须跳过 user scope（§12.7）。"""

    return None if _is_hosted() else USER_HOME_ROOT


def ensure_process_directories() -> None:
    """创建进程级目录；hosted 时附加控制面、共享与租户父目录。"""

    from src.tenancy.context import is_hosted_mode

    paths = [USER_DATA_ROOT, LOG_ROOT, TEMP_ROOT]
    if is_hosted_mode():
        paths += [control_root(), shared_root(), tenants_root()]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


_ensured_tenants: set[str] = set()
_ensured_lock = RLock()


def ensure_tenant_directories() -> None:
    """创建当前租户的数据目录并执行 legacy 迁移；每个租户只执行一次。"""

    from src.tenancy.context import current_tenant

    ctx = current_tenant()
    with _ensured_lock:
        if ctx.tenant_id in _ensured_tenants:
            return
        for path in (
            data_root(),
            default_project_root(),
            skill_packages_root(),
            subagent_root(),
            workspaces_catalog_root(),
        ):
            path.mkdir(parents=True, exist_ok=True)
        _migrate_legacy_skill_packages()
        _migrate_legacy_subagents()
        _ensured_tenants.add(ctx.tenant_id)


def forget_tenant_directories(tenant_id: str) -> None:
    """删除租户后，把它从 ensure_tenant_directories 的记忆集合中移除。"""

    with _ensured_lock:
        _ensured_tenants.discard(tenant_id)


def _migrate_legacy_skill_packages() -> None:
    """把旧 data/skill_packages 包安全迁移到运行时根目录 skills。"""

    legacy = legacy_skill_packages_root()
    if not legacy.is_dir():
        return
    for source in legacy.iterdir():
        target = skill_packages_root() / source.name
        if target.exists():
            continue
        try:
            shutil.move(str(source), str(target))
        except OSError:
            # 单个包迁移失败不阻塞服务启动，后续仍可由记录逻辑修复。
            continue


def _migrate_legacy_subagents() -> None:
    """把旧 data/subagents 定义安全迁移到运行时根目录 subagents。"""

    legacy = legacy_subagent_root()
    if not legacy.is_dir():
        return
    for source in legacy.iterdir():
        target = subagent_root() / source.name
        if target.exists():
            continue
        try:
            shutil.move(str(source), str(target))
        except OSError:
            # 单个定义迁移失败不阻塞服务启动，后续仍可手动处理。
            continue
