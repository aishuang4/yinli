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
    host: str = "127.0.0.1"
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

    # === 产品功能档（打包前改 config/product-features.json）===
    product_features_path: str = ""

    # === 托管账号 ===
    # desktop：本机回环，不强制登录。hosted：工作台要求会话。
    serve_mode: str = "desktop"
    admin_emails: str = ""
    admin_initial_password: str = ""
    auth_email_mode: str = "smtp"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    # 引力模型平台。地址固定为已发布的 key2api；令牌只放在服务器环境变量。
    key2api_base_url: str = "http://103.236.80.228:3000"
    key2api_sync_token: str = ""
    # 桌面端登录引力云端的默认地址。登录表单可以改。
    cloud_base_url: str = ""
    sandbox_docker_host: str = ""
    sandbox_tls_ca: str = ""
    sandbox_tls_cert: str = ""
    sandbox_tls_key: str = ""
    sandbox_host_data_root: str = ""
    sandbox_runtime: str = "runsc"
    sandbox_allow_runc: bool = False
    sandbox_network: str = "yinli-sandbox"
    sandbox_images: str = "python:3.11-slim"
    sandbox_memory_mb: int = 1024
    sandbox_cpus: float = 1.0
    sandbox_pids: int = 256
    sandbox_max_timeout_seconds: int = 600
    tenant_disk_mb: int = 2048
    tenant_upload_mb: int = 50
    tenant_max_active_runs: int = 3
    tenant_max_jobs: int = 20
    tenant_max_firings_per_day: int = 50

    model_config = {
        "env_prefix": "AGENT_WORKSTATION_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # .env.example 里的 VECTOR_STORE_PROVIDER / EMBEDDING_* 由 VectorStorePolicy 读，
        # 不是本 Settings 字段；忽略以免复制即启动失败。
        "extra": "ignore",
    }


class HostedConfigError(RuntimeError):
    """托管模式缺少可发送验证邮件的配置。"""


def parsed_admin_emails(config: Settings | None = None) -> set[str]:
    """解析逗号或分号分隔的管理员邮箱。"""

    source = config if config is not None else settings
    raw = source.admin_emails.replace(";", ",")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def assert_hosted_email_config(config: Settings | None = None) -> None:
    """托管且非 log 邮件模式时，必须具备 SMTP 主机和发件人。"""

    source = config if config is not None else settings
    mode = source.serve_mode.strip().lower() or "desktop"
    if mode not in {"desktop", "hosted"}:
        raise HostedConfigError(f"未知的服务模式: {source.serve_mode}")
    if mode != "hosted":
        return
    email_mode = source.auth_email_mode.strip().lower() or "smtp"
    if email_mode == "log":
        return
    if email_mode != "smtp":
        raise HostedConfigError("邮件模式只能是 smtp 或 log")
    if not source.smtp_host.strip() or not source.smtp_from.strip():
        raise HostedConfigError("托管模式必须配置 SMTP 主机和发件人，或把邮件模式设为 log")


def assert_hosted_platform_config(config: Settings | None = None) -> None:
    """托管必须有 key2api 服务令牌，平台目录不再回退部署级 models.json。"""

    source = config if config is not None else settings
    if (source.serve_mode.strip().lower() or "desktop") != "hosted":
        return
    if not source.key2api_sync_token.strip():
        raise HostedConfigError("托管模式必须配置 AGENT_WORKSTATION_KEY2API_SYNC_TOKEN")


def assert_hosted_sandbox(config: Settings | None = None) -> None:
    """托管必须指定沙箱 daemon。runc 需要显式放行。"""

    source = config if config is not None else settings
    if (source.serve_mode.strip().lower() or "desktop") != "hosted":
        return
    if not source.sandbox_docker_host.strip():
        raise HostedConfigError("托管模式必须配置 AGENT_WORKSTATION_SANDBOX_DOCKER_HOST")
    if source.sandbox_runtime.strip() == "runc" and not source.sandbox_allow_runc:
        raise HostedConfigError("使用 runc 必须同时设置 AGENT_WORKSTATION_SANDBOX_ALLOW_RUNC=true")


# 全局配置单例
settings = Settings()
