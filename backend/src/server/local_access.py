"""本地访问边界，拒绝非回环地址直接调用桌面后端。"""

from __future__ import annotations

import ipaddress

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def is_local_client(host: str) -> bool:
    """判断连接来源是否为本机回环地址。非 IP 名称仅供 ASGI 测试客户端使用。"""

    value = str(host or "").strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value in {"testclient"}
    if address.is_loopback:
        return True
    return bool(address.version == 6 and address.ipv4_mapped and address.ipv4_mapped.is_loopback)


class LocalAccessMiddleware:
    """只允许直接来自回环接口的 HTTP 请求，不包装或缓冲 SSE 响应。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] == "http":
            client = scope.get("client")
            client_host = str(client[0]) if client else ""
            if not is_local_client(client_host):
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "引力 YINLI 后端仅允许本机访问。"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
