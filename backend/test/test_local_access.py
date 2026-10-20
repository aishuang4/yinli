"""桌面后端本地访问边界测试。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.settings import Settings
from src.server.local_access import LocalAccessMiddleware, is_local_client


def test_backend_defaults_to_loopback_host():
    """默认配置不得监听全部网卡。"""

    assert Settings().host == "127.0.0.1"


def test_settings_ignores_vector_store_env_file_keys(tmp_path, monkeypatch):
    """复制 .env.example 后 VECTOR_STORE_* 不得让 Settings 启动失败。"""

    env_file = tmp_path / ".env"
    env_file.write_text(
        "VECTOR_STORE_PROVIDER=zvec\n"
        "EMBEDDING_PROVIDER=disabled\n"
        "AGENT_WORKSTATION_HOST=127.0.0.1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    loaded = Settings(_env_file=env_file)
    assert loaded.host == "127.0.0.1"


def test_local_access_recognizes_loopback_and_rejects_lan_addresses():
    """访问控制同时覆盖 IPv4、IPv6 与 IPv4-mapped IPv6。"""

    assert is_local_client("127.0.0.1") is True
    assert is_local_client("::1") is True
    assert is_local_client("::ffff:127.0.0.1") is True
    assert is_local_client("192.168.1.20") is False
    assert is_local_client("203.0.113.5") is False


def test_local_access_middleware_blocks_non_loopback_client():
    """即使服务误绑全部网卡，非回环连接也不能进入 API。"""

    app = FastAPI()
    app.add_middleware(LocalAccessMiddleware)

    @app.get("/api/test")
    async def endpoint() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app, client=("192.168.1.20", 50000)) as client:
        response = client.get("/api/test")

    assert response.status_code == 403
    assert response.json()["detail"] == "引力 YINLI 后端仅允许本机访问。"


def test_hosted_loopback_still_requires_a_session(monkeypatch):
    """反向代理把来源变成回环地址时，托管模式仍然要求会话。"""

    from src.server.auth.middleware import HostedSessionMiddleware

    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    app = FastAPI()
    app.add_middleware(HostedSessionMiddleware)
    app.add_middleware(LocalAccessMiddleware)

    @app.get("/api/chat/send")
    async def chat() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.get("/api/chat/send")
    assert response.status_code == 401


def test_desktop_loopback_chat_allows_anonymous_local_use(monkeypatch):
    """桌面本机回环对话不要求登录。"""

    from src.server.auth.middleware import HostedSessionMiddleware

    monkeypatch.setattr("src.config.settings.settings.serve_mode", "desktop")
    app = FastAPI()
    app.add_middleware(HostedSessionMiddleware)
    app.add_middleware(LocalAccessMiddleware)

    @app.get("/api/chat/send")
    async def chat() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.get("/api/chat/send")
    assert response.status_code == 200


def test_desktop_cloud_base_url_is_read_from_pack_file(tmp_path):
    from src.config.settings import read_desktop_cloud_base_url

    path = tmp_path / "desktop.json"
    path.write_text('{"cloudBaseUrl":"http://cloud.example/"}', encoding="utf-8")
    assert read_desktop_cloud_base_url(path) == "http://cloud.example"
