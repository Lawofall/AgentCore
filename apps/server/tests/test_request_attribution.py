"""RequestAttributionMiddleware: HTTP identity + client header log context."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from agentcore.core.log_context import clear_log_context, get_log_value
from agentcore.middleware.request_attribution import (
    _MAX_PLATFORM_LEN,
    _MAX_VERSION_LEN,
    MISSING_CLIENT_HEADER,
    RequestAttributionMiddleware,
    client_header_for_log,
    stream_path_reason_for_log,
)


@pytest.fixture(autouse=True)
def _clear_log_ctx() -> None:
    clear_log_context()
    yield
    clear_log_context()


def test_client_header_for_log_distinguishes_absent_from_empty() -> None:
    assert client_header_for_log(None, max_len=64) == MISSING_CLIENT_HEADER
    assert client_header_for_log("", max_len=64) == ""
    assert client_header_for_log("   ", max_len=64) == ""
    assert client_header_for_log("  0.9.4  ", max_len=64) == "0.9.4"
    assert client_header_for_log("desktop", max_len=32) == "desktop"


def test_client_header_for_log_truncates_overlong() -> None:
    raw = "v" * (_MAX_VERSION_LEN + 8)
    assert client_header_for_log(raw, max_len=_MAX_VERSION_LEN) == "v" * _MAX_VERSION_LEN
    plat = "p" * (_MAX_PLATFORM_LEN + 3)
    assert client_header_for_log(plat, max_len=_MAX_PLATFORM_LEN) == "p" * _MAX_PLATFORM_LEN


async def _seen_from_request(**headers: str) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def homepage(_request):  # noqa: ANN001
        ctx = dict(structlog.contextvars.get_contextvars())
        seen["ctx"] = ctx
        seen["via_get"] = {
            "platform": get_log_value("client_platform"),
            "version": get_log_value("client_version"),
        }
        task = asyncio.current_task()
        seen["task_name"] = task.get_name() if task else ""
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/v1/ping", homepage)])
    app.add_middleware(RequestAttributionMiddleware)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/ping", headers=headers)
    assert resp.status_code == 200
    return seen


async def test_middleware_binds_client_headers_when_present() -> None:
    seen = await _seen_from_request(
        **{"X-Client-Platform": "desktop", "X-Client-Version": "0.9.4"}
    )
    ctx = seen["ctx"]
    assert ctx["client_platform"] == "desktop"
    assert ctx["client_version"] == "0.9.4"
    assert ctx["http_method"] == "GET"
    assert ctx["http_path"] == "/v1/ping"
    assert len(ctx["http_req_id"]) == 12
    assert seen["task_name"] == "http:GET /v1/ping"


async def test_middleware_missing_headers_use_sentinel_not_empty() -> None:
    seen = await _seen_from_request()
    ctx = seen["ctx"]
    assert ctx["client_platform"] == MISSING_CLIENT_HEADER
    assert ctx["client_version"] == MISSING_CLIENT_HEADER
    # Sentinel is truthy so get_log_value does not collapse it to default "".
    assert seen["via_get"]["platform"] == MISSING_CLIENT_HEADER
    assert seen["via_get"]["version"] == MISSING_CLIENT_HEADER


def test_stream_path_reason_for_log_allowlists() -> None:
    assert stream_path_reason_for_log(None) is None
    assert stream_path_reason_for_log("") is None
    assert stream_path_reason_for_log("not-a-reason") is None
    assert stream_path_reason_for_log("probe_unhealthy") == "probe_unhealthy"
    assert stream_path_reason_for_log("  Probe_Unhealthy  ") == "probe_unhealthy"


async def test_middleware_binds_stream_path_reason_when_allowlisted() -> None:
    seen = await _seen_from_request(
        **{
            "X-Client-Platform": "desktop",
            "X-AgentCore-Stream-Path-Reason": "probe_unhealthy",
        }
    )
    assert seen["ctx"]["stream_path_reason"] == "probe_unhealthy"


async def test_middleware_ignores_unknown_stream_path_reason() -> None:
    seen = await _seen_from_request(
        **{"X-AgentCore-Stream-Path-Reason": "please-inject"}
    )
    assert "stream_path_reason" not in seen["ctx"]


async def test_middleware_omits_stream_path_reason_when_absent() -> None:
    seen = await _seen_from_request(**{"X-Client-Platform": "desktop"})
    assert "stream_path_reason" not in seen["ctx"]


async def test_middleware_empty_headers_stay_empty_string() -> None:
    """Present-but-empty must not collapse to the missing sentinel.

    Drive ASGI headers as bytes so an empty value actually reaches the
    middleware (some HTTP clients omit empty headers).
    """
    seen: dict[str, Any] = {}

    async def homepage(_request):  # noqa: ANN001
        seen["ctx"] = dict(structlog.contextvars.get_contextvars())
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/v1/ping", homepage)])
    app.add_middleware(RequestAttributionMiddleware)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/v1/ping",
        "raw_path": b"/v1/ping",
        "query_string": b"",
        "headers": [
            (b"host", b"test"),
            (b"x-client-platform", b""),
            (b"x-client-version", b"  "),
        ],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: object) -> None:
        return None

    await app(scope, receive, send)
    ctx = seen["ctx"]
    assert ctx["client_platform"] == ""
    assert ctx["client_version"] == ""
    assert ctx["client_platform"] != MISSING_CLIENT_HEADER
    assert ctx["client_version"] != MISSING_CLIENT_HEADER
