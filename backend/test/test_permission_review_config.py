"""审核用途引用模型目录且仅绑定 primary，不替代主 Agent 配置。"""

import pytest

from src.config.model_catalog import CatalogError, PermissionReviewConfig, public_catalog, read_catalog, save_catalog
from src.config.runtime_settings import resolve_permission_review_binding


def test_old_catalog_has_no_implicit_reviewer():
    catalog, _ = read_catalog()
    assert catalog.permission_review is None
    with pytest.raises(CatalogError, match="尚未配置"):
        resolve_permission_review_binding(catalog=catalog)


def test_review_config_atomic_roundtrip_and_conflict():
    public = public_catalog()
    public["permissionReview"] = PermissionReviewConfig(modelConfigId="primary").model_dump(by_alias=True)
    saved = save_catalog(public, public["etag"])
    assert saved["permissionReview"]["modelConfigId"] == "primary"
    assert "test-only" not in str(saved)
    with pytest.raises(CatalogError):
        save_catalog(public, public["etag"])


def test_reviewer_ignores_disabled_fallback_and_does_not_change_main_catalog():
    catalog, _ = read_catalog()
    models = tuple(model.model_copy(update={"fallback_model_ids": ("fallback",)}) if model.id == "primary"
                   else model.model_copy(update={"enabled": False}) if model.id == "fallback" else model
                   for model in catalog.models)
    catalog = catalog.model_copy(update={"models": models, "permission_review": PermissionReviewConfig(modelConfigId="primary")})
    binding = resolve_permission_review_binding(catalog=catalog)
    assert len(binding.chain) == 1
    assert binding.primary.entry.id == "primary"
    assert catalog.models[-1].enabled is False


def test_deleted_or_disabled_reviewer_is_unavailable_without_substitution():
    catalog, _ = read_catalog()
    for ident in ("deleted", "primary"):
        modified = catalog.model_copy(update={
            "permission_review": PermissionReviewConfig(modelConfigId=ident),
            "models": tuple(item.model_copy(update={"enabled": False}) if item.id == ident else item for item in catalog.models),
        })
        # 允许保存悬空引用显示不可用，但运行解析不能选择默认主模型。
        modified = type(catalog).model_validate(modified.model_dump(by_alias=True))
        with pytest.raises(CatalogError):
            resolve_permission_review_binding(catalog=modified)
