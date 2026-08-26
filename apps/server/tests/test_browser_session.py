"""GVisorBrowserSession: exec-into-desk, close does not tear down the guest."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from agentcore.tools.sandbox.browser import gvisor_session as gs
from agentcore.tools.sandbox.browser.driver import chromium_launch_args
from agentcore.tools.sandbox.browser.gvisor_session import GVisorBrowserSession
from agentcore.tools.sandbox.browser.protocol import (
    BrowserSessionError,
    BrowserSessionRequest,
)
from agentcore.tools.sandbox.gvisor import DeskAttach
from agentcore.tools.sandbox.sandboxd.argv import EXEC_BINS, build_runsc_exec_cmd
from agentcore.tools.sandbox.sandboxd.client import set_sandboxd_client_for_tests

_DESK = "agentcore-desk-testdesk01"
_EXEC = f"{_DESK}-exec-stdio-deadbeef0001"


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
        self.container_id = _EXEC

    async def aclose(self) -> None:
        self.closed = True
        self._closed = True


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
        self.calls.append(("kill", container_id, signal))

    async def delete(self, container_id: str, *, force: bool = True) -> None:
        self.calls.append(("delete", container_id, force))


def _make_session(stdio: _FakeStdio, channel: _FakeChannel, client: _FakeClient):
    return GVisorBrowserSession(
        conversation_id="c1",
        slot=0,
        exec_id=_EXEC,
        desk_container_id=_DESK,
        driver_script=None,
        stdio=stdio,
        channel=channel,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_close_kills_exec_not_desk():
    stdio = _FakeStdio()
    ch = _FakeChannel()
    client = _FakeClient()
    session = _make_session(stdio, ch, client)

    await session.close()

    assert ch.requests == ["close"]
    assert ch.closed is True
    assert stdio.closed is True
    assert client.calls == [("kill", _EXEC, "SIGKILL")]
    assert session.alive is False


@pytest.mark.asyncio
async def test_close_is_idempotent():
    stdio = _FakeStdio()
    ch = _FakeChannel()
    client = _FakeClient()
    session = _make_session(stdio, ch, client)

    await session.close()
    assert len(client.calls) == 1
    await session.close()
    assert len(client.calls) == 1
    assert ch.requests == ["close"]


@pytest.mark.asyncio
async def test_close_after_driver_crash_still_reclaims_driver_only():
    """Crashed session must kill the exec track, never the desk guest."""
    stdio = _FakeStdio()
    ch = _FakeChannel()
    client = _FakeClient()
    session = _make_session(stdio, ch, client)

    session._alive = False

    await session.close()

    assert ch.requests == []
    assert ch.closed is True
    assert stdio.closed is True
    assert client.calls == [("kill", _EXEC, "SIGKILL")]

    await session.close()
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_close_swallows_client_kill_failure():
    class _BoomClient(_FakeClient):
        async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
            raise RuntimeError("sandboxd kill failed")

    stdio = _FakeStdio()
    ch = _FakeChannel()
    session = _make_session(stdio, ch, _BoomClient())
    await session.close()
    assert ch.closed is True


def test_browser_exec_argv_stays_in_exec_bins():
    cmd = build_runsc_exec_cmd(
        runsc_path="runsc",
        runtime_root="/data/sandbox",
        container_id=_DESK,
        argv=["python3", "-u", "/scratch/browser_driver_0_abcd.py"],
        env=["BROWSER_PROXY=http://10.0.0.1:8899", "HTTP_PROXY="],
    )
    assert "python3" in EXEC_BINS
    assert cmd[0] == "runsc"
    assert "exec" in cmd
    assert "run" not in cmd[cmd.index("exec") :]
    assert "--cwd=/workspace" in cmd
    assert "/scratch/browser_driver_0_abcd.py" in cmd
    assert "-env" in cmd
    assert "HTTP_PROXY=" in cmd


def test_chromium_launch_args_bypass_loopback():
    args = chromium_launch_args("http://10.0.0.1:8899")
    assert any(a.startswith("--proxy-server=") for a in args)
    bypass = next(a for a in args if a.startswith("--proxy-bypass-list="))
    assert "<-loopback>" in bypass
    assert "127.0.0.1" in bypass


class _FakeProxy:
    port = 8899


class _ReadyStdio:
    def __init__(self) -> None:
        self._ready_sent = False
        self._closed = False
        self._closed_event = asyncio.Event()
        self._replies: asyncio.Queue[bytes] = asyncio.Queue()
        self.closed = False
        self.writes: list[bytes] = []
        self.container_id = _EXEC

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
    container_id = _EXEC

    async def write(self, data: bytes) -> None:
        return None

    async def readline(self) -> bytes:
        return b""

    async def aclose(self) -> None:
        self._closed = True


class _OpenFakeClient:
    def __init__(self, stdio) -> None:
        self.stdio = stdio
        self.exec_stdio_calls: list[dict] = []
        self.netns_setup_calls: list[dict] = []
        self.kills: list[str] = []
        self.deletes: list[str] = []

    async def exec_stdio(self, *, container_id: str, argv: list[str], cwd: str = "/workspace", env=None):
        self.exec_stdio_calls.append(
            {"container_id": container_id, "argv": argv, "cwd": cwd, "env": list(env or [])}
        )
        return self.stdio

    async def netns_setup(self, family: str, slot: int, subnet_base: str):
        self.netns_setup_calls.append(
            {"family": family, "slot": slot, "subnet_base": subnet_base}
        )
        raise AssertionError("must not set up family=browser netns")

    async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
        self.kills.append(container_id)

    async def delete(self, container_id: str, *, force: bool = True) -> None:
        self.deletes.append(container_id)


def _install_open_mocks(monkeypatch, tmp_path: Path, *, stdio):
    monkeypatch.setattr(gs, "_IS_LINUX", True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    desk = DeskAttach(container_id=_DESK, scratch_dir=scratch, host_ip="10.0.0.1")

    async def _attach(workspace, **_k):
        assert Path(workspace).is_dir()
        return desk

    async def _proxy():
        return _FakeProxy()

    monkeypatch.setattr("agentcore.tools.sandbox.gvisor.attach_workspace_desk", _attach)
    monkeypatch.setattr(gs, "ensure_browser_proxy", _proxy)
    client = _OpenFakeClient(stdio)
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    gs._used_slots.clear()
    return client


@pytest.mark.asyncio
async def test_open_session_uses_exec_stdio_not_second_container(monkeypatch, tmp_path):
    async def _boom(*_a, **_k):
        raise AssertionError("API must not exec runsc")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    client = _install_open_mocks(monkeypatch, tmp_path, stdio=_ReadyStdio())
    ws = tmp_path / "ws"
    ws.mkdir()
    session = await gs.open_gvisor_browser_session(
        BrowserSessionRequest(conversation_id="c-ok", workspace_root=str(ws)),
        runsc_path="runsc",
        runtime_root=str(tmp_path / "rt"),
    )
    try:
        assert not hasattr(client, "run_stdio")
        assert client.netns_setup_calls == []
        assert client.exec_stdio_calls
        call = client.exec_stdio_calls[0]
        assert call["container_id"] == _DESK
        assert call["cwd"] == "/workspace"
        assert call["argv"][0] == "python3"
        assert call["argv"][1] == "-u"
        assert call["argv"][2].startswith("/scratch/browser_driver_")
        env = call["env"]
        assert "BROWSER_PROXY=http://10.0.0.1:8899" in env
        assert "HTTP_PROXY=" in env
        assert not any(item.startswith("HTTP_PROXY=http") for item in env)
        assert session.alive is True
    finally:
        await session.close()
    assert _DESK not in client.kills
    assert _DESK not in client.deletes
    assert client.deletes == []
    assert _EXEC in client.kills


@pytest.mark.asyncio
async def test_open_without_workspace_root_fails_honestly(monkeypatch, tmp_path):
    called: list[int] = []

    async def _attach(*_a, **_k):
        called.append(1)
        raise AssertionError("must not attach without a disk")

    monkeypatch.setattr(gs, "_IS_LINUX", True)
    monkeypatch.setattr("agentcore.tools.sandbox.gvisor.attach_workspace_desk", _attach)
    with pytest.raises(BrowserSessionError, match="工作区盘"):
        await gs.open_gvisor_browser_session(
            BrowserSessionRequest(conversation_id="c-nodisk"),
            runsc_path="runsc",
            runtime_root=str(tmp_path / "rt"),
        )
    assert called == []


@pytest.mark.asyncio
async def test_open_non_directory_workspace_fails_honestly(monkeypatch, tmp_path):
    called: list[int] = []

    async def _attach(*_a, **_k):
        called.append(1)
        raise AssertionError("must not attach a missing disk")

    monkeypatch.setattr(gs, "_IS_LINUX", True)
    monkeypatch.setattr("agentcore.tools.sandbox.gvisor.attach_workspace_desk", _attach)
    missing = tmp_path / "no-such-ws"
    with pytest.raises(BrowserSessionError, match="工作区盘"):
        await gs.open_gvisor_browser_session(
            BrowserSessionRequest(conversation_id="c-missing", workspace_root=str(missing)),
            runsc_path="runsc",
            runtime_root=str(tmp_path / "rt"),
        )
    assert called == []


@pytest.mark.asyncio
async def test_open_session_stdio_eof_logs_and_cleans_driver_only(monkeypatch, tmp_path):
    async def _boom(*_a, **_k):
        raise AssertionError("API must not exec runsc")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    client = _install_open_mocks(monkeypatch, tmp_path, stdio=_EofStdio())
    ws = tmp_path / "ws"
    ws.mkdir()

    with (
        capture_logs() as logs,
        pytest.raises(BrowserSessionError, match="RpcChannelClosedError"),
    ):
        await gs.open_gvisor_browser_session(
            BrowserSessionRequest(conversation_id="c-eof", workspace_root=str(ws)),
            runsc_path="runsc",
            runtime_root=str(tmp_path / "rt"),
        )

    assert client.exec_stdio_calls
    assert not hasattr(client, "run_stdio")
    assert _EXEC in client.kills
    assert _DESK not in client.kills
    assert client.deletes == []
    events = {row["event"]: row for row in logs if "event" in row}
    failed = events["browser.session_open_failed"]
    assert failed["conversation_id"] == "c-eof"
    assert failed["error_type"] == "RpcChannelClosedError"
