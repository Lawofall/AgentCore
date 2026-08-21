"""GVisorBrowserSession close/open: sandboxd stdio, then kill/delete + netns_teardown.

API never execs ``runsc`` / ``ip``. Shape B argv always includes ``--ignore-cgroups``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from agentcore.tools.sandbox.browser import gvisor_session as gs
from agentcore.tools.sandbox.browser.gvisor_session import GVisorBrowserSession
from agentcore.tools.sandbox.browser.protocol import (
    BrowserSessionError,
    BrowserSessionRequest,
)
from agentcore.tools.sandbox.sandboxd.argv import build_runsc_cmd
from agentcore.tools.sandbox.sandboxd.client import set_sandboxd_client_for_tests

_CID = "agentcore-browser-test"


class _FakeChannel:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.closed = False

    async def request(self, action: str, args: dict, *, timeout: float) -> dict:
        self.requests.append(action)
        return {"id": 1, "ok": True, "closed": True}

    async def aclose(self) -> None:
        self.closed = True


class _FakeStdio:
    def __init__(self) -> None:
        self._closed = False
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
        self._closed = True


class _FakeNetns:
    def __init__(self) -> None:
        self.torn = False

    async def teardown(self) -> None:
        self.torn = True


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
        self.calls.append(("kill", container_id, signal))

    async def delete(self, container_id: str, *, force: bool = True) -> None:
        self.calls.append(("delete", container_id, force))


def _make_session(
    stdio: _FakeStdio, channel: _FakeChannel, netns: _FakeNetns, client: _FakeClient
):
    return GVisorBrowserSession(
        conversation_id="c1",
        slot=0,
        netns=netns,  # type: ignore[arg-type]
        bundle_dir="/nonexistent/agentcore_browser_test_bundle",
        container_id=_CID,
        stdio=stdio,
        channel=channel,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_close_aclose_stdio_then_kill_delete():
    stdio = _FakeStdio()
    ch = _FakeChannel()
    netns = _FakeNetns()
    client = _FakeClient()
    session = _make_session(stdio, ch, netns, client)

    await session.close()

    assert ch.requests == ["close"]
    assert ch.closed is True
    assert stdio.closed is True
    assert client.calls == [
        ("kill", _CID, "SIGKILL"),
        ("delete", _CID, True),
    ]
    assert netns.torn is True
    assert session.alive is False


@pytest.mark.asyncio
async def test_close_is_idempotent():
    stdio = _FakeStdio()
    ch = _FakeChannel()
    client = _FakeClient()
    session = _make_session(stdio, ch, _FakeNetns(), client)

    await session.close()
    assert len(client.calls) == 2
    await session.close()
    assert len(client.calls) == 2
    assert ch.requests == ["close"]


@pytest.mark.asyncio
async def test_close_after_driver_crash_still_reclaims_resources():
    """A crashed session (``_alive=False``, teardown never ran) must still tear down fully.

    Idempotency is keyed on ``_closed``, not ``_alive`` — otherwise the netns/veth, the
    concurrency slot, the runsc container and the bundle dir of a crashed driver leak until
    process exit (they are host-side resources; the crash only killed the RPC channel).
    """
    stdio = _FakeStdio()
    ch = _FakeChannel()
    netns = _FakeNetns()
    client = _FakeClient()
    session = _make_session(stdio, ch, netns, client)

    session._alive = False

    await session.close()

    assert ch.requests == []
    assert ch.closed is True
    assert stdio.closed is True
    assert client.calls == [
        ("kill", _CID, "SIGKILL"),
        ("delete", _CID, True),
    ]
    assert netns.torn is True

    await session.close()
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_close_swallows_client_kill_failure():
    """Best-effort teardown: a failing kill/delete must not raise."""

    class _BoomClient(_FakeClient):
        async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
            raise RuntimeError("sandboxd kill failed")

    stdio = _FakeStdio()
    ch = _FakeChannel()
    netns = _FakeNetns()
    session = _make_session(stdio, ch, netns, _BoomClient())
    await session.close()
    assert ch.closed is True
    assert netns.torn is True


def test_build_browser_runsc_cmd_matches_shape_net():
    kwargs = {
        "runsc_path": "runsc",
        "runtime_root": "/data/sandbox",
        "bundle_dir": "/b",
        "container_id": "c1",
    }
    cmd = gs.build_browser_runsc_cmd(**kwargs)
    assert cmd == build_runsc_cmd(**kwargs, shape="net")
    run_idx = cmd.index("run")
    assert "--ignore-cgroups" in cmd[:run_idx]
    assert "--rootless" not in cmd[:run_idx]
    assert cmd[:4] == [
        "runsc",
        "--platform=systrap",
        "--network=sandbox",
        "--ignore-cgroups",
    ]


class _FakeProxy:
    port = 8899


class _OpenFakeNetns:
    def __init__(self, **_kwargs):
        self.host_ip = "10.201.0.1"
        self.netns_path = "/var/run/netns/acbrw0"

    async def setup(self) -> None:
        return None

    async def teardown(self) -> None:
        return None


class _ReadyStdio:
    def __init__(self) -> None:
        self._ready_sent = False
        self._closed = False
        self._closed_event = asyncio.Event()
        self._replies: asyncio.Queue[bytes] = asyncio.Queue()
        self.closed = False
        self.writes: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.writes.append(data)
        msg = json.loads(data.decode())
        rid = msg.get("id")
        await self._replies.put(
            (json.dumps({"id": rid, "ok": True, "closed": True}) + "\n").encode()
        )

    async def readline(self) -> bytes:
        if not self._ready_sent:
            self._ready_sent = True
            return b'{"event":"ready","id":0,"ok":true}\n'
        get = asyncio.create_task(self._replies.get())
        closed = asyncio.create_task(self._closed_event.wait())
        done, pending = await asyncio.wait(
            {get, closed}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if get in done:
            return get.result()
        return b""

    async def aclose(self) -> None:
        self.closed = True
        self._closed = True
        self._closed_event.set()


class _EofStdio:
    _closed = False

    async def write(self, data: bytes) -> None:
        return None

    async def readline(self) -> bytes:
        return b""

    async def aclose(self) -> None:
        self._closed = True


class _OpenFakeClient:
    def __init__(self, stdio) -> None:
        self.stdio = stdio
        self.run_stdio_calls: list[dict] = []
        self.kills: list[str] = []
        self.deletes: list[str] = []

    async def run_stdio(self, *, bundle_dir: str, container_id: str, netns_path: str):
        self.run_stdio_calls.append(
            {
                "bundle_dir": bundle_dir,
                "container_id": container_id,
                "netns_path": netns_path,
            }
        )
        return self.stdio

    async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
        self.kills.append(container_id)

    async def delete(self, container_id: str, *, force: bool = True) -> None:
        self.deletes.append(container_id)


def _install_open_mocks(monkeypatch, *, stdio):
    monkeypatch.setattr(gs, "_IS_LINUX", True)

    async def _proxy():
        return _FakeProxy()

    monkeypatch.setattr(gs, "ensure_browser_proxy", _proxy)
    monkeypatch.setattr(gs, "SessionNetns", _OpenFakeNetns)
    client = _OpenFakeClient(stdio)
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    gs._used_slots.clear()
    return client


@pytest.mark.asyncio
async def test_open_session_uses_run_stdio_not_subprocess(monkeypatch, tmp_path):
    async def _boom(*_a, **_k):
        raise AssertionError("API must not exec runsc")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    client = _install_open_mocks(monkeypatch, stdio=_ReadyStdio())
    runtime_root = tmp_path / "rt"
    session = await gs.open_gvisor_browser_session(
        BrowserSessionRequest(conversation_id="c-ok"),
        runsc_path="runsc",
        runtime_root=str(runtime_root),
    )
    try:
        assert client.run_stdio_calls
        assert client.run_stdio_calls[0]["netns_path"] == "/var/run/netns/acbrw0"
        bundle = client.run_stdio_calls[0]["bundle_dir"]
        assert Path(bundle).resolve().is_relative_to(runtime_root.resolve())
        assert session.alive is True
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_open_session_stdio_eof_logs_and_cleans_up(monkeypatch, tmp_path):
    async def _boom(*_a, **_k):
        raise AssertionError("API must not exec runsc")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    client = _install_open_mocks(monkeypatch, stdio=_EofStdio())

    with (
        capture_logs() as logs,
        pytest.raises(BrowserSessionError, match="RpcChannelClosedError"),
    ):
        await gs.open_gvisor_browser_session(
            BrowserSessionRequest(conversation_id="c-eof"),
            runsc_path="runsc",
            runtime_root=str(tmp_path / "rt"),
        )

    assert client.run_stdio_calls
    assert client.kills and client.deletes
    events = {row["event"]: row for row in logs if "event" in row}
    failed = events["browser.session_open_failed"]
    assert failed["conversation_id"] == "c-eof"
    assert failed["error_type"] == "RpcChannelClosedError"
