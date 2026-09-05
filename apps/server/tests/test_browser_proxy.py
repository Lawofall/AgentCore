"""D10 SSRF egress proxy: the resolve/refuse decision reuses core/net.py guardrails."""

from __future__ import annotations

import pytest

from agentcore.config import settings
from agentcore.tools.sandbox.browser.proxy import resolve_dial_target


@pytest.mark.asyncio
async def test_public_ip_literal_allowed():
    ip, reason = await resolve_dial_target("8.8.8.8", 443)
    assert ip == "8.8.8.8" and reason == "ok_literal"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "169.254.169.254",  # cloud metadata (link-local)
        "10.0.0.5",  # RFC1918 private
        "192.168.1.1",
        "127.0.0.1",  # loopback
        "0.0.0.0",  # unspecified / blocked hostname
    ],
)
async def test_private_and_metadata_literals_refused(host: str):
    ip, reason = await resolve_dial_target(host, 80)
    assert ip is None
    assert reason in ("PRIVATE_IP", "LOOPBACK_HOST", "BLOCKED_HOST", "BAD_SCHEME")


@pytest.mark.asyncio
async def test_blocked_hostname_refused():
    # ``localhost`` refuses as LOOPBACK_HOST (core.net names the local-machine reason
    # separately); reserved internal names stay BLOCKED_HOST. Both still refuse.
    ip, reason = await resolve_dial_target("localhost", 80)
    assert ip is None and reason == "LOOPBACK_HOST"
    ip2, reason2 = await resolve_dial_target("metadata.google.internal", 80)
    assert ip2 is None and reason2 == "BLOCKED_HOST"


@pytest.mark.asyncio
async def test_hostname_resolving_to_private_is_refused(monkeypatch: pytest.MonkeyPatch):
    async def fake_getaddrinfo(host, port=None):
        return ["10.1.2.3"]  # DNS answers with an internal address

    monkeypatch.setattr("agentcore.core.net._getaddrinfo", fake_getaddrinfo)
    ip, reason = await resolve_dial_target("evil.example.com", 443)
    assert ip is None and reason == "PRIVATE_IP"


@pytest.mark.asyncio
async def test_fake_ip_proxy_range_gated_by_setting(monkeypatch: pytest.MonkeyPatch):
    # 198.18/15 is the Clash/Mihomo fake-IP placeholder — allowed only when the
    # core.net setting is on (dev machines behind such a proxy), refused otherwise.
    monkeypatch.setattr(settings, "web_fetch_allow_fake_ip_proxy", True)
    ip, reason = await resolve_dial_target("198.18.0.7", 443)
    assert ip == "198.18.0.7"

    monkeypatch.setattr(settings, "web_fetch_allow_fake_ip_proxy", False)
    ip2, reason2 = await resolve_dial_target("198.18.0.7", 443)
    # core.net flags the Clash fake-IP range with a dedicated refusal reason.
    assert ip2 is None and reason2 in ("PRIVATE_IP", "PRIVATE_IP_FAKE_PROXY")
