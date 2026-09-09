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


MODEL_PROVIDERS: dict[str, ModelProvider] = {
    "dashscope": ModelProvider(
        id="dashscope",
        name="阿里云百炼 DashScope",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        models=(
            "qwen3.7-max",
            "qwen3.7-plus",
            "qwen-max",
            "qwen-plus",
            "qwen-turbo",
            "qwen-long",
        ),
        adapter="dashscope",
    ),
    "openai": ModelProvider(
        id="openai",
        name="OpenAI",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4.1",
        models=("gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o3", "o4-mini"),
        adapter="openai",
    ),
    "azure_openai": ModelProvider(
        id="azure_openai",
        name="Azure OpenAI",
        default_base_url="https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT",
        default_model="gpt-4.1",
        models=("gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"),
        adapter="openai",
    ),
    "deepseek": ModelProvider(
        id="deepseek",
        name="DeepSeek",
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-v4-flash",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        adapter="deepseek",
    ),
    "volcengine": ModelProvider(
        id="volcengine",
        name="火山引擎方舟",
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="doubao-1-5-pro-32k-250115",
        models=(
            "doubao-1-5-pro-32k-250115",
            "doubao-1-5-lite-32k-250115",
            "doubao-1-5-thinking-pro-250415",
        ),
        adapter="openai",
    ),
    "siliconflow": ModelProvider(
        id="siliconflow",
        name="SiliconFlow",
        default_base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen2.5-72B-Instruct",
        models=(
            "Qwen/Qwen2.5-72B-Instruct",
            "Qwen/Qwen2.5-32B-Instruct",
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
        ),
        adapter="openai",
    ),
    "openrouter": ModelProvider(
        id="openrouter",
        name="OpenRouter",
        default_base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4.1",
        models=(
            "openai/gpt-4.1",
            "openai/gpt-4o",
            "anthropic/claude-3.5-sonnet",
            "google/gemini-2.5-pro",
            "deepseek/deepseek-chat",
        ),
        adapter="openai",
    ),
    "groq": ModelProvider(
        id="groq",
        name="Groq",
        default_base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        models=(
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
        ),
        adapter="openai",
    ),
    "together": ModelProvider(
        id="together",
        name="Together AI",
        default_base_url="https://api.together.xyz/v1",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        models=(
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ),
        adapter="openai",
    ),
    "perplexity": ModelProvider(
        id="perplexity",
        name="Perplexity",
        default_base_url="https://api.perplexity.ai",
        default_model="sonar-pro",
        models=("sonar-pro", "sonar", "sonar-reasoning-pro", "sonar-reasoning"),
        adapter="openai",
    ),
    "moonshot": ModelProvider(
        id="moonshot",
        name="Moonshot",
        default_base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        models=("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"),
        adapter="moonshot",
    ),
    "gemini": ModelProvider(
        id="gemini",
        name="Google Gemini",
        default_base_url="",
        default_model="gemini-2.0-flash",
        models=(
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-pro",
        ),
        adapter="gemini",
    ),
    "anthropic": ModelProvider(
        id="anthropic",
        name="Anthropic",
        default_base_url="https://api.anthropic.com",
        default_model="claude-3-5-sonnet-latest",
        models=(
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest",
            "claude-3-opus-latest",
        ),
        adapter="anthropic",
    ),
    "ollama": ModelProvider(
        id="ollama",
        name="Ollama",
        default_base_url="http://127.0.0.1:11434",
        default_model="llama3.1",
        models=("llama3.1", "qwen2.5", "deepseek-r1", "mistral"),
        requires_api_key=False,
        adapter="ollama",
    ),
    "custom_openai": ModelProvider(
        id="custom_openai",
        name="自定义 OpenAI 兼容",
        default_base_url="",
        default_model="",
        models=(),
        requires_api_key=False,
        adapter="openai",
    ),
}
