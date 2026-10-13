"""从 models.json 解析完整连接；ContextVar 固定一次运行的模型快照。"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from src.config.model_catalog import (
    CatalogError,
    ModelCatalog,
    ModelEntry,
    ProviderConfig,
    read_catalog,
)

SYSTEM_SETTINGS_NAMESPACE = "system_settings"


@dataclass(frozen=True)
class ResolvedModel:
    """模型条目与唯一供应商连接的不可变组合。"""

    entry: ModelEntry
    connection: ProviderConfig = field(repr=False)

    @property
    def provider(self):
        return self.connection.id

    @property
    def provider_name(self):
        return self.connection.name

    @property
    def adapter(self):
        return self.connection.adapter

    @property
    def api_key(self):
        return self.connection.api_key

    @property
    def base_url(self):
        return self.connection.base_url

    @property
    def default_model(self):
        return self.entry.model


@dataclass(frozen=True)
class RuntimeModelBinding:
    """本次运行的完整目录快照与已解析的降级链。"""

    catalog: ModelCatalog = field(repr=False)
    chain: tuple[ResolvedModel, ...] = field(repr=False)

    @property
    def primary(self):
        return self.chain[0]


_binding: ContextVar[RuntimeModelBinding | None] = ContextVar(
    "runtime_model_binding", default=None
)


def resolve_model_binding(
    model_config_id: str | None = None,
    *,
    catalog: ModelCatalog | None = None,
    validate: bool = True,
) -> RuntimeModelBinding:
    if (
        catalog is None
        and model_config_id
        and str(model_config_id).startswith("platform:")
    ):
        from src.models.platform_layer import platform_binding

        return platform_binding(str(model_config_id))
    catalog = catalog or read_catalog()[0]
    ident = (model_config_id or catalog.default_model_id or "").strip()
    entries = {item.id: item for item in catalog.models}
    providers = {item.id: item for item in catalog.providers}
    if not model_config_id and ident not in entries:
        from src.tenancy.context import is_hosted_mode

        if is_hosted_mode():
            from src.models.platform_layer import list_platform_models, platform_binding

            models = list_platform_models()
            if models:
                return platform_binding(str(models[0]["id"]))
            raise CatalogError("没有可用模型，请添加自定义模型")
    selected = entries.get(ident)
    selected_provider = providers.get(selected.provider_id) if selected is not None else None
    if (
        catalog is not None
        and selected is not None
        and selected_provider is not None
        and selected_provider.id == "yinli-platform"
    ):
        from src.models.platform_layer import platform_binding

        return platform_binding(f"platform:{selected.model}")
    if ident not in entries:
        raise CatalogError("所选模型配置不存在，请到模型管理重新选择。")
    chain, seen = [], set()

    def append(key):
        if key in seen:
            return
        seen.add(key)
        entry = entries[key]
        provider = providers[entry.provider_id]
        if validate:
            if not entry.enabled or not provider.enabled:
                raise CatalogError("所选模型或降级模型已停用。")
            if provider.requires_api_key and not provider.api_key.strip():
                raise CatalogError(f"供应商 {provider.name} 尚未配置 API Key。")
            if provider.adapter != "gemini" and not provider.base_url.strip():
                raise CatalogError(f"供应商 {provider.name} 尚未配置 Base URL。")
        chain.append(ResolvedModel(entry, provider))
        for child in entry.fallback_model_ids:
            append(child)

    append(ident)
    return RuntimeModelBinding(catalog, tuple(chain))


def current_model_binding() -> RuntimeModelBinding | None:
    return _binding.get()


def set_model_binding(binding: RuntimeModelBinding | None):
    return _binding.set(binding)


def reset_model_binding(token):
    _binding.reset(token)


@contextmanager
def model_binding_scope(binding: RuntimeModelBinding):
    token = _binding.set(binding)
    try:
        yield binding
    finally:
        _binding.reset(token)


def binding_for_model(model_name: str | None = None) -> RuntimeModelBinding:
    """普通执行继承快照；显式 Subagent 模型只允许唯一映射。"""
    binding = current_model_binding() or resolve_model_binding()
    if not model_name or model_name in (
        binding.primary.entry.id,
        binding.primary.default_model,
    ):
        return resolve_model_binding(binding.primary.entry.id, catalog=binding.catalog)
    return binding_for_explicit_model(model_name, binding=binding)


def binding_for_explicit_model(
    model_name: str, *, binding: RuntimeModelBinding | None = None
) -> RuntimeModelBinding:
    """显式定义即使与父模型同名，也不能在多个供应商间猜测。"""
    binding = binding or current_model_binding() or resolve_model_binding()
    entries = binding.catalog.models
    matches = [entry for entry in entries if entry.id == model_name]
    if not matches:
        matches = [entry for entry in entries if entry.model == model_name]
    if len(matches) != 1:
        raise CatalogError("显式模型名称不存在或对应多个供应商，请使用模型配置 ID。")
    return resolve_model_binding(matches[0].id, catalog=binding.catalog)


@dataclass(frozen=True)
class EffectiveModelSettings:
    """为既有 Runtime 编排提供不含二次供应商解析的读取视图。"""

    provider: str
    provider_name: str
    adapter: str
    api_key: str = field(repr=False)
    base_url: str
    default_model: str
    fallback_models: list[str]
    custom_models: list[str]


def get_effective_model_settings() -> EffectiveModelSettings:
    binding = current_model_binding() or resolve_model_binding(validate=False)
    primary = binding.primary
    return EffectiveModelSettings(
        primary.provider,
        primary.provider_name,
        primary.adapter,
        primary.api_key,
        primary.base_url,
        primary.default_model,
        [item.entry.id for item in binding.chain[1:]],
        [],
    )
