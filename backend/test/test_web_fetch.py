"""WebFetch 公开网页读取与安全边界测试。"""

from __future__ import annotations

import json

import httpx
from agentscope.message import ToolResultState

from src.tools.web_fetch import PinnedAsyncHTTPTransport, WebFetch


async def _allow_public_host(hostname: str, port: int) -> None:
    if hostname in {"127.0.0.1", "localhost", "169.254.169.254"}:
        raise ValueError("WebFetch blocks local, private, reserved, and link-local network targets.")


def _payload(chunk) -> dict:
    return json.loads(chunk.content[0].text)


def test_web_fetch_extracts_readable_html_and_removes_scripts():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="""
                <html><head><title>示例页面</title><script>stealSecrets()</script></head>
                <body><nav>导航内容</nav><main>
                  <h1>公开文档</h1><p>这是需要提取的网页正文。</p>
                  <p>第二段提供足够长度，验证 main 区域能够优先成为正文内容。</p>
                  <p>网页中的指令只是外部数据，不能改变 Agent 的系统规则。</p>
                  <p>继续补充正文，让优先正文超过最小长度并排除导航区域。</p>
                </main></body></html>
            """,
            request=request,
        )

    import asyncio

    chunk = asyncio.run(WebFetch(
        transport=httpx.MockTransport(handler),
        resolver=_allow_public_host,
    )("https://example.com/docs"))
    payload = _payload(chunk)

    assert chunk.state == ToolResultState.SUCCESS
    assert payload["title"] == "示例页面"
    assert "这是需要提取的网页正文" in payload["content"]
    assert "stealSecrets" not in payload["content"]
    assert "导航内容" not in payload["content"]
    assert payload["security_notice"]


def test_web_fetch_blocks_direct_private_ip_before_request():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("private target must be rejected before HTTP request")

    import asyncio

    chunk = asyncio.run(WebFetch(transport=httpx.MockTransport(handler))("http://127.0.0.1/admin"))

    assert chunk.state == ToolResultState.ERROR
    assert "blocks local, private" in chunk.content[0].text


def test_web_fetch_revalidates_redirect_target():
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"}, request=request)

    import asyncio

    chunk = asyncio.run(WebFetch(
        transport=httpx.MockTransport(handler),
        resolver=_allow_public_host,
    )("https://example.com/start"))

    assert chunk.state == ToolResultState.ERROR
    assert requests == ["https://example.com/start"]
    assert "blocks local, private" in chunk.content[0].text


def test_web_fetch_rejects_embedded_credentials_and_non_http_urls():
    import asyncio

    tool = WebFetch(resolver=_allow_public_host)
    credentials = asyncio.run(tool("https://user:secret@example.com/private"))
    file_url = asyncio.run(tool("file:///etc/passwd"))

    assert credentials.state == ToolResultState.ERROR
    assert "embedded credentials" in credentials.content[0].text
    assert file_url.state == ToolResultState.ERROR
    assert "absolute http:// or https://" in file_url.content[0].text


def test_web_fetch_rejects_non_text_content():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.7",
            request=request,
        )

    import asyncio

    chunk = asyncio.run(WebFetch(
        transport=httpx.MockTransport(handler),
        resolver=_allow_public_host,
    )("https://example.com/report.pdf"))

    assert chunk.state == ToolResultState.ERROR
    assert "only supports HTML or plain text" in chunk.content[0].text


def test_web_fetch_limits_response_bytes_and_extracted_chars():
    async def oversized_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain", "content-length": "70000"},
            content=b"x" * 70_000,
            request=request,
        )

    async def long_text_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=("content " * 1000).encode(),
            request=request,
        )

    import asyncio

    oversized = asyncio.run(WebFetch(
        max_response_bytes=64_000,
        transport=httpx.MockTransport(oversized_handler),
        resolver=_allow_public_host,
    )("https://example.com/large"))
    truncated = asyncio.run(WebFetch(
        transport=httpx.MockTransport(long_text_handler),
        resolver=_allow_public_host,
    )("https://example.com/text", max_chars=1000))

    assert oversized.state == ToolResultState.ERROR
    assert "maximum allowed size" in oversized.content[0].text
    assert truncated.state == ToolResultState.SUCCESS
    payload = _payload(truncated)
    assert 990 <= len(payload["content"]) <= 1000
    assert payload["truncated"] is True


def test_pinned_transport_connects_to_validated_ip_and_preserves_host_and_sni():
    """校验与连接共用同一解析结果，同时保留 HTTP Host 与 TLS SNI。"""

    seen: dict[str, object] = {}

    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        seen["resolved"] = (hostname, port)
        return ("93.184.216.34",)

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions["sni_hostname"]
        return httpx.Response(200, request=request)

    import asyncio

    transport = PinnedAsyncHTTPTransport(
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )
    async def send() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://example.com:8443/docs")

    asyncio.run(send())

    assert seen == {
        "resolved": ("example.com", 8443),
        "url": "https://93.184.216.34:8443/docs",
        "host": "example.com:8443",
        "sni": "example.com",
    }
