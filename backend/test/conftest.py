"""测试使用临时模型目录，禁止读取用户连接与系统凭据。"""

import os
import tempfile

import pytest

_test_root = tempfile.TemporaryDirectory(prefix="yinli-tests-")
os.environ["AGENT_WORKSTATION_DATA_DIR"] = _test_root.name
os.environ["AGENT_WORKSTATION_SECRET_STORE"] = "memory"

# 保持开发模式语义：local 租户的默认 Project 根仍是仓库根目录。
from src.config import runtime_paths

runtime_paths.USING_CUSTOM_DATA_ROOT = False


@pytest.fixture(autouse=True)
def local_tenant(tmp_path, monkeypatch):
    """每个测试一份全新的 local 租户数据根。"""

    from src.config import runtime_paths
    from src.tenancy.context import TenantContext, use_tenant
    from src.tenancy.registry import evict_all
    from src.storage import database

    evict_all()
    database.forget_all()
    root = tmp_path / "tenant-local"
    root.mkdir()
    monkeypatch.setattr(runtime_paths, "USER_HOME_ROOT", tmp_path / "test-home")
    monkeypatch.setattr(runtime_paths, "USER_DATA_ROOT", root)  # local 租户 data_root 指向这里
    # 开发 .env 里的 database_url 指向固定库；测试必须改到这份临时根，否则所有用例共用一个库。
    monkeypatch.setattr(
        "src.config.settings.settings.database_url",
        "sqlite+aiosqlite:///" + str(root / "data" / "agent_workstation.db"),
    )
    ctx = TenantContext.local()
    with use_tenant(ctx):
        from src.session_migration.state import set_migration_flag

        set_migration_flag(True, schema_version_value=1)
        yield ctx
    database.forget_all()
    evict_all()
    runtime_paths.forget_tenant_directories(ctx.tenant_id)


@pytest.fixture(autouse=True)
def isolated_model_catalog(local_tenant):
    from src.config import model_catalog
    from src.config.runtime_settings import reset_model_binding, set_model_binding

    model_catalog._write(
        model_catalog.ModelCatalog(
            defaultModelId="deepseek-v4-flash",
            providers=[
                model_catalog.ProviderConfig(
                    id="deepseek",
                    name="DeepSeek",
                    adapter="deepseek",
                    baseUrl="http://127.0.0.1:1",
                    apiKey="test-only",
                )
            ],
            models=[
                model_catalog.ModelEntry(
                    id=name, providerId="deepseek", model=name, label=name
                )
                for name in (
                    "deepseek-v4-flash",
                    "deepseek-v4-pro",
                    "test-model",
                    "primary",
                    "fallback",
                )
            ],
        )
    )
    token = set_model_binding(None)
    yield
    reset_model_binding(token)
