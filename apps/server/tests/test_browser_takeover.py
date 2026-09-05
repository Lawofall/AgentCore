"""Browser sessions: ``control`` is always agent; no takeover mark / mutex."""

from __future__ import annotations

import time

import pytest

from agentcore.runtime.browser.registry import BrowserSessionRegistry
from agentcore.tools.sandbox.browser.protocol import (
    BrowserCommand,
    BrowserCommandResult,
    BrowserSessionRequest,
)


class FakeBrowserSession:
    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self.created_at = time.time()
        self.last_used = time.time()
        self._alive = True
        self.closed = False

    @property
    def alive(self) -> bool:
        return self._alive

    async def send(self, command: BrowserCommand) -> BrowserCommandResult:
        self.last_used = time.time()
        return BrowserCommandResult(ok=True, data={"injected": 1})

    async def close(self) -> None:
        self.closed = True
        self._alive = False


def _make_registry(**kw) -> BrowserSessionRegistry:
    kw.setdefault("max_sessions", 8)
    kw.setdefault("idle_ttl_seconds", 1000)
    kw.setdefault("max_lifetime_seconds", 100000)

    async def factory(request: BrowserSessionRequest) -> FakeBrowserSession:
        return FakeBrowserSession(request.conversation_id)

    return BrowserSessionRegistry(factory=factory, **kw)


def test_registry_has_no_takeover_api():
    assert not hasattr(BrowserSessionRegistry, "is_taken_over")
    assert not hasattr(BrowserSessionRegistry, "begin_takeover")
    assert not hasattr(BrowserSessionRegistry, "end_takeover")
    assert not hasattr(BrowserSessionRegistry, "takeover_mark")
    assert not hasattr(BrowserSessionRegistry, "set_takeover_finalizer")


@pytest.mark.asyncio
async def test_registry_control_is_always_agent():
    reg = _make_registry()
    await reg.create(BrowserSessionRequest(conversation_id="c1"), activate=True)
    await reg.create(BrowserSessionRequest(conversation_id="c1"), activate=False)
    infos = reg.list_by_conversation("c1")
    assert infos
    assert all(info.control == "agent" for info in infos)
