"""GVisorBrowserSession.close() teardown: natural-exit ordering, SIGKILL fallback, bounded runsc.

These pin the D9/D10 teardown contract validated end-to-end by the gVisor smoke
(scripts/smoke_browser_gvisor): after the `close` RPC the `runsc run` supervisor is given a
bounded window to exit on its own (a clean exit lets `runsc delete` finish fast instead of the
~120s orphan force-delete path); only a wedged supervisor is SIGKILLed. All runsc waits are
bounded so teardown can never block its callers forever. Everything is driven with fakes /
monkeypatched subprocess, so it runs off-Linux without runsc.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from agentcore.tools.sandbox.browser import gvisor_session as gs
from agentcore.tools.sandbox.browser.gvisor_session import GVisorBrowserSession
from agentcore.tools.sandbox.browser.protocol import (
    BrowserSessionError,
    BrowserSessionRequest,
)

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


class _FakeProcess:
    """Stand-in for the `runsc run` supervisor.

    ``natural_exit=True`` → ``wait()`` returns promptly (the supervisor followed the driver out).
    ``natural_exit=False`` → wedged: ``wait()`` blocks until ``kill()`` (the SIGKILL fallback).
    """

    def __init__(self, *, natural_exit: bool) -> None:
        self._natural_exit = natural_exit
        self.returncode: int | None = None
        self.killed = False
        self.waited = False
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        self.waited = True
        if self._natural_exit:
            self.returncode = 0
            return 0
        await self._exited.wait()
        return self.returncode if self.returncode is not None else -9

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._exited.set()


class _FakeNetns:
    def __init__(self) -> None:
        self.torn = False

    async def teardown(self) -> None:
        self.torn = True


def _make_session(process: _FakeProcess, channel: _FakeChannel, netns: _FakeNetns):
    return GVisorBrowserSession(
        conversation_id="c1",
        slot=0,
        netns=netns,
        bundle_dir="/nonexistent/agentcore_browser_test_bundle",
        container_id=_CID,
        runsc_path="runsc",
        runtime_root="/tmp/agentcore-test-root",
        process=process,  # type: ignore[arg-type]
        channel=channel,  # type: ignore[arg-type]
    )


def _record_runsc(session) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    async def _rec(*args: str) -> None:
        calls.append(args)

    session._runsc_cmd = _rec
    return calls


@pytest.mark.asyncio
async def test_close_awaits_natural_supervisor_exit_before_kill():
    proc = _FakeProcess(natural_exit=True)
    ch = _FakeChannel()
    netns = _FakeNetns()
    session = _make_session(proc, ch, netns)
    runsc_calls = _record_runsc(session)

    await session.close()

    assert ch.requests == ["close"]  # close RPC sent first
    assert ch.closed is True
    assert proc.waited is True  # waited for the supervisor to exit on its own
    assert proc.killed is False  # clean exit → never SIGKILLed (avoids orphan slow-delete)
    assert runsc_calls == [
        ("kill", _CID, "SIGKILL"),
        ("delete", "--force", _CID),
    ]
    assert netns.torn is True
    assert session.alive is False


@pytest.mark.asyncio
async def test_close_sigkills_wedged_supervisor_after_bounded_wait(monkeypatch):
    monkeypatch.setattr(gs, "_SUPERVISOR_EXIT_TIMEOUT", 0.05)
    proc = _FakeProcess(natural_exit=False)  # supervisor never exits on its own
    ch = _FakeChannel()
    netns = _FakeNetns()
    session = _make_session(proc, ch, netns)
    runsc_calls = _record_runsc(session)

    await asyncio.wait_for(session.close(), timeout=5)

    assert proc.killed is True  # bounded wait elapsed → SIGKILL fallback fired
    assert runsc_calls == [
        ("kill", _CID, "SIGKILL"),
        ("delete", "--force", _CID),
    ]
    assert netns.torn is True


@pytest.mark.asyncio
async def test_close_is_idempotent():
    proc = _FakeProcess(natural_exit=True)
    ch = _FakeChannel()
    session = _make_session(proc, ch, _FakeNetns())
    runsc_calls = _record_runsc(session)

    await session.close()
    assert len(runsc_calls) == 2
    await session.close()  # second call is a no-op (already torn down)
    assert len(runsc_calls) == 2
    assert ch.requests == ["close"]


@pytest.mark.asyncio
async def test_close_after_driver_crash_still_reclaims_resources():
    """A crashed session (``_alive=False``, teardown never ran) must still tear down fully.

    Idempotency is keyed on ``_closed``, not ``_alive`` — otherwise the netns/veth, the
    concurrency slot, the runsc container and the bundle dir of a crashed driver leak until
    process exit (they are host-side resources; the crash only killed the RPC channel).
    """
    proc = _FakeProcess(natural_exit=True)
    ch = _FakeChannel()
    netns = _FakeNetns()
    session = _make_session(proc, ch, netns)
    runsc_calls = _record_runsc(session)

    session._alive = False  # driver crash marked the session dead before any teardown

    await session.close()

    assert ch.requests == []  # dead channel: no close RPC attempted
    assert ch.closed is True
    assert runsc_calls == [
        ("kill", _CID, "SIGKILL"),
        ("delete", "--force", _CID),
    ]
    assert netns.torn is True

    await session.close()  # still idempotent afterwards
    assert len(runsc_calls) == 2


@pytest.mark.asyncio
async def test_run_runsc_bounded_abandons_wedged_runsc(monkeypatch):
    """A wedged `runsc` is abandoned after the bound (child killed), never blocking the caller."""
    monkeypatch.setattr(gs, "_RUNSC_CMD_TIMEOUT", 0.05)
    killed = {"value": False}

    class _WedgedProc:
        returncode = None

        def __init__(self) -> None:
            self._exited = asyncio.Event()

        async def wait(self) -> int:
            await self._exited.wait()
            return -9

        def kill(self) -> None:
            killed["value"] = True
            self._exited.set()

    async def _fake_exec(*_args, **_kwargs):
        return _WedgedProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    await asyncio.wait_for(
        gs._run_runsc_bounded("runsc", "/tmp/root", "delete", "--force", _CID), timeout=5
    )
    assert killed["value"] is True  # bounded wait elapsed → wedged runsc child killed


@pytest.mark.asyncio
async def test_run_runsc_bounded_swallows_spawn_failure(monkeypatch):
    """A missing/unspawnable runsc must not raise out of best-effort teardown."""

    async def _boom(*_args, **_kwargs):
        raise FileNotFoundError("runsc not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)

    await gs._run_runsc_bounded("runsc", "/tmp/root", "delete", "--force", _CID)


def test_cgroup_subtree_control_writable_missing():
    assert gs.cgroup_subtree_control_writable(Path("/no/such/cgroup.subtree_control")) is True


def test_cgroup_subtree_control_writable_ro(tmp_path, monkeypatch):
    path = tmp_path / "cgroup.subtree_control"
    path.write_text("")
    monkeypatch.setattr(gs.os, "access", lambda _path, _mode: False)
    assert gs.cgroup_subtree_control_writable(path) is False


def test_cgroup_subtree_control_writable_rw(tmp_path, monkeypatch):
    path = tmp_path / "cgroup.subtree_control"
    path.write_text("")
    monkeypatch.setattr(gs.os, "access", lambda _path, _mode: True)
    assert gs.cgroup_subtree_control_writable(path) is True


def test_ignore_browser_cgroups_reason_matrix():
    assert gs.ignore_browser_cgroups_reason(configured=True, writable=True) == "configured"
    assert (
        gs.ignore_browser_cgroups_reason(configured=False, writable=False)
        == "cgroup_subtree_control_unwritable"
    )
    assert gs.ignore_browser_cgroups_reason(configured=False, writable=True) is None


def test_build_browser_runsc_cmd_toggles_ignore_cgroups():
    kwargs = {
        "runsc_path": "runsc",
        "runtime_root": "/data/sandbox",
        "bundle_dir": "/b",
        "container_id": "c1",
    }
    plain = gs.build_browser_runsc_cmd(**kwargs, ignore_cgroups=False)
    flagged = gs.build_browser_runsc_cmd(**kwargs, ignore_cgroups=True)
    assert "--ignore-cgroups" not in plain
    assert "--ignore-cgroups" in flagged
    assert plain[:3] == ["runsc", "--platform=systrap", "--network=sandbox"]


def test_stderr_preview_keeps_tail():
    buf = ("head-" + ("x" * 80) + "-TAILMARK").encode()
    text = gs.stderr_preview(buf, limit=12)
    assert text == "x" * 3 + "-TAILMARK"
    assert gs.stderr_preview(b"  short  ") == "short"


_REAL_CREATE_SUBPROCESS_EXEC = asyncio.create_subprocess_exec


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


async def _dying_runsc_exec(*argv, **kwargs):
    """Stand-in for ``runsc run``: write the prod cgroup error to stderr and exit."""
    if "run" in argv and any(str(a).startswith("--bundle=") for a in argv):
        return await _REAL_CREATE_SUBPROCESS_EXEC(
            sys.executable,
            "-c",
            (
                "import sys;"
                "sys.stderr.write("
                "'cannot set up cgroup for root: open /sys/fs/cgroup/"
                "cgroup.subtree_control: read-only file system\\n');"
                "sys.exit(128)"
            ),
            stdin=kwargs.get("stdin", asyncio.subprocess.PIPE),
            stdout=kwargs.get("stdout", asyncio.subprocess.PIPE),
            stderr=kwargs.get("stderr", asyncio.subprocess.PIPE),
            limit=kwargs.get("limit"),
        )
    return await _REAL_CREATE_SUBPROCESS_EXEC(
        sys.executable,
        "-c",
        "raise SystemExit(0)",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


def _install_open_mocks(monkeypatch, *, writable: bool, configured: bool, exec_fn):
    monkeypatch.setattr(gs, "_IS_LINUX", True)
    monkeypatch.setattr(gs.settings, "browser_sandbox_ignore_cgroups", configured)
    monkeypatch.setattr(gs, "cgroup_subtree_control_writable", lambda: writable)

    async def _proxy():
        return _FakeProxy()

    monkeypatch.setattr(gs, "ensure_browser_proxy", _proxy)
    monkeypatch.setattr(gs, "SessionNetns", _OpenFakeNetns)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_fn)
    gs._used_slots.clear()


@pytest.mark.asyncio
async def test_open_session_auto_ignores_ro_cgroup_and_logs_stderr(monkeypatch, tmp_path):
    captured: list[list[str]] = []

    async def _exec(*argv, **kwargs):
        captured.append([str(a) for a in argv])
        return await _dying_runsc_exec(*argv, **kwargs)

    _install_open_mocks(monkeypatch, writable=False, configured=False, exec_fn=_exec)
    request = BrowserSessionRequest(conversation_id="c-ro")

    with (
        capture_logs() as logs,
        pytest.raises(BrowserSessionError, match="RpcChannelClosedError"),
    ):
        await gs.open_gvisor_browser_session(
            request, runsc_path="runsc", runtime_root=str(tmp_path / "rt")
        )

    run_argv = next(a for a in captured if "run" in a and any(x.startswith("--bundle=") for x in a))
    assert "--ignore-cgroups" in run_argv
    events = {row["event"]: row for row in logs if "event" in row}
    assert events["browser.cgroup_unwritable_ignore"]["reason"] == (
        "cgroup_subtree_control_unwritable"
    )
    failed = events["browser.session_open_failed"]
    assert failed["conversation_id"] == "c-ro"
    assert "subtree_control" in failed["stderr_preview"]
    assert failed["ignore_cgroups"] is True
    assert failed["error_type"] == "RpcChannelClosedError"


@pytest.mark.asyncio
async def test_open_session_keeps_cgroups_when_writable(monkeypatch, tmp_path):
    captured: list[list[str]] = []

    async def _exec(*argv, **kwargs):
        captured.append([str(a) for a in argv])
        return await _dying_runsc_exec(*argv, **kwargs)

    _install_open_mocks(monkeypatch, writable=True, configured=False, exec_fn=_exec)

    with capture_logs() as logs, pytest.raises(BrowserSessionError):
        await gs.open_gvisor_browser_session(
            BrowserSessionRequest(conversation_id="c-rw"),
            runsc_path="runsc",
            runtime_root=str(tmp_path / "rt"),
        )

    run_argv = next(a for a in captured if "run" in a and any(x.startswith("--bundle=") for x in a))
    assert "--ignore-cgroups" not in run_argv
    assert not any(row.get("event") == "browser.cgroup_unwritable_ignore" for row in logs)
