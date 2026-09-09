"""测试使用临时模型目录，禁止读取用户连接与系统凭据。"""

import os
import tempfile

import pytest

_test_root = tempfile.TemporaryDirectory(prefix="yinli-tests-")
os.environ["AGENT_WORKSTATION_DATA_DIR"] = _test_root.name
os.environ["AGENT_WORKSTATION_SECRET_STORE"] = "memory"

# 保持开发模式语义，同时让所有持久化文件进入临时数据根目录。
from src.config import runtime_paths

runtime_paths.USING_CUSTOM_DATA_ROOT = False
runtime_paths.DEFAULT_PROJECT_ROOT = runtime_paths.INSTALL_PROJECT_ROOT


@pytest.fixture(autouse=True)
def isolated_model_catalog(tmp_path, monkeypatch):
    from src.config import model_catalog
    from src.config.runtime_settings import reset_model_binding, set_model_binding

    monkeypatch.setattr(model_catalog, "MODELS_PATH", tmp_path / "models.json")
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
