"""运行时可写路径。

桌面端通过 ``AGENT_WORKSTATION_DATA_DIR`` 传入 Electron ``userData`` 根目录；
所有应用运行数据必须从该目录派生，不能写入安装目录或 PyInstaller 临时解压目录。

hosted 下与租户数据相关的路径一律通过本模块的函数解析（读取 ``current_tenant().data_root``），
不在模块顶层求值：import 时固定成某一个租户就是串数据。
"""

from __future__ import annotations

import os
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


def subagent_root() -> Path:
    return tenant_root() / "subagents"


def agents_root() -> Path:
    """Agent profile 权威目录。"""

    return tenant_root() / "agents"


def project_default_root(slug: str = "default") -> Path:
    """没有外部目录时，Project 的主目录。"""

    return projects_root() / slug


def session_root(session_id: str) -> Path:
    """会话文件根：上传、产物和 Run 大对象。"""

    return tenant_root() / "sessions" / session_id


def projects_root() -> Path:
    """hosted 下用户 Project 的父目录；桌面 Project 可在任意路径，不使用本函数。"""

    return tenant_root() / "projects"


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
    """创建当前租户的数据目录；每个租户只执行一次。"""

    from src.tenancy.context import current_tenant

    ctx = current_tenant()
    with _ensured_lock:
        if ctx.tenant_id in _ensured_tenants:
            return
        for path in (
            data_root(),
            project_default_root("default"),
            skill_packages_root(),
            subagent_root(),
            agents_root(),
            agents_root() / "profiles",
            agents_root() / "jobs",
            agents_root() / "_system" / "profiles",
            tenant_root() / "mcp",
            tenant_root() / "tools",
            tenant_root() / "sessions",
            tenant_root() / "backups",
        ):
            path.mkdir(parents=True, exist_ok=True)
        _ensured_tenants.add(ctx.tenant_id)


def forget_tenant_directories(tenant_id: str) -> None:
    """删除租户后，把它从 ensure_tenant_directories 的记忆集合中移除。"""

    with _ensured_lock:
        _ensured_tenants.discard(tenant_id)

