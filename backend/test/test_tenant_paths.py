"""路径函数按租户解析，且 §4.2 的旧常量在源码中绝迹。"""

from __future__ import annotations

import re
from pathlib import Path

from src.config import runtime_paths
from src.tenancy.context import TenantContext, use_tenant

HOSTED_A = "usr_" + "a" * 32
HOSTED_B = "usr_" + "b" * 32

PATH_FUNCTIONS = [
    runtime_paths.tenant_root,
    runtime_paths.data_root,
    runtime_paths.memory_root,
    runtime_paths.vector_store_root,
    runtime_paths.team_runtime_root,
    runtime_paths.sandbox_root,
    runtime_paths.skill_packages_root,
    runtime_paths.legacy_skill_packages_root,
    runtime_paths.subagent_root,
    runtime_paths.legacy_subagent_root,
    runtime_paths.workspaces_catalog_root,
    runtime_paths.projects_root,
    runtime_paths.default_project_root,
]


def test_paths_are_scoped_to_each_tenant():
    with use_tenant(TenantContext.hosted(HOSTED_A)):
        paths_a = [fn() for fn in PATH_FUNCTIONS]
        assert runtime_paths.user_home_root() is None
    with use_tenant(TenantContext.hosted(HOSTED_B)):
        paths_b = [fn() for fn in PATH_FUNCTIONS]

    assert paths_a != paths_b
    for path_a, path_b in zip(paths_a, paths_b):
        assert path_a.is_relative_to(runtime_paths.tenants_root() / HOSTED_A), path_a
        assert path_b.is_relative_to(runtime_paths.tenants_root() / HOSTED_B), path_b
        assert not path_a.is_relative_to(runtime_paths.tenants_root() / HOSTED_B)
        assert not path_b.is_relative_to(runtime_paths.tenants_root() / HOSTED_A)


def test_local_tenant_paths_stay_under_user_data_root():
    ctx = TenantContext.local()
    with use_tenant(ctx):
        for fn in PATH_FUNCTIONS[:-1]:  # default_project_root 在桌面可以是仓库根
            assert fn().is_relative_to(runtime_paths.USER_DATA_ROOT)
        assert runtime_paths.user_home_root() == runtime_paths.USER_HOME_ROOT


def test_ensure_tenant_directories_runs_once_per_tenant():
    ctx = TenantContext.hosted(HOSTED_A)
    with use_tenant(ctx):
        runtime_paths.forget_tenant_directories(HOSTED_A)
        runtime_paths.ensure_tenant_directories()
        for fn in (
            runtime_paths.data_root,
            runtime_paths.default_project_root,
            runtime_paths.skill_packages_root,
            runtime_paths.subagent_root,
            runtime_paths.workspaces_catalog_root,
        ):
            assert fn().is_dir()


def test_legacy_path_constants_are_gone_from_source():
    """§4.2 收尾 grep 的测试化：旧模块常量不允许回到 src/。"""

    banned = re.compile(
        r"\b(DATA_ROOT|MEMORY_ROOT|VECTOR_STORE_ROOT|TEAM_RUNTIME_ROOT|SANDBOX_ROOT|"
        r"SKILL_PACKAGES_ROOT|SUBAGENT_ROOT|USER_SUBAGENT_ROOT|WORKSPACES_CATALOG_ROOT|"
        r"DEFAULT_PROJECT_ROOT|MODELS_PATH|DB_PATH|PROJECT_ROOT|SECRET_DIR|WORKSPACE_DIR|"
        r"DATA_DIR|SKILL_PACKAGES_DIR|SUBAGENT_USER_DIR|CHAT_DATA_PATH|"
        r"ensure_runtime_directories)\b"
    )
    src_root = Path(runtime_paths.INSTALL_BACKEND_ROOT) / "src"
    offenders: list[str] = []
    for file in src_root.rglob("*.py"):
        if file == Path(runtime_paths.__file__):
            continue
        for lineno, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            if banned.search(line):
                offenders.append(f"{file.relative_to(src_root)}:{lineno}: {line.strip()}")
    assert offenders == []
