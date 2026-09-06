"""Host-side SSRF filtering proxy — the D10 egress chokepoint (production).

Chromium in each browser sandbox is launched with ``--proxy-server`` pointing at
this proxy, and the sandbox netns has NO other route out, so every request is
DNS-resolved and vetted HERE before any packet leaves the host. It refuses
private / loopback / link-local (incl. 169.254.169.254 cloud metadata) targets —
a sandbox-internal raw socket cannot bypass it because there is nothing else to
route to (the network-layer enforcement D10 requires; the app-level guard in
``core/net.py`` cannot see raw sockets inside runsc).

Reuses ``core.net`` guardrails as the single source of truth: ``classify_url``
(scheme / blocked-hostname / DNS / all-resolved-IPs-safe / Clash fake-IP) for the
decision, ``ip_is_safe`` to pin the dial IP. The decision function is pure-async
and unit-testable (monkeypatch DNS) independent of the socket plumbing.

Process-wide singleton (one proxy serves every session's veth); bound to the
per-session veth host ends only (container-internal, never published externally).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from urllib.parse import urlsplit

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.net import resolve_ssrf_dial_target

logger = get_logger(__name__)

_CONNECTION_ESTABLISHED = b"HTTP/1.1 200 Connection Established\r\n\r\n"


def _refusal(status: str) -> bytes:
    """A bodyless reply that closes cleanly (no keep-alive hang on the client)."""
    return f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()


async def resolve_dial_target(
    host: str, port: int, *, scheme: str = "https"
) -> tuple[str | None, str]:
    """SSRF-vet ``host``; policy lives in :func:`core.net.resolve_ssrf_dial_target`."""
    return await resolve_ssrf_dial_target(host, port, scheme=scheme)


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """One-way byte relay until EOF; closes the writer side when done."""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(OSError):
            writer.write_eof()


class BrowserFilterProxy:
    """Process-wide async CONNECT/HTTP proxy enforcing the SSRF egress policy."""

    def __init__(
        self,
        *,
        on_decision: Callable[[str, str, bool, str], None] | None = None,
    ) -> None:
        # on_decision(method, host, allowed, reason) — observability / test hook.
        self._on_decision = on_decision
        self._server: asyncio.AbstractServer | None = None
        self._host = ""
        self._port = 0

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return self._port

    async def start(self, host: str, port: int) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle, host, port)
        self._host, self._port = host, port
        logger.info("browser.proxy_started", host=host, port=port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()
        self._server = None
        logger.info("browser.proxy_stopped")

    def _decide(self, method: str, host: str, allowed: bool, reason: str) -> None:
        logger.info(
            "browser.proxy_decision", method=method, host=host, allowed=allowed, reason=reason
        )
        if self._on_decision is not None:
            self._on_decision(method, host, allowed, reason)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not request_line:
                return
            parts = request_line.decode("latin-1").strip().split(" ")
            if len(parts) < 3:
                return
            method, target = parts[0].upper(), parts[1]
            if method == "CONNECT":
                await self._connect(reader, writer, target)
            else:
                await self._forward(reader, writer, method, target, parts[2])
        except (TimeoutError, OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                writer.close()

    @staticmethod
    async def _drain_headers(reader: asyncio.StreamReader) -> list[bytes]:
        headers: list[bytes] = []
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            headers.append(line)
        return headers

    async def _connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target: str
    ) -> None:
        host, _, port_s = target.rpartition(":")
        port = int(port_s or "443")
        await self._drain_headers(reader)
        ip, reason = await resolve_dial_target(host, port)
        if ip is None:
            self._decide("CONNECT", host, False, reason)
            writer.write(_refusal("403 Forbidden"))
            await writer.drain()
            return
        self._decide("CONNECT", host, True, reason)
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=15
            )
        except (OSError, TimeoutError):
            writer.write(_refusal("502 Bad Gateway"))
            await writer.drain()
            return
        writer.write(_CONNECTION_ESTABLISHED)
        await writer.drain()
        await asyncio.gather(
            _pump(reader, up_writer), _pump(up_reader, writer), return_exceptions=True
        )
        with contextlib.suppress(OSError):
            up_writer.close()

    async def _forward(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        url: str,
        version: str,
    ) -> None:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or 80
        ip, reason = await resolve_dial_target(host, port, scheme="http")
        headers = await self._drain_headers(reader)
        if ip is None:
            self._decide(method, host, False, reason)
            writer.write(_refusal("403 Forbidden"))
            await writer.drain()
            return
        self._decide(method, host, True, reason)
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=15
            )
        except (OSError, TimeoutError):
            writer.write(_refusal("502 Bad Gateway"))
            await writer.drain()
            return
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        req = f"{method} {path} {version}\r\n".encode("latin-1") + b"".join(headers) + b"\r\n"
        up_writer.write(req)
        await up_writer.drain()
        await asyncio.gather(
            _pump(reader, up_writer), _pump(up_reader, writer), return_exceptions=True
        )
        with contextlib.suppress(OSError):
            up_writer.close()


_proxy: BrowserFilterProxy | None = None


async def ensure_browser_proxy() -> BrowserFilterProxy:
    """Start (once) and return the process-wide browser egress proxy.

    Bound to ``0.0.0.0`` on the configured port so every session's veth host end
    (10.x.n.1) reaches it; the port is container-internal (never published), which
    is the "仅本机监听" posture in a cloud deployment.
    """
    global _proxy
    if _proxy is None:
        _proxy = BrowserFilterProxy()
    if not _proxy.running:
        await _proxy.start("0.0.0.0", int(settings.browser_proxy_port))
    return _proxy


async def shutdown_browser_proxy() -> None:
    global _proxy
    if _proxy is not None:
        await _proxy.stop()
        _proxy = None
