"""托管 Compose 只公开 HTTPS，并且不缓冲 SSE。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_compose_publishes_only_https_and_disables_sse_buffering():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    example = (ROOT / "deploy" / "backend.env.example").read_text(encoding="utf-8")
    assert '443:443' in compose
    assert "8765:" not in compose
    assert 'network_mode: "service:caddy"' in compose
    assert "flush_interval -1" in caddy
    assert "127.0.0.1:8765" in caddy
    assert "sk-" not in example
    assert "api_key" not in example.lower() or "SMTP" in example
