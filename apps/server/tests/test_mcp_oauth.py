"""MCP OAuth helpers: PKCE and return-origin allowlist."""

from __future__ import annotations

import base64
import hashlib

from agentcore.mcp.oauth import pkce_pair
from agentcore.mcp.servers import safe_return_origin


def test_pkce_pair_s256():
    verifier, challenge = pkce_pair()
    assert len(verifier) >= 43
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_safe_return_origin_allowlist(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(
        settings,
        "cors_allow_origins",
        "https://app.example,http://localhost:5173",
    )
    monkeypatch.setattr(settings, "debug", False)
    fallback = "https://api.example"
    assert (
        safe_return_origin("https://app.example", fallback=fallback)
        == "https://app.example"
    )
    assert (
        safe_return_origin("https://app.example/path", fallback=fallback)
        == "https://app.example"
    )
    assert (
        safe_return_origin("https://evil.example", fallback=fallback) == fallback
    )
    assert safe_return_origin("javascript:alert(1)", fallback=fallback) == fallback
    assert safe_return_origin("", fallback=fallback) == fallback


def test_safe_return_origin_debug_loopback(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "cors_allow_origins", "https://app.example")
    monkeypatch.setattr(settings, "debug", True)
    assert (
        safe_return_origin(
            "http://127.0.0.1:4173", fallback="https://api.example"
        )
        == "http://127.0.0.1:4173"
    )
