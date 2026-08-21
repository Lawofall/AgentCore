"""Long-lived gVisor browser session (Linux-only) — the D9/D10 production wiring.

Ties together the pieces the M0 channel PoC validated end-to-end:
- a per-session isolated netns + veth (``netns``), whose only egress is the proxy;
- the process-wide SSRF filter proxy (``proxy``);
- a runsc container running the in-sandbox ``driver`` forever (via sandboxd);
- a stdio JSON-RPC channel (``rpc``) to it, one command at a time;
- base64 keyframes returned inline and surfaced as ``BrowserCommandResult.frame``.

The API process never execs ``runsc`` or ``ip``: session open is sandboxd
``run_stdio``; close/reaper is stdio 收口 then ``kill`` / ``delete`` +
``netns_teardown``. Tests drive the registry / tools with fake sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

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
from agentcore.tools.sandbox.sandboxd.client import SandboxdClient, get_sandboxd_client

logger = get_logger(__name__)

_IS_LINUX = sys.platform == "linux"

_slot_lock = asyncio.Lock()
_used_slots: set[int] = set()


def build_browser_runsc_cmd(
    *,
    runsc_path: str,
    runtime_root: str,
    bundle_dir: str,
    container_id: str,
) -> list[str]:
    """Shape B argv pin — always ``--ignore-cgroups`` (sandboxd allowlist)."""
    from agentcore.tools.sandbox.sandboxd.argv import build_runsc_cmd

    return build_runsc_cmd(
        runsc_path=runsc_path,
        runtime_root=runtime_root,
        bundle_dir=bundle_dir,
        container_id=container_id,
        shape="net",
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
        stdio: Any,
        channel: StdioRpcChannel,
        client: SandboxdClient,
    ) -> None:
        self.conversation_id = conversation_id
        self.created_at = time.time()
        self.last_used = self.created_at
        self._slot = slot
        self._netns = netns
        self._bundle_dir = bundle_dir
        self._container_id = container_id
        self._stdio = stdio
        self._channel = channel
        self._client = client
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
        return self._alive and not getattr(self._stdio, "_closed", False)

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
            timeout=15.0,
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
            await self._request("stop_screencast", {}, timeout=15.0, touch=False)
        self._screencast_on = False

    async def close(self) -> None:
        # Idempotency is keyed on ``_closed``, NOT ``_alive``: a session whose driver crashed
        # (``_alive=False``, e.g. RPC channel death) has never run teardown — its netns/veth,
        # concurrency slot, runsc container and bundle dir are all still allocated and must be
        # reclaimed here, else they leak until process exit.
        if self._closed:
            return
        self._closed = True
        was_alive = self._alive
        self._alive = False
        if was_alive:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._channel.request("close", {}, timeout=5), timeout=6)
        await self._channel.aclose()
        with contextlib.suppress(Exception):
            await self._stdio.aclose()
        with contextlib.suppress(Exception):
            await self._client.kill(self._container_id)
        with contextlib.suppress(Exception):
            await self._client.delete(self._container_id, force=True)
        await self._netns.teardown()
        await _free_slot(self._slot)
        shutil.rmtree(self._bundle_dir, ignore_errors=True)
        logger.info("browser.session_closed", conversation_id=self.conversation_id, slot=self._slot)


def _log_session_open_failed(*, conversation_id: str, exc: BaseException) -> None:
    logger.warning(
        "browser.session_open_failed",
        conversation_id=conversation_id,
        error=str(exc)[:300],
        error_type=type(exc).__name__,
    )


async def open_gvisor_browser_session(
    request: BrowserSessionRequest,
    *,
    runsc_path: str,
    runtime_root: str,
) -> GVisorBrowserSession:
    """Launch a long-lived browser sandbox for one conversation.

    Raises :class:`BrowserSessionsBusyError` at the slot cap and
    :class:`BrowserSessionError` on any launch / handshake failure (cleaning up
    every partial resource first). ``runsc_path`` / ``runtime_root`` match the
    provider factory signature; sandboxd owns the actual runsc argv.
    """
    _ = runsc_path
    if not _IS_LINUX:
        raise BrowserSessionError("云端浏览器仅在 Linux + gVisor 环境可用")

    proxy = await ensure_browser_proxy()
    slot = await _alloc_slot(int(settings.browser_max_sessions))
    netns = SessionNetns(slot=slot, subnet_base=settings.browser_veth_subnet_base)
    # Bundles must live on the DATA_DIR volume (same as execute()): sandboxd
    # rejects paths outside runtime_root, and container /tmp breaks runsc mkdir.
    root = Path(runtime_root)
    root.mkdir(parents=True, exist_ok=True)
    bundle_dir = tempfile.mkdtemp(prefix="agentcore_browser_", dir=str(root))
    container_id = f"agentcore-browser-{uuid.uuid4().hex[:12]}"
    client = get_sandboxd_client()
    stdio: Any = None
    logged_fail = False

    def _note_fail(exc: BaseException) -> None:
        nonlocal logged_fail
        if logged_fail:
            return
        logged_fail = True
        _log_session_open_failed(conversation_id=request.conversation_id, exc=exc)

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

        stdio = await client.run_stdio(
            bundle_dir=bundle_dir,
            container_id=container_id,
            netns_path=netns.netns_path,
        )

        channel = StdioRpcChannel(write=stdio.write, readline=stdio.readline)
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
            stdio=stdio,
            channel=channel,
            client=client,
        )
        # Route driver-INITIATED event lines (M1 live frames) into the session.
        channel.set_event_handler(session._handle_driver_event)
        logger.info("browser.session_opened", conversation_id=request.conversation_id, slot=slot)
        return session
    except BrowserSessionError as exc:
        _note_fail(exc)
        await _cleanup_partial(stdio, netns, slot, bundle_dir, container_id, client)
        raise
    except Exception as exc:  # noqa: BLE001 - any launch failure → explainable error
        _note_fail(exc)
        await _cleanup_partial(stdio, netns, slot, bundle_dir, container_id, client)
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
    stdio: Any,
    netns: SessionNetns,
    slot: int,
    bundle_dir: str,
    container_id: str,
    client: SandboxdClient,
) -> None:
    if stdio is not None:
        with contextlib.suppress(Exception):
            await stdio.aclose()
    with contextlib.suppress(Exception):
        await client.kill(container_id)
    with contextlib.suppress(Exception):
        await client.delete(container_id, force=True)
    with contextlib.suppress(Exception):
        await netns.teardown()
    await _free_slot(slot)
    shutil.rmtree(bundle_dir, ignore_errors=True)
