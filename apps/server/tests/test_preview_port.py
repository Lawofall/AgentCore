"""Parse HTTP listen ports from long-running process ready logs."""

from __future__ import annotations

import pytest

from agentcore.tools.sandbox.preview_port import parse_preview_http_ports


@pytest.mark.parametrize(
    ("output", "ports"),
    [
        ("Local: http://localhost:5173/", (5173,)),
        ("  http://127.0.0.1:4173/ ", (4173,)),
        ("https://0.0.0.0:8080", (8080,)),
        ("http://[::1]:3000/", (3000,)),
        ("http://[::]:4173", (4173,)),
        ("waiting on localhost:3000", (3000,)),
        ("bind 127.0.0.1:8000 ok", (8000,)),
        ("0.0.0.0:5173", (5173,)),
        ("listening on [::1]:8080", (8080,)),
        ("bound [::]:9000", (9000,)),
        ("Listening on port 4173", (4173,)),
        ("Listening on :3000", (3000,)),
        ("Server running at port 8081", (8081,)),
        ("Application started on :5000", (5000,)),
        ("", ()),
        ("no listen banner here", ()),
    ],
)
def test_parse_preview_http_ports_shapes(output: str, ports: tuple[int, ...]):
    assert parse_preview_http_ports(output) == ports


def test_parse_preview_http_ports_unique_appearance_order():
    text = "serving on port 3000 then http://127.0.0.1:5173/ and localhost:3000"
    assert parse_preview_http_ports(text) == (3000, 5173)


def test_parse_preview_http_ports_url_does_not_duplicate_hostport():
    assert parse_preview_http_ports("Local: http://localhost:5173/\n") == (5173,)
