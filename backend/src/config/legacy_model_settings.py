"""旧模型配置的迁移读取与供应商模板；不作为在线配置源。"""

from dataclasses import dataclass

SYSTEM_SETTINGS_NAMESPACE = "system_settings"
MODEL_SETTINGS_KEY = "model"


@dataclass(frozen=True)
class ModelProvider:
    id: str
    name: str
    default_base_url: str
    default_model: str
    models: tuple[str, ...] = ()
    requires_api_key: bool = True
    adapter: str = "openai"


# 首次安装只预置 DeepSeek；OpenAI / 百炼 / Ollama 等由用户在「模型」页自行添加。
MODEL_PROVIDERS: dict[str, ModelProvider] = {
    "deepseek": ModelProvider(
        id="deepseek",
        name="DeepSeek",
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-v4-flash",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        adapter="deepseek",
    ),
}
