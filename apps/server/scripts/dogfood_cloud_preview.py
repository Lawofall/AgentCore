#!/usr/bin/env python3
"""Cloud user-preview origin dogfood (安全 · 五、第二刀).

Does **not** start gVisor. Any OS can exercise mint-shaped ``/enter`` → cookie →
reverse-proxy → stub HTTP. The product button (``run`` wait_for → ToolLine
「打开预览」) still needs a Linux API + sandboxd; Windows local API cannot
assemble cloud ``run``.

Usage (from ``apps/server``)::

    uv run python scripts/dogfood_cloud_preview.py
    uv run python scripts/dogfood_cloud_preview.py --serve
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from urllib.parse import parse_qs, urlsplit

import httpx

from agentcore.security.tokens import create_preview_token
from agentcore.tools.sandbox.sandboxd.preview_http import (
    PREVIEW_COOKIE,
    PreviewHttpServer,
    register_preview,
    reset_preview_registry_for_tests,
)

_CID = "dogfood-preview-conv"
_PID = "dogfood-preview-proc"
_APP_PORT = 5173
_MARKER = "dogfood-preview-ok"


async def _stub_http(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    while await reader.readline() not in (b"\r\n", b"\n", b""):
        pass
    body = _MARKER.encode()
    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )
    await writer.drain()
    writer.close()


@asynccontextmanager
async def _origin() -> AsyncIterator[tuple[PreviewHttpServer, str]]:
    reset_preview_registry_for_tests()
    stub = await asyncio.start_server(_stub_http, "127.0.0.1", 0)
    stub_port = int(stub.sockets[0].getsockname()[1])
    proxy = PreviewHttpServer()
    await proxy.start("127.0.0.1", 0)
    register_preview(
        conversation_id=_CID,
        process_id=_PID,
        upstream_ip="127.0.0.1",
        upstream_port=stub_port,
        app_port=_APP_PORT,
    )
    token = create_preview_token(
        "dogfood-user",
        conversation_id=_CID,
        process_id=_PID,
        port=_APP_PORT,
    )
    enter = f"http://127.0.0.1:{proxy.port}/enter?t={token}"
    try:
        yield proxy, enter
    finally:
        await proxy.stop()
        stub.close()
        await stub.wait_closed()
        reset_preview_registry_for_tests()


async def _check(enter: str) -> None:
    parts = urlsplit(enter)
    token = (parse_qs(parts.query).get("t") or [""])[0]
    base = f"{parts.scheme}://{parts.hostname}:{parts.port}"
    timeout = httpx.Timeout(5.0)
    async with httpx.AsyncClient(base_url=base, timeout=timeout) as authed:
        bounced = await authed.get("/enter", params={"t": token}, follow_redirects=False)
        if bounced.status_code != 302 or bounced.headers.get("location") != "/":
            raise SystemExit(f"enter expected 302 /, got {bounced.status_code}")
        cookie = bounced.headers.get("set-cookie") or ""
        if PREVIEW_COOKIE not in cookie or "HttpOnly" not in cookie:
            raise SystemExit("enter did not set HttpOnly preview cookie")
        flags = {part.strip().split("=", 1)[0] for part in cookie.split(";")}
        if "Secure" in flags:
            raise SystemExit("local http origin must not set Secure on the preview cookie")
        ok = await authed.get("/")
        if ok.status_code != 200 or _MARKER not in ok.text:
            raise SystemExit(f"proxied GET / failed: {ok.status_code} {ok.text!r}")
    async with httpx.AsyncClient(base_url=base, timeout=timeout) as anon:
        denied = await anon.get("/")
        if denied.status_code != 401:
            raise SystemExit(f"no cookie expected 401, got {denied.status_code}")
        bad = await anon.get("/enter", follow_redirects=False)
        if bad.status_code != 401:
            raise SystemExit(f"enter without ticket expected 401, got {bad.status_code}")
    print("  OK  /enter → cookie → stub HTTP")
    print("  OK  missing ticket → 401")


async def _amain(serve: bool) -> None:
    async with _origin() as (_proxy, enter):
        await _check(enter)
        if not serve:
            return
        print(enter)
        print("serving until Ctrl+C — open the URL above in a system browser", file=sys.stderr)
        await asyncio.Event().wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serve",
        action="store_true",
        help="keep the origin up after the check so a browser can click /enter",
    )
    args = parser.parse_args()
    with suppress(KeyboardInterrupt):
        asyncio.run(_amain(args.serve))


if __name__ == "__main__":
    main()
