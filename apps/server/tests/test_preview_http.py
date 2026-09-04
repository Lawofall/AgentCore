"""sandboxd preview registry + HTTP/WS reverse proxy (no runsc)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest

from agentcore.security.tokens import create_access_token, create_preview_token
from agentcore.tools.sandbox.sandboxd.preview_http import (
    PREVIEW_COOKIE,
    PreviewHttpServer,
    lookup_preview,
    register_preview,
    reset_preview_registry_for_tests,
)
from agentcore.tools.sandbox.sandboxd.server import RpcDeniedError, SandboxdServer

_CID = "conv-preview-1"
_PID = "proc-preview-1"
_APP_PORT = 5173


def _token(**overrides: object) -> str:
    kwargs: dict = {
        "conversation_id": _CID,
        "process_id": _PID,
        "port": _APP_PORT,
        **overrides,
    }
    return create_preview_token("user-1", **kwargs)


@asynccontextmanager
async def _proxy() -> AsyncIterator[PreviewHttpServer]:
    reset_preview_registry_for_tests()
    server = PreviewHttpServer()
    await server.start("127.0.0.1", 0)
    try:
        yield server
    finally:
        await server.stop()
        reset_preview_registry_for_tests()


async def _read_headers(
    reader: asyncio.StreamReader,
) -> tuple[str, list[tuple[str, str]]]:
    request_line = (await reader.readline()).decode("latin-1").rstrip("\r\n")
    headers: list[tuple[str, str]] = []
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        text = line.decode("latin-1").rstrip("\r\n")
        name, sep, value = text.partition(":")
        if sep:
            headers.append((name.strip(), value.lstrip()))
    return request_line, headers


def _header(headers: list[tuple[str, str]], name: str) -> str | None:
    want = name.lower()
    for key, value in headers:
        if key.lower() == want:
            return value
    return None


@pytest.mark.asyncio
async def test_enter_sets_cookie_and_redirects():
    token = _token()
    async with (
        _proxy() as proxy,
        httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{proxy.port}",
            timeout=5.0,
            follow_redirects=False,
        ) as client,
    ):
        resp = await client.get("/enter", params={"t": token})
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    set_cookie = resp.headers["set-cookie"]
    assert f"{PREVIEW_COOKIE}=" in set_cookie
    assert token in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/" in set_cookie
    assert "SameSite=Lax" in set_cookie
    flags = {part.strip().split("=", 1)[0] for part in set_cookie.split(";")}
    assert "Secure" not in flags


@pytest.mark.asyncio
async def test_cookie_proxies_http_200():
    token = _token()
    captured: dict[str, object] = {}

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line, headers = await _read_headers(reader)
        captured["request_line"] = request_line
        captured["headers"] = headers
        body = b"ok-preview"
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()

    up = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = int(up.sockets[0].getsockname()[1])
    try:
        async with _proxy() as proxy:
            register_preview(
                conversation_id=_CID,
                process_id=_PID,
                upstream_ip="127.0.0.1",
                upstream_port=up_port,
                app_port=_APP_PORT,
            )
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{proxy.port}",
                timeout=5.0,
                cookies={PREVIEW_COOKIE: token, "other": "keep"},
            ) as client:
                resp = await client.get(
                    "/",
                    headers={"Origin": "http://preview.example"},
                )
            assert resp.status_code == 200
            assert resp.text == "ok-preview"
    finally:
        up.close()
        await up.wait_closed()

    headers = captured["headers"]
    assert isinstance(headers, list)
    assert _header(headers, "Host") == f"127.0.0.1:{_APP_PORT}"
    assert _header(headers, "Origin") == f"http://127.0.0.1:{_APP_PORT}"
    cookie = _header(headers, "Cookie") or ""
    assert PREVIEW_COOKIE not in cookie
    assert "other=keep" in cookie


@pytest.mark.asyncio
async def test_missing_cookie_401():
    async with _proxy() as proxy:
        base = f"http://127.0.0.1:{proxy.port}"
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            missing = await client.get("/")
        async with httpx.AsyncClient(
            base_url=base,
            timeout=5.0,
            cookies={PREVIEW_COOKIE: create_access_token("user-1", audience="product")},
        ) as client:
            wrong = await client.get("/")
        async with httpx.AsyncClient(
            base_url=base,
            timeout=5.0,
            cookies={PREVIEW_COOKIE: _token(expires_delta=timedelta(minutes=-1))},
        ) as client:
            expired = await client.get("/")
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert expired.status_code == 401


@pytest.mark.asyncio
async def test_dead_upstream_502_unregisters():
    token = _token()
    holder = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    dead_port = int(holder.sockets[0].getsockname()[1])
    holder.close()
    await holder.wait_closed()
    async with _proxy() as proxy:
        register_preview(
            conversation_id=_CID,
            process_id=_PID,
            upstream_ip="127.0.0.1",
            upstream_port=dead_port,
            app_port=_APP_PORT,
        )
        assert lookup_preview(_CID, _PID, _APP_PORT) is not None
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{proxy.port}",
            timeout=5.0,
            cookies={PREVIEW_COOKIE: token},
        ) as client:
            resp = await client.get("/")
        assert resp.status_code == 502
        assert lookup_preview(_CID, _PID, _APP_PORT) is None


@pytest.mark.asyncio
async def test_registry_miss_502_unregisters():
    token = _token()
    async with _proxy() as proxy:
        assert lookup_preview(_CID, _PID, _APP_PORT) is None
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{proxy.port}",
            timeout=5.0,
            cookies={PREVIEW_COOKIE: token},
        ) as client:
            resp = await client.get("/")
        assert resp.status_code == 502
        assert lookup_preview(_CID, _PID, _APP_PORT) is None


@pytest.mark.asyncio
async def test_websocket_upgrade_pumps():
    token = _token()
    captured: dict[str, object] = {}

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        _line, headers = await _read_headers(reader)
        captured["headers"] = headers
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n\r\n"
        )
        await writer.drain()
        payload = await reader.readexactly(8)
        captured["payload"] = payload
        writer.write(payload)
        await writer.drain()
        writer.close()

    up = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = int(up.sockets[0].getsockname()[1])
    try:
        async with _proxy() as proxy:
            register_preview(
                conversation_id=_CID,
                process_id=_PID,
                upstream_ip="127.0.0.1",
                upstream_port=up_port,
                app_port=_APP_PORT,
            )
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            try:
                writer.write(
                    b"GET / HTTP/1.1\r\n"
                    b"Host: preview.example\r\n"
                    b"Upgrade: websocket\r\n"
                    b"Connection: Upgrade\r\n"
                    b"Origin: http://preview.example\r\n"
                    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    b"Sec-WebSocket-Version: 13\r\n"
                    + f"Cookie: {PREVIEW_COOKIE}={token}; extra=1\r\n".encode()
                    + b"\r\n"
                )
                await writer.drain()
                status, headers = await _read_headers(reader)
                assert status.startswith("HTTP/1.1 101")
                assert _header(headers, "Upgrade") == "websocket"
                writer.write(b"hmr-ping")
                await writer.drain()
                echoed = await asyncio.wait_for(reader.readexactly(8), timeout=5.0)
                assert echoed == b"hmr-ping"
            finally:
                writer.close()
                await writer.wait_closed()
    finally:
        up.close()
        await up.wait_closed()

    headers = captured["headers"]
    assert isinstance(headers, list)
    assert _header(headers, "Host") == f"127.0.0.1:{_APP_PORT}"
    assert _header(headers, "Origin") == f"http://127.0.0.1:{_APP_PORT}"
    cookie = _header(headers, "Cookie") or ""
    assert PREVIEW_COOKIE not in cookie
    assert "extra=1" in cookie
    assert captured["payload"] == b"hmr-ping"


@pytest.mark.asyncio
async def test_server_preview_register_rpc_params():
    reset_preview_registry_for_tests()
    server = SandboxdServer(
        socket_path="unused.sock",
        runsc_path="runsc",
        runtime_root=".",
    )
    server._preview_register(
        {
            "conversation_id": _CID,
            "process_id": _PID,
            "upstream_ip": "127.0.0.1",
            "upstream_port": 28000,
            "app_port": _APP_PORT,
        }
    )
    got = lookup_preview(_CID, _PID, _APP_PORT)
    assert got is not None
    assert got.upstream_port == 28000
    assert got.app_port == _APP_PORT
    with pytest.raises(RpcDeniedError):
        server._preview_register(
            {
                "conversation_id": _CID,
                "process_id": _PID,
                "upstream_ip": "not-an-ip",
                "upstream_port": 28000,
                "app_port": _APP_PORT,
            }
        )
    server._preview_unregister({"conversation_id": _CID, "process_id": _PID})
    assert lookup_preview(_CID, _PID, _APP_PORT) is None
    reset_preview_registry_for_tests()


@pytest.mark.asyncio
async def test_two_app_ports_do_not_overwrite():
    async def make_upstream(label: str):
        async def upstream(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await _read_headers(reader)
            body = label.encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        return server, int(server.sockets[0].getsockname()[1])

    first, first_up = await make_upstream("vite")
    second, second_up = await make_upstream("storybook")
    try:
        async with _proxy() as proxy:
            register_preview(
                conversation_id=_CID,
                process_id=_PID,
                upstream_ip="127.0.0.1",
                upstream_port=first_up,
                app_port=5173,
            )
            register_preview(
                conversation_id=_CID,
                process_id=_PID,
                upstream_ip="127.0.0.1",
                upstream_port=second_up,
                app_port=6006,
            )
            base = f"http://127.0.0.1:{proxy.port}"
            async with httpx.AsyncClient(
                base_url=base,
                timeout=5.0,
                cookies={PREVIEW_COOKIE: _token(port=5173)},
            ) as client:
                vite = await client.get("/")
            async with httpx.AsyncClient(
                base_url=base,
                timeout=5.0,
                cookies={PREVIEW_COOKIE: _token(port=6006)},
            ) as client:
                story = await client.get("/")
            async with httpx.AsyncClient(
                base_url=base,
                timeout=5.0,
                cookies={PREVIEW_COOKIE: _token(port=3000)},
            ) as client:
                miss = await client.get("/")
            assert vite.status_code == 200 and vite.text == "vite"
            assert story.status_code == 200 and story.text == "storybook"
            assert miss.status_code == 502
            assert lookup_preview(_CID, _PID, 5173) is not None
            assert lookup_preview(_CID, _PID, 6006) is not None
            assert lookup_preview(_CID, _PID, 3000) is None
    finally:
        first.close()
        second.close()
        await first.wait_closed()
        await second.wait_closed()
