"""SEC-007 PoC + regression: the pinned-IP transport closes the DNS-rebinding TOCTOU.

The SSRF guard (:func:`classify_url`) runs a *pre-flight* DNS lookup, but httpx then
runs its *own* lookup when it actually connects. A hostile resolver can answer
"public IP" to the first and "127.0.0.1 / 169.254.169.254" to the second (classic
DNS rebinding), slipping past a guard that only validated the first answer.

:class:`PinnedIPTransport` makes the *validated* address and the *connected* address
the same one: it resolves once, refuses unless every resolved address is globally
routable, then rewrites the connection target to that pinned IP literal — while
preserving the original hostname for the ``Host`` header and TLS SNI / cert check.

These tests are hermetic: ``_getaddrinfo`` is monkeypatched (no real DNS) and a
recording inner transport stands in for the socket (no real connection), so the
suite proves the transport's logic — including a simulated rebind — without network.
"""

from __future__ import annotations

import httpx
import pytest

from agentcore.core import net
from agentcore.core.net import PinnedAddressError, PinnedIPTransport, URLBlock


class _RecordingInner(httpx.AsyncBaseTransport):
    """Inner transport stub: records the request it *would* dial, returns 200.

    Standing in for ``httpx.AsyncHTTPTransport`` lets the tests assert exactly what
    the pinned transport handed downstream (rewritten URL, Host header, SNI) without
    opening a socket — and assert it is *never* reached when a request is blocked.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, text="ok", request=request)

    async def aclose(self) -> None:  # pragma: no cover - nothing to release
        return None


def _fake_dns(monkeypatch, mapping: dict[str, list[str]]) -> None:
    """Route ``_getaddrinfo`` through a fixed host->IPs table (OSError when absent)."""

    async def _resolver(host: str, port: int | None = None) -> list[str]:
        try:
            return list(mapping[host])
        except KeyError as e:
            raise OSError(f"no fake DNS entry for {host!r}") from e

    monkeypatch.setattr(net, "_getaddrinfo", _resolver)


def _client(inner: _RecordingInner) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=PinnedIPTransport(inner))


# --- happy path: pin to the validated IP, keep Host + SNI on the real hostname ---


async def test_pins_to_validated_public_ip_and_preserves_host_and_sni(monkeypatch):
    _fake_dns(monkeypatch, {"example.com": ["93.184.216.34"]})
    inner = _RecordingInner()
    async with _client(inner) as client:
        resp = await client.get("https://example.com/path?q=1")

    assert resp.status_code == 200
    sent = inner.requests[-1]
    # Connection target rewritten to the validated IP literal …
    assert sent.url.host == "93.184.216.34"
    assert sent.url.path == "/path"
    assert sent.url.query == b"q=1"
    # … but Host header + TLS SNI still carry the real hostname (vhost + cert OK).
    assert sent.headers["host"] == "example.com"
    assert sent.extensions.get("sni_hostname") == "example.com"


async def test_pins_first_resolved_ip_when_all_safe(monkeypatch):
    # Multiple public answers: pin deterministically to the first (resolution order).
    _fake_dns(monkeypatch, {"multi.example": ["1.1.1.1", "8.8.8.8"]})
    inner = _RecordingInner()
    async with _client(inner) as client:
        await client.get("https://multi.example/")
    assert inner.requests[-1].url.host == "1.1.1.1"


async def test_http_scheme_pins_without_sni(monkeypatch):
    # Plain HTTP has no TLS handshake → no SNI extension, but Host is still preserved.
    _fake_dns(monkeypatch, {"plain.example": ["93.184.216.34"]})
    inner = _RecordingInner()
    async with _client(inner) as client:
        await client.get("http://plain.example/x")
    sent = inner.requests[-1]
    assert sent.url.host == "93.184.216.34"
    assert sent.headers["host"] == "plain.example"
    assert "sni_hostname" not in sent.extensions


async def test_pins_to_validated_public_ipv6(monkeypatch):
    # IPv6 pinning (a flagged risk): the rewritten target must bracket the literal,
    # while Host + SNI keep the hostname. ip_is_safe already vets the address family.
    _fake_dns(monkeypatch, {"v6.example": ["2001:4860:4860::8888"]})
    inner = _RecordingInner()
    async with _client(inner) as client:
        await client.get("https://v6.example/p")
    sent = inner.requests[-1]
    assert ":" in sent.url.host  # an IPv6 literal was pinned
    assert str(sent.url).startswith("https://[2001:4860:4860::8888]/p")
    assert sent.headers["host"] == "v6.example"
    assert sent.extensions.get("sni_hostname") == "v6.example"


async def test_preserves_nondefault_port_in_host_header(monkeypatch):
    # An explicit non-default port must ride the Host header after the IP rewrite.
    _fake_dns(monkeypatch, {"example.com": ["93.184.216.34"]})
    inner = _RecordingInner()
    async with _client(inner) as client:
        await client.get("https://example.com:8443/")
    sent = inner.requests[-1]
    assert sent.url.host == "93.184.216.34"
    assert sent.url.port == 8443
    assert sent.headers["host"] == "example.com:8443"


# --- the money shot: a rebind that passes pre-flight is still blocked at connect ---


async def test_toctou_preflight_passes_but_connect_pins_and_blocks(monkeypatch):
    """The whole point of SEC-007. The pre-flight classify_url sees a public answer
    and allows the URL; the transport resolves *again* at connect time, sees the
    rebound loopback, and refuses — because the validated IP is the one it dials."""
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])

    async def _rebind(host: str, port: int | None = None) -> list[str]:
        return list(next(answers))

    monkeypatch.setattr(net, "_getaddrinfo", _rebind)

    # 1) pre-flight guard sees the public answer → allows it.
    assert await net.classify_url("https://rebind.evil/") is None
    # 2) transport resolves again at connect → rebound to loopback → blocked.
    inner = _RecordingInner()
    async with _client(inner) as client:
        with pytest.raises(PinnedAddressError):
            await client.get("https://rebind.evil/")
    assert inner.requests == []  # the connection was never attempted


# --- blocking: rebinding / literal private targets never reach the socket ---


@pytest.mark.parametrize(
    "rebound_ip",
    ["127.0.0.1", "169.254.169.254", "10.0.0.5", "192.168.1.1", "::1"],
)
async def test_blocks_rebinding_to_internal_address(monkeypatch, rebound_ip):
    _fake_dns(monkeypatch, {"rebind.evil": [rebound_ip]})
    inner = _RecordingInner()
    async with _client(inner) as client:
        with pytest.raises(PinnedAddressError):
            await client.get("https://rebind.evil/")
    assert inner.requests == []


async def test_blocks_when_any_resolved_ip_is_private(monkeypatch):
    # Mixed public+private answer is treated as hostile (matches classify_url policy).
    _fake_dns(monkeypatch, {"mixed.evil": ["93.184.216.34", "10.0.0.5"]})
    inner = _RecordingInner()
    async with _client(inner) as client:
        with pytest.raises(PinnedAddressError):
            await client.get("https://mixed.evil/")
    assert inner.requests == []


async def test_blocks_literal_private_ip_host(monkeypatch):
    # A literal private IP needs no DNS, but the same policy still applies.
    called = {"dns": False}

    async def _should_not_resolve(host: str, port: int | None = None) -> list[str]:
        called["dns"] = True
        return []

    monkeypatch.setattr(net, "_getaddrinfo", _should_not_resolve)
    inner = _RecordingInner()
    async with _client(inner) as client:
        with pytest.raises(PinnedAddressError):
            await client.get("http://127.0.0.1:8000/")
    assert inner.requests == []
    assert called["dns"] is False  # literal IPs skip the resolver entirely


async def test_allows_literal_public_ip_without_rewrite(monkeypatch):
    inner = _RecordingInner()
    async with _client(inner) as client:
        resp = await client.get("https://1.1.1.1/")
    assert resp.status_code == 200
    assert inner.requests[-1].url.host == "1.1.1.1"


async def test_dns_failure_surfaces_as_pinned_dns_error(monkeypatch):
    async def _boom(host: str, port: int | None = None) -> list[str]:
        raise OSError("name resolution failed")

    monkeypatch.setattr(net, "_getaddrinfo", _boom)
    inner = _RecordingInner()
    async with _client(inner) as client:
        with pytest.raises(PinnedAddressError) as ei:
            await client.get("https://nope.example/")
    assert net.describe_net_error(ei.value) == URLBlock.DNS_FAIL.value
    assert inner.requests == []


# --- error classification & breaker compatibility ---


def test_describe_net_error_surfaces_pinned_reason():
    assert net.describe_net_error(PinnedAddressError(URLBlock.PRIVATE_IP.value)) == (
        URLBlock.PRIVATE_IP.value
    )


def test_pinned_error_is_a_network_error_for_the_egress_breaker():
    # web_fetch._safe_request counts httpx.NetworkError toward the per-host breaker;
    # PinnedAddressError must remain in that hierarchy so a rebind attempt is counted.
    err = PinnedAddressError(URLBlock.PRIVATE_IP.value)
    assert isinstance(err, httpx.ConnectError)
    assert isinstance(err, httpx.NetworkError)
