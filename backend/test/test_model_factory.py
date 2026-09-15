"""模型创建与 fallback 链回归测试。"""

from types import SimpleNamespace

from src.agents import model_factory


def test_create_model_chain_uses_catalog_fallbacks(monkeypatch) -> None:
    """主模型与 fallback 重复时只创建并展示一次。"""

    created: list[str] = []

    def fake_create_model(name: str, **_kwargs):
        created.append(name)
        return SimpleNamespace(
            credential=object(),
            model=name,
            parameters=object(),
            stream=True,
            context_size=None,
        )

    monkeypatch.setattr(model_factory, "create_model", fake_create_model)
    monkeypatch.setattr(
        model_factory,
        "FallbackChainModel",
        lambda models, on_event=None: SimpleNamespace(
            model=" -> ".join(item.model for item in models),
            on_event=on_event,
        ),
    )

    from src.config.model_catalog import read_catalog, save_catalog
    catalog, etag = read_catalog()
    payload = catalog.model_dump(mode="json", by_alias=True)
    payload["models"][0]["fallbackModelIds"] = ["deepseek-v4-pro"]
    save_catalog(payload, etag)
    model = model_factory.create_model_chain(["deepseek-v4-flash"])

    assert created == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert model.model == "deepseek-v4-flash -> deepseek-v4-pro"


def test_resolve_context_size_prefers_entry_then_adapter_default() -> None:
    """显式 contextSize 优先；否则按 adapter 默认；未知 adapter 回到 32k。"""

    explicit = SimpleNamespace(entry=SimpleNamespace(context_size=64_000), adapter="deepseek")
    assert model_factory.resolve_context_size(explicit) == 64_000
    by_adapter = SimpleNamespace(entry=SimpleNamespace(context_size=0), adapter="deepseek")
    assert model_factory.resolve_context_size(by_adapter) == 128_000
    anthropic = SimpleNamespace(entry=SimpleNamespace(context_size=0), adapter="anthropic")
    assert model_factory.resolve_context_size(anthropic) == 200_000
    unknown = SimpleNamespace(entry=SimpleNamespace(context_size=0), adapter="mystery")
    assert model_factory.resolve_context_size(unknown) == model_factory.FALLBACK_CONTEXT_SIZE


def test_fallback_chain_uses_smallest_context_window() -> None:
    """降级链任何一环都可能承接请求，压缩阈值按链上最小窗口。"""

    def fake(name: str, size: int):
        return SimpleNamespace(credential=object(), model=name, parameters=object(), stream=True, context_size=size)

    chain = model_factory.FallbackChainModel([fake("a", 128_000), fake("b", 32_768)])
    assert chain.context_size == 32_768


def test_real_models_receive_catalog_context_size() -> None:
    """真实 ChatModel 构造必须拿到解析后的窗口，而不是 AgentScope 默认 32768。"""

    from src.config.model_catalog import read_catalog
    from src.config.runtime_settings import binding_for_model

    catalog, _ = read_catalog()
    entry = catalog.models[0]
    resolved = binding_for_model(entry.id).primary
    model = model_factory.create_model(entry.model, resolved_model=resolved)
    assert model.context_size == model_factory.resolve_context_size(resolved)
    assert model.context_size != 32_768 or resolved.adapter == "ollama"


def test_create_model_chain_returns_single_model_after_deduplication(monkeypatch) -> None:
    """去重后只剩一个模型时不再创建 fallback 包装层。"""

    single_model = SimpleNamespace(model="deepseek-v4-flash")
    monkeypatch.setattr(model_factory, "create_model", lambda *_args, **_kwargs: single_model)

    model = model_factory.create_model_chain([
        "deepseek-v4-flash",
        "deepseek-v4-flash",
    ])

    assert model is single_model
