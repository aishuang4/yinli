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


def test_create_model_chain_returns_single_model_after_deduplication(monkeypatch) -> None:
    """去重后只剩一个模型时不再创建 fallback 包装层。"""

    single_model = SimpleNamespace(model="deepseek-v4-flash")
    monkeypatch.setattr(model_factory, "create_model", lambda *_args, **_kwargs: single_model)

    model = model_factory.create_model_chain([
        "deepseek-v4-flash",
        "deepseek-v4-flash",
    ])

    assert model is single_model
