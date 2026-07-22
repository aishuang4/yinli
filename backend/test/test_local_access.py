"""桌面后端本地访问边界测试。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.settings import Settings
from src.server.local_access import LocalAccessMiddleware, is_local_client


def test_backend_defaults_to_loopback_host():
    """默认配置不得监听全部网卡。"""

    assert Settings().host == "127.0.0.1"


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
