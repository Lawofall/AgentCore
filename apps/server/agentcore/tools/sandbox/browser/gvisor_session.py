"""Long-lived gVisor browser session (Linux-only) — the D9/D10 production wiring.

Ties together the pieces the M0 channel PoC validated end-to-end:
- a per-session isolated netns + veth (``netns``), whose only egress is the proxy;
- the process-wide SSRF filter proxy (``proxy``);
- a runsc container running the in-sandbox ``driver`` forever;
- a stdio JSON-RPC channel (``rpc``) to it, one command at a time;
- base64 keyframes returned inline and surfaced as ``BrowserCommandResult.frame``.

Only executes under a real gVisor deploy on Linux. Tests drive the registry / tools
with fake sessions, so nothing here runs off-Linux / without runsc.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.tools.sandbox.browser.netns import (
    EGRESS_UNAVAILABLE_CODE,
    NetnsError,
    SessionNetns,
    is_netns_capability_error,
)
from agentcore.tools.sandbox.browser.oci import build_browser_oci
from agentcore.tools.sandbox.browser.protocol import (
    BrowserCommand,
    BrowserCommandResult,
    BrowserDriverCrashedError,
    BrowserFrameListener,
    BrowserSessionError,
    BrowserSessionRequest,
    BrowserSessionsBusyError,
)
from agentcore.tools.sandbox.browser.proxy import ensure_browser_proxy
from agentcore.tools.sandbox.browser.rpc import (
    RpcChannelClosedError,
    RpcError,
    StdioRpcChannel,
    extract_frame,
)

logger = get_logger(__name__)

_IS_LINUX = sys.platform == "linux"
# Large line limit so a base64 keyframe (≤512KB → ~700KB) fits in one readline.
_STREAM_LIMIT = 8 * 1024 * 1024
# CDP screencast start/stop are quick control calls — detect a wedged driver fast rather
# than waiting the full per-command (navigation) deadline.
_SCREENCAST_CONTROL_TIMEOUT = 15.0
# After a `close` RPC the driver tears down Chromium and exits, and the `runsc run` supervisor
# follows within ~1s. Wait this bounded window for that clean exit before falling back to
# SIGKILL: killing the supervisor first orphans the sandbox into runsc's slow force-delete path
# (~120s observed in the gVisor smoke), whereas a clean exit lets `runsc delete` finish fast.
_SUPERVISOR_EXIT_TIMEOUT = 10.0
# Teardown paths (session close, reaper loop, lifespan shutdown, delete-conversation API) await
# runsc synchronously, so a wedged `runsc` must never block them forever. Abandon the wait after
# this bound (killing the runsc child) with a warning; a legitimately slow force-delete still
# completes within it.
_RUNSC_CMD_TIMEOUT = 180.0
# Docker's default cgroup2 mount is read-only inside the api container. Non-rootless
# runsc then dies at create (``subtree_control: read-only file system``) and the host
# only sees stdout EOF → RpcChannelClosedError. Keep a tail of runsc stderr so that
# failure is diagnosable; drain continuously so a chatty driver cannot fill the pipe.
_CGROUP_SUBTREE_CONTROL = Path("/sys/fs/cgroup/cgroup.subtree_control")
_STDERR_KEEP = 8 * 1024
_STDERR_PREVIEW = 1500
_STDERR_DRAIN_TIMEOUT = 1.0

_slot_lock = asyncio.Lock()
_used_slots: set[int] = set()


def cgroup_subtree_control_writable(path: Path | None = None) -> bool:
    """True when runsc can write cgroup v2 ``subtree_control``.

    A missing path (Windows / cgroup v1) is not the Docker-RO case — return True so
    we still apply session OCI limits unless configured otherwise or the v2 file
    exists and is not writable.
    """
    target = path if path is not None else _CGROUP_SUBTREE_CONTROL
    try:
        if not target.exists():
            return True
        return os.access(target, os.W_OK)
    except OSError:
        return False


def ignore_browser_cgroups_reason(
    *, configured: bool, writable: bool | None = None
) -> str | None:
    """Why ``--ignore-cgroups`` should be added, or ``None`` to apply OCI limits."""
    if configured:
        return "configured"
    if writable is None:
        writable = cgroup_subtree_control_writable()
    if not writable:
        return "cgroup_subtree_control_unwritable"
    return None


def build_browser_runsc_cmd(
    *,
    runsc_path: str,
    runtime_root: str,
    bundle_dir: str,
    container_id: str,
    ignore_cgroups: bool,
) -> list[str]:
    cmd = [runsc_path, "--platform=systrap", "--network=sandbox"]
    if ignore_cgroups:
        cmd.append("--ignore-cgroups")
    cmd += [f"--root={runtime_root}", "run", f"--bundle={bundle_dir}", container_id]
    return cmd


def stderr_preview(buf: bytes | bytearray, *, limit: int = _STDERR_PREVIEW) -> str:
    text = bytes(buf).decode("utf-8", errors="replace").strip()
    if len(text) > limit:
        return text[-limit:]
    return text


async def _drain_stderr(stream: asyncio.StreamReader, buf: bytearray) -> None:
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            buf.extend(chunk)
            overflow = len(buf) - _STDERR_KEEP
            if overflow > 0:
                del buf[:overflow]
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def _stderr_preview_after(task: asyncio.Task | None, buf: bytearray) -> str:
    if task is not None:
        with contextlib.suppress(TimeoutError, asyncio.CancelledError, Exception):
            await asyncio.wait_for(asyncio.shield(task), timeout=_STDERR_DRAIN_TIMEOUT)
    return stderr_preview(buf)


async def _cancel_stderr_task(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def _log_session_open_failed(
    *,
    conversation_id: str,
    exc: BaseException,
    process: asyncio.subprocess.Process | None,
    stderr_task: asyncio.Task | None,
    stderr_buf: bytearray,
    ignore_reason: str | None,
) -> None:
    preview = await _stderr_preview_after(stderr_task, stderr_buf)
    rc = process.returncode if process is not None else None
    if process is not None and rc is None:
        with contextlib.suppress(TimeoutError, Exception):
            await asyncio.wait_for(process.wait(), timeout=0.2)
        rc = process.returncode
    logger.warning(
        "browser.session_open_failed",
        conversation_id=conversation_id,
        error=str(exc)[:300],
        error_type=type(exc).__name__,
        stderr_preview=preview,
        returncode=rc,
        ignore_cgroups=bool(ignore_reason),
        ignore_reason=ignore_reason or "",
    )


def browser_sessions_supported() -> bool:
    """True only where a real gVisor browser sandbox can run (Linux)."""
    return _IS_LINUX


async def _alloc_slot(max_slots: int) -> int:
    async with _slot_lock:
        for i in range(max_slots):
            if i not in _used_slots:
                _used_slots.add(i)
                return i
    raise BrowserSessionsBusyError(
        f"云端浏览器会话已满（并发上限 {max_slots}）。请稍后重试，或先结束其它会话。"
    )


async def _free_slot(slot: int) -> None:
    async with _slot_lock:
        _used_slots.discard(slot)


class GVisorBrowserSession:
    """One conversation's long-lived Chromium inside a runsc sandbox."""

    def __init__(
        self,
        *,
        conversation_id: str,
        slot: int,
        netns: SessionNetns,
        bundle_dir: str,
        container_id: str,
        runsc_path: str,
        runtime_root: str,
        process: asyncio.subprocess.Process,
        channel: StdioRpcChannel,
        stderr_task: asyncio.Task | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        self.created_at = time.time()
        self.last_used = self.created_at
        self._slot = slot
        self._netns = netns
        self._bundle_dir = bundle_dir
        self._container_id = container_id
        self._runsc = runsc_path
        self._runtime_root = runtime_root
        self._process = process
        self._channel = channel
        self._stderr_task = stderr_task
        self._alive = True
        # Teardown-idempotency flag, SEPARATE from ``_alive``: a driver crash marks the
        # session dead (``_alive=False``) but its host-side resources (netns / veth / slot /
        # runsc container / bundle dir) still exist and MUST be reclaimed by close().
        self._closed = False
        # Live screencast (M1 · D14): the hub sets this to fan driver-pushed frames out to
        # viewers. The channel routes driver ``live_frame`` event lines to _handle_driver_event.
        self._frame_listener: BrowserFrameListener | None = None
        self._screencast_on = False

    @property
    def alive(self) -> bool:
        return self._alive and self._process.returncode is None

    async def _request(
        self, action: str, args: dict, *, timeout: float, touch: bool
    ) -> dict:
        """Send one command; map a broken channel to a crashed session.

        ``touch`` bumps ``last_used`` (idle-TTL) for AI tool activity; screencast control is
        viewer activity (guarded by the registry's watch check instead), so it passes False.
        """
        if not self.alive:
            raise BrowserDriverCrashedError("浏览器会话已失效")
        if touch:
            self.last_used = time.time()
        try:
            return await self._channel.request(action, args, timeout=timeout)
        except (RpcChannelClosedError, RpcError) as exc:
            self._alive = False
            raise BrowserDriverCrashedError(str(exc)) from exc

    async def send(self, command: BrowserCommand) -> BrowserCommandResult:
        resp = await self._request(
            command.action,
            command.args,
            timeout=float(settings.browser_command_timeout_seconds),
            touch=True,
        )
        frame = extract_frame(resp)
        ok = bool(resp.pop("ok", False))
        error = resp.pop("error", None)
        resp.pop("id", None)
        return BrowserCommandResult(ok=ok, data=resp, error=error, frame=frame)

    def set_frame_listener(self, listener: BrowserFrameListener | None) -> None:
        self._frame_listener = listener

    def _handle_driver_event(self, msg: dict) -> None:
        """Channel callback for driver-INITIATED event lines (currently ``live_frame``).

        Runs on the channel read task; must stay non-blocking (the listener only enqueues).
        """
        if msg.get("event") == "live_frame":
            listener = self._frame_listener
            if listener is not None:
                listener(
                    {
                        "frame_b64": msg.get("frame_b64") or "",
                        "width": int(msg.get("width") or 0),
                        "height": int(msg.get("height") or 0),
                    }
                )

    async def start_screencast(self) -> None:
        resp = await self._request(
            "start_screencast",
            {
                "quality": int(settings.browser_screencast_jpeg_quality),
                "max_width": int(settings.browser_screencast_max_width),
                "max_height": int(settings.browser_screencast_max_height),
                "every_nth_frame": int(settings.browser_screencast_every_nth_frame),
            },
            timeout=_SCREENCAST_CONTROL_TIMEOUT,
            touch=False,
        )
        if not resp.get("ok", False):
            raise BrowserDriverCrashedError(str(resp.get("error") or "screencast start failed"))
        self._screencast_on = True

    async def stop_screencast(self) -> None:
        if not self.alive or not self._screencast_on:
            self._screencast_on = False
            return
        with contextlib.suppress(BrowserDriverCrashedError):
            await self._request(
                "stop_screencast", {}, timeout=_SCREENCAST_CONTROL_TIMEOUT, touch=False
            )
        self._screencast_on = False

    async def close(self) -> None:
        # Idempotency is keyed on ``_closed``, NOT ``_alive``: a session whose driver crashed
        # (``_alive=False``, e.g. RPC channel death) has never run teardown — its netns/veth,
        # concurrency slot, runsc container and bundle dir are all still allocated and must be
        # reclaimed here, else they leak until process exit.
        if self._closed:
            return
        self._closed = True
        await _cancel_stderr_task(self._stderr_task)
        self._stderr_task = None
        was_alive = self._alive
        self._alive = False
        if was_alive:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._channel.request("close", {}, timeout=5), timeout=6)
        await self._channel.aclose()
        # Let the `runsc run` supervisor exit on its own now the driver has processed `close`
        # and torn Chromium down. SIGKILLing it first orphans the sandbox and forces runsc's
        # slow force-delete path; a clean exit lets `runsc delete` finish fast. Fall back to
        # SIGKILL only if the supervisor is wedged past the bounded window.
        if not await self._await_process_exit(_SUPERVISOR_EXIT_TIMEOUT):
            with contextlib.suppress(ProcessLookupError):
                if self._process.returncode is None:
                    self._process.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._process.wait(), timeout=5)
        await self._runsc_cmd("kill", self._container_id, "SIGKILL")
        await self._runsc_cmd("delete", "--force", self._container_id)
        await self._netns.teardown()
        await _free_slot(self._slot)
        shutil.rmtree(self._bundle_dir, ignore_errors=True)
        logger.info("browser.session_closed", conversation_id=self.conversation_id, slot=self._slot)

    async def _await_process_exit(self, timeout: float) -> bool:
        """Wait up to ``timeout`` for the ``runsc run`` supervisor to exit; True if it did."""
        if self._process.returncode is not None:
            return True
        try:
            await asyncio.wait_for(self._process.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def _runsc_cmd(self, *args: str) -> None:
        await _run_runsc_bounded(self._runsc, self._runtime_root, *args)


async def _run_runsc_bounded(runsc_path: str, runtime_root: str, *args: str) -> None:
    """Run one ``runsc`` subcommand best-effort with a bounded wait — never raises.

    Every teardown caller (session close, reaper loop, lifespan shutdown, delete-conversation)
    awaits runsc synchronously, so a wedged ``runsc`` must not block them forever: after
    ``_RUNSC_CMD_TIMEOUT`` the wait is abandoned with a warning and the runsc child is killed. A
    legitimately slow force-delete still completes within the bound. Any other failure (e.g.
    runsc missing) is swallowed so teardown stays best-effort and idempotent.
    """
    with contextlib.suppress(Exception):
        proc = await asyncio.create_subprocess_exec(
            runsc_path,
            f"--root={runtime_root}",
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=_RUNSC_CMD_TIMEOUT)
        except TimeoutError:
            logger.warning(
                "browser.runsc_cmd_timeout",
                argv=" ".join(args),
                timeout_seconds=_RUNSC_CMD_TIMEOUT,
            )
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)


async def open_gvisor_browser_session(
    request: BrowserSessionRequest,
    *,
    runsc_path: str,
    runtime_root: str,
) -> GVisorBrowserSession:
    """Launch a long-lived browser sandbox for one conversation.

    Raises :class:`BrowserSessionsBusyError` at the slot cap and
    :class:`BrowserSessionError` on any launch / handshake failure (cleaning up
    every partial resource first).
    """
    if not _IS_LINUX:
        raise BrowserSessionError("云端浏览器仅在 Linux + gVisor 环境可用")

    proxy = await ensure_browser_proxy()
    slot = await _alloc_slot(int(settings.browser_max_sessions))
    netns = SessionNetns(slot=slot, subnet_base=settings.browser_veth_subnet_base)
    bundle_dir = tempfile.mkdtemp(prefix="agentcore_browser_")
    container_id = f"agentcore-browser-{uuid.uuid4().hex[:12]}"
    process: asyncio.subprocess.Process | None = None
    stderr_task: asyncio.Task | None = None
    stderr_buf = bytearray()
    ignore_reason = ignore_browser_cgroups_reason(
        configured=bool(settings.browser_sandbox_ignore_cgroups)
    )
    if ignore_reason == "cgroup_subtree_control_unwritable":
        logger.warning("browser.cgroup_unwritable_ignore", reason=ignore_reason)
    logged_fail = False

    async def _note_fail(exc: BaseException) -> None:
        nonlocal logged_fail
        if logged_fail:
            return
        logged_fail = True
        await _log_session_open_failed(
            conversation_id=request.conversation_id,
            exc=exc,
            process=process,
            stderr_task=stderr_task,
            stderr_buf=stderr_buf,
            ignore_reason=ignore_reason,
        )
        await _cancel_stderr_task(stderr_task)

    try:
        try:
            await netns.setup()
        except NetnsError as exc:
            raise BrowserSessionError(
                "云端浏览器沙箱网络隔离不可用（netns 创建失败），"
                "browser_* 本回合不可用；请改用 web_search / read_url 等非浏览器路径。",
                code=EGRESS_UNAVAILABLE_CODE,
            ) from exc
        scratch = Path(bundle_dir) / "scratch"
        scratch.mkdir()
        (Path(bundle_dir) / "rootfs").mkdir()
        shutil.copy(Path(__file__).parent / "driver.py", scratch / "browser_driver.py")

        proxy_url = f"http://{netns.host_ip}:{proxy.port}"
        config = build_browser_oci(
            scratch_dir=str(scratch.resolve()),
            browsers_path=settings.browser_playwright_browsers_path,
            netns_path=netns.netns_path,
            proxy_url=proxy_url,
            width=request.viewport_width,
            height=request.viewport_height,
            jpeg_quality=request.jpeg_quality,
            memory_limit_mb=int(settings.browser_sandbox_memory_limit_mb),
            pids_limit=int(settings.browser_sandbox_pids_limit),
            cpu_limit=float(settings.browser_sandbox_cpu_limit),
        )
        (Path(bundle_dir) / "config.json").write_text(json.dumps(config), encoding="utf-8")

        cmd = build_browser_runsc_cmd(
            runsc_path=runsc_path,
            runtime_root=runtime_root,
            bundle_dir=bundle_dir,
            container_id=container_id,
            ignore_cgroups=bool(ignore_reason),
        )
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
        )
        if process.stderr is not None:
            stderr_task = asyncio.create_task(
                _drain_stderr(process.stderr, stderr_buf), name="browser-runsc-stderr"
            )

        async def _write(data: bytes) -> None:
            assert process is not None and process.stdin is not None
            process.stdin.write(data)
            await process.stdin.drain()

        assert process.stdout is not None
        channel = StdioRpcChannel(write=_write, readline=process.stdout.readline)
        channel.start()
        ready = await channel.wait_ready(timeout=90)
        if not ready.get("ok", True):
            raise BrowserSessionError("浏览器启动失败：" + str(ready.get("error") or "unknown"))

        session = GVisorBrowserSession(
            conversation_id=request.conversation_id,
            slot=slot,
            netns=netns,
            bundle_dir=bundle_dir,
            container_id=container_id,
            runsc_path=runsc_path,
            runtime_root=runtime_root,
            process=process,
            channel=channel,
            stderr_task=stderr_task,
        )
        # Route driver-INITIATED event lines (M1 live frames) into the session.
        channel.set_event_handler(session._handle_driver_event)
        logger.info("browser.session_opened", conversation_id=request.conversation_id, slot=slot)
        return session
    except BrowserSessionError as exc:
        await _note_fail(exc)
        await _cleanup_partial(
            process, netns, slot, bundle_dir, container_id, runsc_path, runtime_root
        )
        raise
    except Exception as exc:  # noqa: BLE001 - any launch failure → explainable error
        await _note_fail(exc)
        await _cleanup_partial(
            process, netns, slot, bundle_dir, container_id, runsc_path, runtime_root
        )
        if is_netns_capability_error(exc):
            raise BrowserSessionError(
                "云端浏览器沙箱网络隔离不可用（netns 创建失败），"
                "browser_* 本回合不可用；请改用 web_search / read_url 等非浏览器路径。",
                code=EGRESS_UNAVAILABLE_CODE,
            ) from exc
        raise BrowserSessionError(
            f"浏览器会话启动失败：{type(exc).__name__}: {exc}"
        ) from exc


async def _cleanup_partial(
    process: asyncio.subprocess.Process | None,
    netns: SessionNetns,
    slot: int,
    bundle_dir: str,
    container_id: str,
    runsc_path: str,
    runtime_root: str,
) -> None:
    if process is not None:
        with contextlib.suppress(ProcessLookupError):
            if process.returncode is None:
                process.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=5)
    await _run_runsc_bounded(runsc_path, runtime_root, "delete", "--force", container_id)
    with contextlib.suppress(Exception):
        await netns.teardown()
    await _free_slot(slot)
    shutil.rmtree(bundle_dir, ignore_errors=True)
