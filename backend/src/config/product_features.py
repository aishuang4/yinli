"""产品功能配置档：打包前改 JSON，运行时前后端读同一份。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VIEW_KEYS = (
    "chat",
    "runs",
    "traces",
    "sandbox",
    "jobs",
    "memory",
    "agents",
    "models",
    "tools",
    "mcp",
    "skills",
    "subagents",
    "settings",
)
SURFACE_KEYS = (
    "workflow",
    "groupStudio",
    "teamMode",
    "planMode",
)
ALWAYS_ON_VIEWS = frozenset({"chat", "settings"})

_DEFAULT_VIEWS = {key: True for key in VIEW_KEYS}
_DEFAULT_VIEWS["runs"] = False
_DEFAULT_SURFACES = {key: True for key in SURFACE_KEYS}

_REPO_FEATURES_PATH = Path(__file__).resolve().parents[3] / "config" / "product-features.json"

_cache: tuple[str, float, "ProductFeatures"] | None = None


@dataclass(frozen=True)
class ProductFeatures:
    """归一化后的产品开关；缺键回退默认值。"""

    views: dict[str, bool]
    surfaces: dict[str, bool]
    source_path: str | None = None

    def as_public_dict(self) -> dict[str, dict[str, bool]]:
        """前端与设置接口使用的稳定 JSON。"""

        return {
            "views": {key: bool(self.views[key]) for key in VIEW_KEYS},
            "surfaces": {key: bool(self.surfaces[key]) for key in SURFACE_KEYS},
        }


def default_product_features() -> ProductFeatures:
    """仓库缺档时的内置默认：仅独立 Runs 页关闭。"""

    return ProductFeatures(views=dict(_DEFAULT_VIEWS), surfaces=dict(_DEFAULT_SURFACES))


def reset_product_features_cache() -> None:
    """测试或热读前清空进程缓存。"""

    global _cache
    _cache = None


def resolve_product_features_path() -> Path | None:
    """优先环境变量 / settings，其次仓库 config/product-features.json。"""

    from src.config.settings import settings

    configured = str(getattr(settings, "product_features_path", "") or "").strip()
    if configured:
        return Path(configured)
    if _REPO_FEATURES_PATH.is_file():
        return _REPO_FEATURES_PATH
    return None


def _coerce_flag(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    return fallback


def normalize_product_features(
    raw: Any,
    *,
    source_path: str | None = None,
) -> ProductFeatures:
    """合并缺省、忽略未知键，并强制 chat / settings 开启。"""

    payload = raw if isinstance(raw, dict) else {}
    raw_views = payload.get("views") if isinstance(payload.get("views"), dict) else {}
    raw_surfaces = payload.get("surfaces") if isinstance(payload.get("surfaces"), dict) else {}
    views = {
        key: _coerce_flag(raw_views.get(key), _DEFAULT_VIEWS[key])
        for key in VIEW_KEYS
    }
    for key in ALWAYS_ON_VIEWS:
        views[key] = True
    surfaces = {
        key: _coerce_flag(raw_surfaces.get(key), _DEFAULT_SURFACES[key])
        for key in SURFACE_KEYS
    }
    return ProductFeatures(views=views, surfaces=surfaces, source_path=source_path)


def load_product_features(*, force: bool = False) -> ProductFeatures:
    """读磁盘 JSON；按路径 + mtime 缓存。"""

    global _cache
    path = resolve_product_features_path()
    cache_key = str(path) if path is not None else ""
    mtime = -1.0
    if path is not None and path.is_file():
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = -1.0
    if not force and _cache is not None and _cache[0] == cache_key and _cache[1] == mtime:
        return _cache[2]
    features = default_product_features()
    if path is not None and path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        features = normalize_product_features(loaded, source_path=str(path))
    _cache = (cache_key, mtime, features)
    return features


def is_view_enabled(view: str) -> bool:
    """页面入口是否对用户开放。"""

    if view in ALWAYS_ON_VIEWS:
        return True
    return bool(load_product_features().views.get(view, False))


def is_surface_enabled(surface: str) -> bool:
    """Chat / 顶栏等横切能力是否开放。"""

    return bool(load_product_features().surfaces.get(surface, False))
