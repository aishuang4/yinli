"""产品功能配置档：归一化、强制开启项、页面 API 门禁。"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.product_features import (
    load_product_features,
    normalize_product_features,
    reset_product_features_cache,
)
from src.server.product_feature_gate import ProductFeatureGateMiddleware
from src.server.routes import settings as settings_routes


def test_normalize_forces_chat_and_settings_on():
    features = normalize_product_features({
        "views": {"chat": False, "settings": False, "runs": True},
        "surfaces": {"teamMode": False},
    })
    assert features.views["chat"] is True
    assert features.views["settings"] is True
    assert features.views["runs"] is True
    assert features.surfaces["teamMode"] is False


def test_load_product_features_from_env_file(tmp_path, monkeypatch):
    path = tmp_path / "product-features.json"
    path.write_text(
        json.dumps({
            "views": {"traces": False, "chat": False},
            "surfaces": {"workflow": False, "groupStudio": False},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.config.settings.settings.product_features_path",
        str(path),
    )
    reset_product_features_cache()
    features = load_product_features(force=True)
    assert features.views["chat"] is True
    assert features.views["traces"] is False
    assert features.surfaces["workflow"] is False
    assert features.surfaces["groupStudio"] is False
    reset_product_features_cache()


def test_product_features_api_and_page_gate(tmp_path, monkeypatch):
    path = tmp_path / "product-features.json"
    path.write_text(
        json.dumps({
            "views": {"traces": False, "sandbox": True},
            "surfaces": {"workflow": False},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.config.settings.settings.product_features_path",
        str(path),
    )
    reset_product_features_cache()

    app = FastAPI()
    app.add_middleware(ProductFeatureGateMiddleware)
    app.include_router(settings_routes.router, prefix="/api/settings")

    @app.get("/api/traces")
    def traces() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/sandbox")
    def sandbox() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/chat/runs")
    def chat_runs() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/workflows")
    def workflows() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        payload = client.get("/api/settings/product-features").json()
        assert payload["views"]["traces"] is False
        assert payload["views"]["chat"] is True
        assert client.get("/api/traces").status_code == 404
        assert client.get("/api/sandbox").status_code == 200
        assert client.get("/api/chat/runs").status_code == 200
        assert client.get("/api/workflows").status_code == 404

    reset_product_features_cache()
