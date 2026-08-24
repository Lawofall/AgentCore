"""Shared outbound-HTTP infrastructure: timeouts, error description, SSRF guard.

These primitives are pure, stateless infra (no business-module dependencies), so
they live in ``core`` and are consumed by *both* the web tools
(``tools/builtin/web``) and HTTP routes that fetch the open internet (e.g. the
favicon proxy). Keeping them here avoids an ``api -> tools`` import just to reuse
a security guard, and guarantees the favicon proxy and ``read_url`` apply the
*same* SSRF policy (one definition, no drift).

What lives here (stateless):
- :func:`web_timeout` — an ``httpx.Timeout`` with a short connect deadline
  (blocked hosts fail fast) and a longer read window (slow sites still succeed).
- :func:`outbound_async_client` — product egress ``httpx.AsyncClient`` with
  ``trust_env=False`` (do not inherit system SOCKS / HTTP proxy env; avoids
  missing-``socksio``「调用失败」on Clash/V2Ray machines).
- :func:`abort_httpx_response` — tear down an in-flight stream on cancel
  (shielded ``aclose``; does not drain remaining SSE).
- :func:`describe_net_error` — turn opaque httpx errors into an honest,
  model-facing reason (so logs show the real cause, not ``error: ""``).
- :func:`site_of` — display hostname for source/citation cards.
- :func:`classify_url` / :func:`is_safe_url` — the SSRF guard: reject
  non-http(s), reserved hostnames, and any host that resolves to a
  private/loopback/link-local/reserved address (blocks cloud-metadata SSRF).

The *stateful* per-host egress circuit breaker lives in
``tools/builtin/web/_net`` (agent-runtime egress state, not generic infra).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
import ssl
from enum import Enum
from urllib.parse import urlparse

import httpx

# Overall search read budget. Kept >= SearXNG's own max_request_timeout (15s, see
# deploy/searxng/settings.yml): a search fans out to several China engines and can
# legitimately take >10s under load, so a tighter client deadline would abandon
# results SearXNG would still return. Connect still uses the short
# WEB_CONNECT_TIMEOUT, so a genuinely down host fast-fails into the breaker.
SEARCH_TIMEOUT = 16.0
WEB_CONNECT_TIMEOUT = 5.0  # connect deadline: blocked hosts fail fast
WEB_READ_TIMEOUT = 15.0  # read window for slow-but-reachable sites


class EgressError(Exception):
    """Raised when the per-host breaker short-circuits an outbound request.

    Its ``str()`` is the honest, model-facing reason, so callers can surface it
    directly without re-wrapping.
    """


class PinnedAddressError(httpx.ConnectError):
    """Raised by :class:`PinnedIPTransport` when a host resolves — *at connect time* —
    to a blocked address (or cannot be resolved), closing the DNS-rebinding TOCTOU.

    Subclasses ``httpx.ConnectError`` so existing ``except httpx.NetworkError`` paths
    (e.g. the egress breaker in ``read_url._safe_request``) catch it unchanged; its
    ``str()`` is the honest, model-facing reason (see :func:`describe_net_error`).

    ``block`` keeps the :class:`URLBlock` that caused the refusal, so a caller can map a
    connect-time block to the same stable failure code as the pre-flight one instead of
    re-parsing the message.
    """

    def __init__(
        self,
        message: str,
        *,
        request: httpx.Request | None = None,
        block: URLBlock | None = None,
    ) -> None:
        super().__init__(message, request=request)
        self.block = block


def web_timeout(read: float = WEB_READ_TIMEOUT) -> httpx.Timeout:
    """Timeout with a short connect deadline and a configurable read window."""
    return httpx.Timeout(read, connect=WEB_CONNECT_TIMEOUT)


async def abort_httpx_response(response: httpx.Response | None) -> None:
    """Abort an in-flight httpx response so the upstream connection is torn down.

    Shielded: a cancelling task must still finish the close (cleanup, not new
    work). ``aclose`` releases the stream; it does not drain remaining SSE.
    """
    if response is None:
        return
    with contextlib.suppress(BaseException):
        await asyncio.shield(response.aclose())


def outbound_async_client(**kwargs: object) -> httpx.AsyncClient:
    """Product outbound ``httpx.AsyncClient`` that ignores process proxy env.

    Default httpx ``trust_env=True`` reads ``HTTP(S)_PROXY`` / ``ALL_PROXY``. On
    maintainer and end-user machines those often point at a local Clash/V2Ray
    **SOCKS** port; without optional ``socksio`` (``httpx[socks]``) every call
    fails with a raw install hint shown as「调用失败」. Industry default for
    first-party / LLM / tool egress: do not silently inherit system SOCKS.
    Explicit ``proxy=`` / mounts still work when passed by the caller.
    """
    kwargs.setdefault("trust_env", False)
    return httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]


def site_of(url: str) -> str:
    """Display hostname for a URL: lowercased, sans a leading ``www.``.

    Used to label source/citation cards. Returns ``""`` when the URL has no
    parseable host (the card then falls back to the title/url).
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_loopback_host(host: str) -> bool:
    """True for ``localhost`` / ``127.0.0.0/8`` / ``::1`` (SearXNG default bind).

    Used only to pick honest connect-error copy — does **not** relax SSRF.
    """
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    if h in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def is_local_machine_host(host: str) -> bool:
    """True when ``host`` names the *caller's own machine* as a request target.

    :func:`is_loopback_host` (``localhost`` / ``127.0.0.0/8`` / ``::1``) plus the
    unspecified addresses ``0.0.0.0`` / ``::``, which a user typing a bind address means
    the same way. One predicate so a refusal keeps one reason instead of splitting the
    same intent across ``BLOCKED_HOST`` (hostnames) and ``PRIVATE_IP`` (literals).

    Classification only — the reject set (:func:`ip_is_safe`) is unchanged.
    """
    if is_loopback_host(host):
        return True
    try:
        return ipaddress.ip_address((host or "").strip().lower().rstrip(".")).is_unspecified
    except ValueError:
        return False


def _error_target_host(e: BaseException, url: str | None) -> str | None:
    """Hostname for error copy: explicit ``url``, else ``e.request.url`` when present."""
    if url:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            host = ""
        return host or None
    # httpx.RequestError.request raises RuntimeError when unset — prefer the
    # private slot, then a guarded property read for non-httpx exceptions.
    req = getattr(e, "_request", None)
    if req is None:
        try:
            req = getattr(e, "request", None)
        except RuntimeError:
            req = None
    if req is None:
        return None
    host = getattr(getattr(req, "url", None), "host", None)
    return str(host).lower() if host else None


# Connect failures to loopback / configured SearXNG — not「出网受限」(that copy is for
# public egress). User/model-facing: no docker compose teaching; boot logs keep ops hints.
_LOCAL_SEARCH_CONNECT = "本地搜索服务不可用，请稍后重试"
_LOCAL_SEARCH_CONNECT_TIMEOUT = "本地搜索服务暂时不可用，请稍后重试"


def _is_ssl_error(e: BaseException) -> bool:
    """True when an exception (or its cause chain) is a TLS/cert-verification failure.

    httpx wraps the handshake's ``ssl.SSLError`` in an ``httpx.ConnectError`` whose own
    ``str()`` is often empty, so the generic「无法建立连接」branch would mislabel a broken
    cert chain as「出网受限」— a real, high-frequency case for China gov/court sites (see
    实测案例复盘.md 案例 1). Walk the ``__cause__``/``__context__`` chain for an
    ``ssl.SSLError`` (with a string fallback for the verify-failed marker).
    """
    seen: set[int] = set()
    cur: BaseException | None = e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ssl.SSLError):
            return True
        cur = cur.__cause__ or cur.__context__
    return "CERTIFICATE_VERIFY_FAILED" in str(e)


def describe_net_error(
    e: BaseException,
    *,
    url: str | None = None,
    local_service: bool = False,
) -> str:
    """Readable reason for a failed outbound request.

    httpx timeout/connect errors frequently stringify to ``""``; surface the
    failure type plus a plain-language hint so both the log and the model see a
    real cause instead of an empty string.

    ``url`` / ``e.request`` / ``local_service``: connect-class failures aimed at
    loopback (or an explicitly local service such as configured SearXNG) get
    「本地搜索服务不可用」instead of the public「出网受限」copy. Does not change
    SSRF policy — classification only.
    """
    if isinstance(e, EgressError):
        return str(e)
    # A pinned-IP block already carries the honest URLBlock reason. Must precede the
    # generic ConnectError/NetworkError branch below (it subclasses ConnectError).
    if isinstance(e, PinnedAddressError):
        return str(e)
    if _is_ssl_error(e):
        return (
            "SSL 证书校验失败（站点证书链不被信任，常见于国内部分政务/法院站点）；"
            "改用已有摘要或换其它来源，勿对同一站点反复重试"
        )
    host = _error_target_host(e, url)
    treat_as_local = local_service or (host is not None and is_loopback_host(host))
    if isinstance(e, httpx.ConnectTimeout):
        if treat_as_local:
            return _LOCAL_SEARCH_CONNECT_TIMEOUT
        return "连接超时（无法连上该站点，可能出网受限或站点不可达）"
    if isinstance(e, httpx.ReadTimeout):
        return "读取超时（站点响应过慢）"
    if isinstance(e, (httpx.ConnectError, httpx.NetworkError)):
        detail = str(e).strip()
        if treat_as_local:
            return _LOCAL_SEARCH_CONNECT + (f": {detail}" if detail else "")
        return "无法建立连接（出网受限或站点不可达）" + (f": {detail}" if detail else "")
    if isinstance(e, httpx.TimeoutException):
        return "请求超时"
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    detail = str(e).strip()
    return f"{type(e).__name__}" + (f": {detail}" if detail else "")


# --- SSRF guard -------------------------------------------------------------
# One definition, shared by read_url (the tool) and the favicon proxy (a route),
# so both apply identical private-network protection with no drift.

# ``localhost`` / ``0.0.0.0`` are NOT here: they are the same refusal as a 127.x / ::1
# literal (:data:`URLBlock.LOOPBACK_HOST`), and splitting one intent across two reasons
# is what produced two different sentences for one situation.
_BLOCKED_HOSTNAMES = {"metadata.google.internal"}
# Clash/Mihomo 默认 fake-ip-range = 198.18.0.1/16；198.18.0.0/15 为 RFC 2544 保留段。
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")


class URLBlock(Enum):
    """URL 被拒原因；value 为面向模型的诚实错误信息。

    把「DNS 解析失败/网络不可达」与「真·SSRF 拦截」区分开 —— 旧实现把两者都
    报成「私有/内网」，既误导模型反复重试，也掩盖了环境层面的网络问题。
    """

    BAD_SCHEME = "[ERROR] 仅支持 http/https 链接"
    BLOCKED_HOST = "[ERROR] 该主机名禁止访问（本地/内网保留域名）"
    LOOPBACK_HOST = (
        "[ERROR] 该地址指向本机回环 / 本地监听地址（localhost、127.x、::1、0.0.0.0），"
        "服务端进程访问不到用户本机上的服务，已拒绝"
    )
    DNS_FAIL = (
        "[ERROR] 无法解析该域名（DNS 解析失败或网络不可达）。"
        "请确认链接拼写正确且可公网访问；若反复出现，可能是当前环境出网受限。"
    )
    PRIVATE_IP = "[ERROR] 链接解析到私有/保留地址，已按 SSRF 防护拦截"
    PRIVATE_IP_FAKE_PROXY = (
        "[ERROR] 链接解析到私有/保留地址，已按 SSRF 防护拦截。"
        "疑似本机代理 fake-IP 模式（DNS 将域名应答为 198.18.x.x 占位 IP，Clash/Mihomo 默认行为）；"
        "SSRF 拦截正确、非链接问题。处置：代理 DNS 改 redir-host 或关 fake-ip，"
        "再跑 apps/server 下 `uv run python scripts/archive/probe_egress.py` 复验。"
    )


# Every refusal that means「目标不是公网可达地址」. Callers gate on this set to decide
# whether to refuse (``workspace.git`` clone guard), so LOOPBACK_HOST belongs here:
# naming the loopback reason separately must not narrow anyone's reject set.
PRIVATE_IP_BLOCKS = frozenset(
    {URLBlock.PRIVATE_IP, URLBlock.PRIVATE_IP_FAKE_PROXY, URLBlock.LOOPBACK_HOST}
)


def is_fake_ip_proxy_signature(ip: str) -> bool:
    """True when ``ip`` is in the Clash/Mihomo fake-IP placeholder range (198.18/15)."""
    try:
        return ipaddress.ip_address(ip) in _FAKE_IP_NET
    except ValueError:
        return False


def private_ip_block(*ips: str) -> URLBlock:
    """Pick the address-level refusal: fake-IP proxy > loopback > generic private.

    Loopback only when *every* blocked address is the local machine: an answer that
    mixes a public IP with a loopback one is a rebinding attempt, and must keep reading
    as an SSRF block rather than「你本机的服务」.
    """
    if any(is_fake_ip_proxy_signature(ip) for ip in ips):
        return URLBlock.PRIVATE_IP_FAKE_PROXY
    if ips and all(is_local_machine_host(ip) for ip in ips):
        return URLBlock.LOOPBACK_HOST
    return URLBlock.PRIVATE_IP


def _fake_ip_proxy_allowed() -> bool:
    """Whether Clash/Mihomo fake-IP placeholders (198.18/15) may be dialed."""
    from agentcore.config import settings

    return bool(settings.read_url_allow_fake_ip_proxy)


def ip_is_safe(ip: str) -> bool:
    """True only for globally-routable addresses (blocks private/metadata).

    When ``read_url_allow_fake_ip_proxy`` is on, 198.18.0.0/15 placeholders from
    fake-IP DNS are treated as connectable — the local proxy routes them to the
    real destination. True private/loopback/link-local addresses stay blocked.
    """
    if is_fake_ip_proxy_signature(ip) and _fake_ip_proxy_allowed():
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local  # 含云元数据 169.254.169.254
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


async def _getaddrinfo(host: str, port: int | None = None) -> list[str]:
    """Resolve ``host`` to its IP strings (TCP), de-duplicated, order-preserving.

    The single DNS chokepoint so the pre-flight guard (:func:`classify_url`) and the
    connect-time pinning (:class:`PinnedIPTransport`) resolve through identical logic
    — and so a test can monkeypatch one place to simulate DNS (including a rebind).
    """
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, proto=socket.IPPROTO_TCP
    )
    # dict (not set) keeps resolution order so the transport pins deterministically.
    return list(dict.fromkeys(info[4][0] for info in infos))


async def classify_url(url: str) -> URLBlock | None:
    """SSRF 检查并区分拒绝原因；返回 None 表示可安全请求。

    域名经 DNS 解析后，只要任一解析地址落在私网/回环/链路本地/保留段即拒绝
    （封堵「域名指向内网/169.254.169.254」这类绕过）。
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return URLBlock.BAD_SCHEME
        hostname = (parsed.hostname or "").strip().rstrip(".").lower()
        if not hostname:
            return URLBlock.BAD_SCHEME
        if is_local_machine_host(hostname):
            return URLBlock.LOOPBACK_HOST
        if hostname in _BLOCKED_HOSTNAMES:
            return URLBlock.BLOCKED_HOST
        if hostname.endswith(".local") or hostname.endswith(".internal"):
            return URLBlock.BLOCKED_HOST

        try:
            ipaddress.ip_address(hostname)
            return None if ip_is_safe(hostname) else private_ip_block(hostname)
        except ValueError:
            pass  # 不是字面 IP，下面走 DNS 解析

        try:
            addrs = await _getaddrinfo(hostname)
        except OSError:
            return URLBlock.DNS_FAIL
        if not addrs:
            return URLBlock.DNS_FAIL
        if not all(ip_is_safe(a) for a in addrs):
            return private_ip_block(*addrs)
        return None
    except Exception:
        return URLBlock.DNS_FAIL


async def is_safe_url(url: str) -> bool:
    """Bool 包装：用于重定向逐跳重校验。"""
    return await classify_url(url) is None


# --- SEC-007: pinned-IP transport (DNS-rebinding TOCTOU close) ---------------
# classify_url (pre-flight) and httpx's own connect-time resolution are two
# separate DNS lookups; a hostile resolver can answer "public" to the first and
# "127.0.0.1 / 169.254.169.254" to the second. The transport below makes the
# *validated* address and the *connected* address the same one.


def _literal_ip(host: str) -> str | None:
    """Return ``host`` if it is already an IP literal (no DNS step to race), else None."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        return None


def _authority(url: httpx.URL) -> str:
    """``host[:port]`` for the ``Host`` header (omits a default port, brackets IPv6)."""
    host = url.host
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    return f"{host}:{url.port}" if url.port is not None else host


async def _resolve_pinned_ip(host: str, port: int, *, request: httpx.Request) -> str:
    """Resolve ``host`` and return one validated IP to pin the connection to.

    Mirrors :func:`classify_url`'s policy — every resolved address must be globally
    routable, else the whole request is refused (a name that mixes a public and a
    private answer is treated as hostile). Pins to the first resolved address.
    """
    try:
        addrs = await _getaddrinfo(host, port)
    except OSError as e:
        raise PinnedAddressError(
            URLBlock.DNS_FAIL.value, request=request, block=URLBlock.DNS_FAIL
        ) from e
    if not addrs:
        raise PinnedAddressError(URLBlock.DNS_FAIL.value, request=request, block=URLBlock.DNS_FAIL)
    if not all(ip_is_safe(a) for a in addrs):
        block = private_ip_block(*addrs)
        raise PinnedAddressError(block.value, request=request, block=block)
    return addrs[0]


class PinnedIPTransport(httpx.AsyncBaseTransport):
    """SSRF-hardening transport that closes the DNS-rebinding TOCTOU.

    It resolves the host once, refuses the request unless *every* resolved address
    is globally routable, then rewrites the connection target to a pinned IP literal
    so httpcore connects to exactly that IP (no second resolution). The original
    hostname is preserved for the ``Host`` header and — over TLS — the SNI / cert
    hostname, so vhost routing and certificate verification are unaffected.

    Scope: wrap clients that fetch *model/user-supplied* URLs (``read_url``, the
    favicon proxy). Intentionally NOT used for fixed, trusted infrastructure (e.g.
    the self-hosted SearXNG), which may legitimately resolve to a private IP.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        *,
        verify: bool = True,
    ) -> None:
        # Own the inner transport's lifecycle: ``aclose`` tears its pool down so a
        # client built with ``transport=PinnedIPTransport(...)`` still cleans up.
        self._inner = inner if inner is not None else httpx.AsyncHTTPTransport(verify=verify)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if not host:
            raise PinnedAddressError(
                URLBlock.DNS_FAIL.value, request=request, block=URLBlock.DNS_FAIL
            )

        literal = _literal_ip(host)
        if literal is not None:
            # No DNS step to race; just enforce the same private/reserved policy.
            if not ip_is_safe(literal):
                block = private_ip_block(literal)
                raise PinnedAddressError(block.value, request=request, block=block)
            return await self._inner.handle_async_request(request)

        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        ip = await _resolve_pinned_ip(host, port, request=request)

        # Capture the authority BEFORE rewriting so the Host header keeps the real
        # hostname (httpx usually pre-set it; fall back to building it defensively).
        authority = request.headers.get("host") or _authority(request.url)
        if request.url.scheme == "https":
            # Preserve TLS SNI + cert-hostname verification against the real host
            # even though we dial an IP literal (httpcore honors this extension).
            request.extensions["sni_hostname"] = host
        request.url = request.url.copy_with(host=ip)
        request.headers["host"] = authority
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


__all__ = [
    "abort_httpx_response",
    "SEARCH_TIMEOUT",
    "WEB_CONNECT_TIMEOUT",
    "WEB_READ_TIMEOUT",
    "EgressError",
    "PinnedAddressError",
    "PinnedIPTransport",
    "PRIVATE_IP_BLOCKS",
    "URLBlock",
    "classify_url",
    "describe_net_error",
    "ip_is_safe",
    "is_fake_ip_proxy_signature",
    "is_local_machine_host",
    "is_loopback_host",
    "is_safe_url",
    "private_ip_block",
    "site_of",
    "web_timeout",
]
