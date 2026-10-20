"""托管出站地址校验。

完整传输层和 web_fetch 迁移在任务 6.1。这里先挡住模型地址指向本机或内网。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

import httpx

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_BLOCKED_NAMES = {"localhost", "metadata.google.internal"}


class EgressBlocked(ValueError):
    """用户提供的地址不能从托管服务出站。"""


def egress_enforced() -> bool:
    from src.tenancy.context import TenantMissing, current_tenant, is_hosted_mode

    try:
        return current_tenant().is_hosted
    except TenantMissing:
        return is_hosted_mode()


async def assert_public_url(url: str, *, purpose: str) -> None:
    assert_public_url_sync(url, purpose=purpose)


def assert_public_url_sync(url: str, *, purpose: str) -> None:
    """hosted 下只允许 http(s) 公网地址。desktop 直接返回。"""

    del purpose
    if not egress_enforced():
        return
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise EgressBlocked("模型地址不能指向内网或本机")
    if parts.username or parts.password:
        raise EgressBlocked("模型地址不能指向内网或本机")
    host = parts.hostname.lower().rstrip(".")
    if host in _allow_hosts():
        return
    if host in _BLOCKED_NAMES:
        raise EgressBlocked("模型地址不能指向内网或本机")
    addresses = _addresses(host)
    if not addresses or any(_blocked(ip) for ip in addresses):
        raise EgressBlocked("模型地址不能指向内网或本机")


def _allow_hosts() -> set[str]:
    raw = os.getenv("AGENT_WORKSTATION_EGRESS_ALLOW_HOSTS", "")
    return {item.strip().lower().rstrip(".") for item in raw.split(",") if item.strip()}


def _addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    found = []
    for info in infos:
        try:
            found.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    return found


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return True
    if ip in {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


class GuardedAsyncTransport(httpx.AsyncHTTPTransport):
    """托管下每次请求先校验目标，包含调用方手动跟随的重定向。"""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if egress_enforced():
            assert_public_url_sync(str(request.url), purpose="http")
        return await super().handle_async_request(request)


def guarded_async_client(**kwargs: object) -> httpx.AsyncClient:
    """desktop 保持调用方参数。hosted 关闭自动重定向，改由传输层逐跳校验。"""

    from src.security.authorization.execution_context import has_authorized_action

    if has_authorized_action():
        # 批准仅绑定当前请求；新目标需另建行动，不转发正文或凭据。
        kwargs["follow_redirects"] = False
    if not egress_enforced():
        return httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]
    kwargs["follow_redirects"] = False
    kwargs["transport"] = GuardedAsyncTransport()
    return httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]


def assert_redirect(location: str) -> None:
    """重定向的下一跳也必须是公网地址。"""

    assert_public_url_sync(location, purpose="redirect")
