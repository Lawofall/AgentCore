"""Sidecar ``listBrowserSessions`` RPC — Local hydrate from same-process Registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import pytest

from agentcore.runtime.browser import registry as registry_mod
from agentcore.sidecar import protocol
from agentcore.sidecar.server import SidecarServer


@dataclass(frozen=True)
class _Info:
    session_id: str
    conversation_id: str
    host_kind: Literal["sandbox", "local"]
    run_id: str | None
    created_at: float
    last_used: float
    control: Literal["agent", "user"]
    url: str | None = None
    title: str | None = None


class _FakeReg:
    def __init__(self, infos: list[_Info], active: str | None) -> None:
        self._infos = infos
        self._active = active

    def list_by_conversation(self, conversation_id: str) -> list[_Info]:
        return [i for i in self._infos if i.conversation_id == conversation_id]

    def resolve_session_id(self, conversation_id: str, **_kw: Any) -> str | None:
        if any(i.conversation_id == conversation_id for i in self._infos):
            return self._active
        return None


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


def _response(sent: list[dict[str, Any]], request_id: Any) -> dict[str, Any]:
    return next(m for m in sent if m.get("id") == request_id)


async def _call_list(
    server: SidecarServer, request_id: int, conversation_id: str | None
) -> None:
    params: dict[str, Any] = {}
    if conversation_id is not None:
        params["conversationId"] = conversation_id
    await server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "listBrowserSessions",
                "params": params,
            }
        )
    )


@pytest.mark.asyncio
async def test_list_browser_sessions_empty(monkeypatch):
    monkeypatch.setattr(registry_mod, "_registry", _FakeReg([], None))
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    await _call_list(server, 1, "c1")
    resp = _response(sent, 1)
    assert resp["result"] == {"data": [], "active_session_id": None}


@pytest.mark.asyncio
async def test_list_browser_sessions_with_entries(monkeypatch):
    infos = [
        _Info(
            session_id="s1",
            conversation_id="c1",
            host_kind="local",
            run_id=None,
            created_at=1.0,
            last_used=2.0,
            control="agent",
            url="https://example.com/",
            title="Example",
        ),
        _Info(
            session_id="s2",
            conversation_id="c1",
            host_kind="local",
            run_id="r1",
            created_at=3.0,
            last_used=4.0,
            control="agent",
            url=None,
            title=None,
        ),
    ]
    monkeypatch.setattr(registry_mod, "_registry", _FakeReg(infos, "s1"))
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    await _call_list(server, 2, "c1")
    resp = _response(sent, 2)
    result = resp["result"]
    assert result["active_session_id"] == "s1"
    assert len(result["data"]) == 2
    assert result["data"][0] == {
        "session_id": "s1",
        "conversation_id": "c1",
        "host_kind": "local",
        "control": "agent",
        "run_id": None,
        "created_at": 1.0,
        "last_used": 2.0,
        "url": "https://example.com/",
        "title": "Example",
    }
    assert result["data"][1]["session_id"] == "s2"
    assert result["data"][1]["control"] == "agent"


@pytest.mark.asyncio
async def test_list_browser_sessions_requires_conversation_id():
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    await _call_list(server, 3, None)
    resp = _response(sent, 3)
    assert resp["error"]["code"] == protocol.INVALID_PARAMS
