"""审核用途引用模型目录且仅绑定 primary，不替代主 Agent 配置。"""

import pytest
from agentscope.model import ChatResponse

from src.agents.permission_review_model import ReviewModelResponse
from src.config.model_catalog import CatalogError, PermissionReviewConfig, public_catalog, read_catalog, save_catalog
from src.config.runtime_settings import resolve_permission_review_binding
from src.security.authorization.probe import PROBE_BUDGET_REASON, stamp_smartguard_probe
from test.authorization_fakes import run_async


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


def test_review_budget_defaults_to_sixteen_tokens():
    config = PermissionReviewConfig(modelConfigId="primary")
    assert config.timeout_seconds == 8 and config.max_output_tokens == 16


@run_async
async def test_save_probe_rejects_reasoning_model_that_only_returns_length():
    public = public_catalog()
    public["permissionReview"] = PermissionReviewConfig(modelConfigId="primary", probeStatus="passed").model_dump(by_alias=True)
    saved = save_catalog(public, public["etag"])
    assert saved["permissionReview"]["modelConfigId"] == "primary"

    async def length_only(*_args, **_kwargs):
        raise ValueError("审核响应被中断或截断")

    public = public_catalog()
    models = public["models"]
    other = next(item["id"] for item in models if item["id"] != "primary")
    public["permissionReview"] = PermissionReviewConfig(modelConfigId=other).model_dump(by_alias=True)
    with pytest.raises(CatalogError, match=PROBE_BUDGET_REASON):
        await stamp_smartguard_probe(public, transport=length_only)
    current, _ = read_catalog()
    assert current.permission_review is not None
    assert current.permission_review.model_config_id == "primary"


@run_async
async def test_save_probe_accepts_one_legal_token_and_stamps_passed():
    seen = {}

    async def allow_once(binding, **kwargs):
        seen["model"] = binding.primary.entry.id
        seen["tokens"] = kwargs["max_output_tokens"]
        assert len(binding.chain) == 1
        return ReviewModelResponse("allow")

    public = public_catalog()
    public["permissionReview"] = PermissionReviewConfig(modelConfigId="primary").model_dump(by_alias=True)
    stamped = await stamp_smartguard_probe(public, transport=allow_once)
    assert stamped["permissionReview"]["probeStatus"] == "passed"
    assert seen == {"model": "primary", "tokens": 16}
    saved = save_catalog(stamped, stamped["etag"])
    assert saved["permissionReview"]["probeStatus"] == "passed"


@run_async
async def test_save_probe_rejects_empty_finish_reason_length(monkeypatch):
    class EmptyLength:
        async def __call__(self, messages, tools=None, tool_choice=None):
            return ChatResponse(content=[], is_last=True, finished_reason="LENGTH")

    monkeypatch.setattr("src.agents.permission_review_model.create_model", lambda *args, **kwargs: EmptyLength())
    public = public_catalog()
    public["permissionReview"] = PermissionReviewConfig(modelConfigId="primary").model_dump(by_alias=True)
    with pytest.raises(CatalogError, match=PROBE_BUDGET_REASON):
        await stamp_smartguard_probe(public)
