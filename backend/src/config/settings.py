"""
应用配置 — 基于 pydantic-settings

从环境变量和 .env 文件加载配置。
支持类型安全的配置访问和自动验证。
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    应用全局配置

    配置项可通过环境变量覆盖（前缀：AGENT_WORKSTATION_）
    也可通过 .env 文件配置
    """

    # === 服务配置 ===
    host: str = "0.0.0.0"
    port: int = 8765

    # === DeepSeek 配置 ===
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    # === 百炼 DashScope 配置 ===
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # === 默认模型配置 ===
    default_model: str = "deepseek-v4-flash"
    fallback_models: list[str] = ["deepseek-v4-pro"]

    # === 数据库 ===
    database_url: str = "sqlite+aiosqlite:///./data/agent_workstation.db"

    # === Team Runtime 文件保留 ===
    team_run_retention_days: int = 30
    team_run_max_records: int = 200

    # === CORS ===
    cors_origins: list[str] = [
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Electron dev
    ]

    model_config = {
        "env_prefix": "AGENT_WORKSTATION_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


# 全局配置单例
settings = Settings()
