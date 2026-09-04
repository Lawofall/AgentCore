"""In-process preview registry + HTTP/WS reverse proxy (sandboxd execution plane).

Guest listen addresses are not this process's loopback. Callers register the
guest-bridge ``upstream_ip:upstream_port`` plus the app's advertised port so
Host/Origin can be rewritten as vite (and similar) expect.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from agentcore.core.errors import AuthenticationError
from agentcore.core.logging import get_logger
from agentcore.security.tokens import decode_preview_token

logger = get_logger(__name__)

PREVIEW_COOKIE = "ac_preview"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8787
_CONNECT_TIMEOUT = 15.0
_HEADER_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class PreviewUpstream:
    conversation_id: str
    process_id: str
    upstream_ip: str
    upstream_port: int
    app_port: int


# One slot per conversation + process + advertised app port so two preview
# buttons on the same run do not overwrite each other.
_registry: dict[tuple[str, str, int], PreviewUpstream] = {}


def reset_preview_registry_for_tests() -> None:
    """Drop the in-process preview map (unit tests). Does not touch the HTTP server."""
    _registry.clear()


def register_preview(
    *,
    conversation_id: str,
    process_id: str,
    upstream_ip: str,
    upstream_port: int,
    app_port: int,
) -> None:
    _registry[(conversation_id, process_id, app_port)] = PreviewUpstream(
        conversation_id=conversation_id,
        process_id=process_id,
        upstream_ip=upstream_ip,
        upstream_port=upstream_port,
        app_port=app_port,
    )
    logger.info(
        "sandboxd.preview_registered",
        conversation_id=conversation_id,
        process_id=process_id,
        app_port=app_port,
    )


def unregister_preview(
    conversation_id: str,
    process_id: str,
    app_port: int | None = None,
) -> None:
    if app_port is not None:
        dropped = _registry.pop((conversation_id, process_id, app_port), None) is not None
    else:
        keys = [
            key
            for key in _registry
            if key[0] == conversation_id and key[1] == process_id
        ]
        dropped = False
        for key in keys:
            _registry.pop(key, None)
            dropped = True
    if not dropped:
        return
    logger.info(
        "sandboxd.preview_unregistered",
        conversation_id=conversation_id,
        process_id=process_id,
        app_port=app_port,
    )


def lookup_preview(
    conversation_id: str, process_id: str, app_port: int
) -> PreviewUpstream | None:
    return _registry.get((conversation_id, process_id, app_port))


def _bind_host_port() -> tuple[str, int]:
    try:
        from agentcore.config import settings

        host = getattr(settings, "preview_bind_host", None) or _DEFAULT_HOST
        raw_port = getattr(settings, "preview_bind_port", None)
        port = int(raw_port) if raw_port is not None else _DEFAULT_PORT
        return str(host), port
    except Exception:  # noqa: BLE001 — settings must not break desk RPC listen
        return _DEFAULT_HOST, _DEFAULT_PORT


def _cookie_secure() -> bool:
    try:
        from agentcore.config import settings

        return bool(getattr(settings, "cookie_secure", False))
    except Exception:  # noqa: BLE001 — cookie flags must not break the bounce
        return False


def _http_empty(status: str, extra: list[tuple[str, str]] | None = None) -> bytes:
    lines = [f"HTTP/1.1 {status}"]
    for name, value in extra or []:
        lines.append(f"{name}: {value}")
    lines.extend(["Content-Length: 0", "Connection: close", "", ""])
    return "\r\n".join(lines).encode("latin-1")


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
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


async def _read_headers(reader: asyncio.StreamReader) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        text = line.decode("latin-1").rstrip("\r\n")
        name, sep, value = text.partition(":")
        if not sep:
            continue
        headers.append((name.strip(), value.lstrip()))
    return headers


def _header(headers: list[tuple[str, str]], name: str) -> str | None:
    want = name.lower()
    for key, value in headers:
        if key.lower() == want:
            return value
    return None


def _cookie_value(headers: list[tuple[str, str]], name: str) -> str | None:
    raw = _header(headers, "Cookie")
    if not raw:
        return None
    want = name.lower()
    for part in raw.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key.strip().lower() == want:
            return value.strip()
    return None


def _strip_preview_cookie(value: str) -> str | None:
    kept: list[str] = []
    for part in value.split(";"):
        piece = part.strip()
        if not piece:
            continue
        key, sep, _rest = piece.partition("=")
        if sep and key.strip().lower() == PREVIEW_COOKIE:
            continue
        kept.append(piece)
    if not kept:
        return None
    return "; ".join(kept)


def _is_websocket(headers: list[tuple[str, str]]) -> bool:
    upgrade = _header(headers, "Upgrade")
    return upgrade is not None and "websocket" in upgrade.lower()


def _origin_form(target: str) -> str:
    parts = urlsplit(target)
    if not parts.scheme:
        return target
    path = parts.path or "/"
    if parts.query:
        return f"{path}?{parts.query}"
    return path


def _path_of(target: str) -> str:
    return urlsplit(target if "://" in target or target.startswith("/") else f"/{target}").path


def _enter_token(target: str) -> str | None:
    parts = urlsplit(target if target.startswith("/") or "://" in target else f"/{target}")
    values = parse_qs(parts.query, keep_blank_values=True).get("t") or []
    if not values:
        return None
    token = values[0]
    return token if token else None


def _rewrite_headers(
    headers: list[tuple[str, str]],
    *,
    app_port: int,
    websocket: bool,
) -> list[tuple[str, str]]:
    host_value = f"127.0.0.1:{app_port}"
    origin_value = f"http://127.0.0.1:{app_port}"
    out: list[tuple[str, str]] = []
    seen_host = False
    seen_origin = False
    for name, value in headers:
        lower = name.lower()
        if lower == "host":
            out.append((name, host_value))
            seen_host = True
            continue
        if lower == "origin":
            out.append((name, origin_value))
            seen_origin = True
            continue
        if lower == "cookie":
            stripped = _strip_preview_cookie(value)
            if stripped is not None:
                out.append((name, stripped))
            continue
        out.append((name, value))
    if not seen_host:
        out.append(("Host", host_value))
    if websocket and not seen_origin:
        out.append(("Origin", origin_value))
    return out


def _encode_headers(headers: list[tuple[str, str]]) -> bytes:
    return b"".join(f"{name}: {value}\r\n".encode("latin-1") for name, value in headers)


def _preview_ids_from_token(token: str) -> tuple[str, str, int] | None:
    try:
        claims = decode_preview_token(token)
    except AuthenticationError:
        return None
    cid = claims.conversation_id
    pid = claims.process_id
    port = claims.port
    if not cid or not pid or not isinstance(port, int):
        return None
    return cid, pid, port


async def _handle_enter(writer: asyncio.StreamWriter, target: str) -> None:
    token = _enter_token(target)
    if token is None or _preview_ids_from_token(token) is None:
        writer.write(_http_empty("401 Unauthorized"))
        await writer.drain()
        return
    flags = ["HttpOnly", "Path=/", "SameSite=Lax"]
    if _cookie_secure():
        flags.insert(1, "Secure")
    cookie = f"{PREVIEW_COOKIE}={token}; " + "; ".join(flags)
    writer.write(
        _http_empty(
            "302 Found",
            [("Location", "/"), ("Set-Cookie", cookie)],
        )
    )
    await writer.drain()


def _serialize_request(
    method: str,
    target: str,
    version: str,
    headers: list[tuple[str, str]],
) -> bytes:
    line = f"{method} {_origin_form(target)} {version}\r\n".encode("latin-1")
    return line + _encode_headers(headers) + b"\r\n"


class PreviewHttpServer:
    """Process-wide HTTP/WS reverse proxy for registered preview upstreams."""

    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None
        self._host = ""
        self._port = 0

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return self._port

    @property
    def host(self) -> str:
        return self._host

    async def start(self, host: str, port: int) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle, host, port)
        bound_sockets = self._server.sockets
        bound = int(bound_sockets[0].getsockname()[1]) if bound_sockets else port
        self._host, self._port = host, bound
        logger.info("sandboxd.preview_proxy_started", host=host, port=bound)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()
        self._server = None
        logger.info("sandboxd.preview_proxy_stopped")

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        up_writer: asyncio.StreamWriter | None = None
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=_HEADER_TIMEOUT)
            if not request_line:
                return
            parts = request_line.decode("latin-1").strip().split(" ")
            if len(parts) < 3:
                return
            method, target, version = parts[0].upper(), parts[1], parts[2]
            headers = await _read_headers(reader)
            path = _path_of(target)
            if method == "GET" and path.rstrip("/") == "/enter":
                await _handle_enter(writer, target)
                return
            token = _cookie_value(headers, PREVIEW_COOKIE)
            if not token:
                writer.write(_http_empty("401 Unauthorized"))
                await writer.drain()
                return
            ids = _preview_ids_from_token(token)
            if ids is None:
                writer.write(_http_empty("401 Unauthorized"))
                await writer.drain()
                return
            cid, pid, app_port = ids
            entry = lookup_preview(cid, pid, app_port)
            if entry is None:
                unregister_preview(cid, pid, app_port)
                writer.write(_http_empty("502 Bad Gateway"))
                await writer.drain()
                return
            try:
                up_reader, connected = await asyncio.wait_for(
                    asyncio.open_connection(entry.upstream_ip, entry.upstream_port),
                    timeout=_CONNECT_TIMEOUT,
                )
            except (OSError, TimeoutError):
                unregister_preview(cid, pid, app_port)
                writer.write(_http_empty("502 Bad Gateway"))
                await writer.drain()
                return
            up_writer = connected
            rewritten = _rewrite_headers(
                headers, app_port=entry.app_port, websocket=_is_websocket(headers)
            )
            connected.write(_serialize_request(method, target, version, rewritten))
            await connected.drain()
            await asyncio.gather(
                _pump(reader, connected),
                _pump(up_reader, writer),
                return_exceptions=True,
            )
        except (TimeoutError, OSError, ValueError):
            pass
        finally:
            if up_writer is not None:
                with contextlib.suppress(OSError):
                    up_writer.close()
            with contextlib.suppress(OSError):
                writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


_proxy: PreviewHttpServer | None = None


async def ensure_preview_http() -> PreviewHttpServer:
    """Start (once) the process-wide preview reverse proxy."""
    global _proxy
    if _proxy is None:
        _proxy = PreviewHttpServer()
    if not _proxy.running:
        host, port = _bind_host_port()
        await _proxy.start(host, port)
    return _proxy


async def shutdown_preview_http() -> None:
    global _proxy
    if _proxy is not None:
        await _proxy.stop()
        _proxy = None
