"""DEBUG loopback CORS: a drifted local Vite port is not an outage.

Credentialed CORS cannot use ``*``. While DEBUG is on, any
``http://localhost:<port>`` or ``http://127.0.0.1:<port>`` is allowed so a
Vite process that walked off 5173 still passes the browser preflight.
Production keeps the explicit origin list only.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from httpx import Response

from agentcore.config import settings
from agentcore.config.auth import dev_cors_origin_regex
from agentcore.main import app

_EXPLICIT = "http://localhost:5173"


def _client(*, debug: bool) -> TestClient:
    api = FastAPI()

    @api.get("/readyz")
    def readyz() -> dict[str, str]:
        return {"status": "ready"}

    api.add_middleware(
        CORSMiddleware,
        allow_origins=[_EXPLICIT],
        allow_origin_regex=dev_cors_origin_regex(debug=debug),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return TestClient(api)


def _allow_origin(res: Response) -> str | None:
    return res.headers.get("access-control-allow-origin")


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5178",
        "http://127.0.0.1:5999",
        "http://localhost:1",
        "http://127.0.0.1:65535",
    ],
)
def test_debug_allows_loopback_port(origin: str) -> None:
    res = _client(debug=True).get("/readyz", headers={"Origin": origin})
    assert res.status_code == 200
    assert _allow_origin(res) == origin
    assert res.headers.get("access-control-allow-credentials") == "true"


def test_debug_preflight_echoes_drifted_vite_origin() -> None:
    origin = "http://localhost:5178"
    res = _client(debug=True).options(
        "/readyz",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert _allow_origin(res) == origin
    assert res.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://localhost.evil.com:5173",
        "http://127.0.0.1.evil.com:80",
        "https://localhost:5178",
        "http://localhost",
        "http://[::1]:5178",
        "http://2130706433:5173",
        "http://localhost:5178/",
        "http://127.0.0.1:655350",
        "null",
    ],
)
def test_debug_regex_does_not_widen_past_loopback_ports(origin: str) -> None:
    res = _client(debug=True).get("/readyz", headers={"Origin": origin})
    assert _allow_origin(res) is None


def test_production_keeps_explicit_list_only() -> None:
    assert dev_cors_origin_regex(debug=False) is None
    client = _client(debug=False)
    drifted = client.get("/readyz", headers={"Origin": "http://localhost:5178"})
    assert _allow_origin(drifted) is None
    listed = client.get("/readyz", headers={"Origin": _EXPLICIT})
    assert _allow_origin(listed) == _EXPLICIT


def test_app_middleware_follows_debug_flag() -> None:
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert cors.kwargs["allow_origin_regex"] == dev_cors_origin_regex(debug=settings.debug)
    assert cors.kwargs["allow_credentials"] is True
