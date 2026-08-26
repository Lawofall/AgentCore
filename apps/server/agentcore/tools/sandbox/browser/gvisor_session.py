"""Long-lived Chromium inside the workspace cloud-desk guest.

Ties together:
- the workspace desk guest (``gvisor.attach_workspace_desk``);
- the process-wide SSRF filter proxy (``proxy``);
- ``sandboxd exec`` stdio of the in-guest ``driver`` (JSON-RPC);
- base64 keyframes returned inline and surfaced as ``BrowserCommandResult.frame``.

The API process never execs ``runsc`` or ``ip``. Open is exec-into-desk; close
reclaims only the driver / Chromium (kill the exec track id). Never kill/delete
the desk guest and never tear down the packaging netns.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.tools.sandbox.browser.netns import (
    EGRESS_UNAVAILABLE_CODE,
    is_netns_capability_error,
)
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


def browser_sessions_supported() -> bool:
    """True only where a real gVisor browser sandbox can run (Linux)."""
    return _IS_LINUX


def _driver_env(
    *,
    proxy_url: str,
    width: int,
    height: int,
    jpeg_quality: int,
) -> list[str]:
    """Exec env for the driver. Must not inherit the desk packaging HTTP_PROXY."""
    browsers = settings.browser_playwright_browsers_path
    return [
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "HOME=/tmp",
        "LANG=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1",
        f"PLAYWRIGHT_BROWSERS_PATH={browsers}",
        f"BROWSER_PROXY={proxy_url}",
        f"BROWSER_WIDTH={width}",
        f"BROWSER_HEIGHT={height}",
        f"BROWSER_JPEG_Q={jpeg_quality}",
        "HTTP_PROXY=",
        "HTTPS_PROXY=",
        "http_proxy=",
        "https_proxy=",
        "NO_PROXY=localhost,127.0.0.1,::1",
        "no_proxy=localhost,127.0.0.1,::1",
    ]


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
    """One conversation's long-lived Chromium inside the workspace desk guest."""

    def __init__(
        self,
        *,
        conversation_id: str,
        slot: int,
        exec_id: str,
        desk_container_id: str,
        driver_script: Path | None,
        stdio: Any,
        channel: StdioRpcChannel,
        client: SandboxdClient,
    ) -> None:
        self.conversation_id = conversation_id
        self.created_at = time.time()
        self.last_used = self.created_at
        self._slot = slot
        self._exec_id = exec_id
        self._desk_container_id = desk_container_id
        self._driver_script = driver_script
        self._stdio = stdio
        self._channel = channel
        self._client = client
        self._alive = True
        # Teardown-idempotency flag, SEPARATE from ``_alive``: a driver crash marks the
        # session dead (``_alive=False``) but the exec process / slot / driver script
        # still exist and MUST be reclaimed by close(). Never tear down the desk.
        self._closed = False
        self._frame_listener: BrowserFrameListener | None = None
        self._screencast_on = False

    @property
    def alive(self) -> bool:
        return self._alive and not getattr(self._stdio, "_closed", False)

    @property
    def desk_container_id(self) -> str:
        return self._desk_container_id

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
            from agentcore.tools.sandbox.gvisor import touch_desk_by_container

            touch_desk_by_container(self._desk_container_id)
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
        # Idempotency is keyed on ``_closed``, NOT ``_alive``: a crashed driver
        # still holds the exec process and concurrency slot.
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
            await self._client.kill(self._exec_id)
        with contextlib.suppress(Exception):
            await self._stdio.aclose()
        await _free_slot(self._slot)
        if self._driver_script is not None:
            with contextlib.suppress(Exception):
                self._driver_script.unlink(missing_ok=True)
        logger.info(
            "browser.session_closed",
            conversation_id=self.conversation_id,
            slot=self._slot,
            exec_id=self._exec_id,
            desk_container_id=self._desk_container_id,
        )


def _log_session_open_failed(*, conversation_id: str, exc: BaseException) -> None:
    logger.warning(
        "browser.session_open_failed",
        conversation_id=conversation_id,
        error=str(exc)[:300],
        error_type=type(exc).__name__,
    )


def _as_browser_launch_error(exc: BaseException) -> BrowserSessionError:
    if isinstance(exc, BrowserSessionError):
        return exc
    details = getattr(exc, "details", None)
    detail_code = details.get("code") if isinstance(details, dict) else None
    code = detail_code or getattr(exc, "code", None)
    if code == EGRESS_UNAVAILABLE_CODE or is_netns_capability_error(exc):
        return BrowserSessionError(
            "云端浏览器沙箱网络隔离不可用（netns 创建失败），"
            "browser_* 本回合不可用；请改用 web_search / read_url 等非浏览器路径。",
            code=EGRESS_UNAVAILABLE_CODE,
        )
    text = str(exc)
    lowered = text.lower()
    if "netns" in lowered or "egress" in lowered or "出网" in text:
        return BrowserSessionError(
            "云端浏览器沙箱网络隔离不可用（netns 创建失败），"
            "browser_* 本回合不可用；请改用 web_search / read_url 等非浏览器路径。",
            code=EGRESS_UNAVAILABLE_CODE,
        )
    return BrowserSessionError(f"浏览器会话启动失败：{type(exc).__name__}: {exc}")


async def open_gvisor_browser_session(
    request: BrowserSessionRequest,
    *,
    runsc_path: str,
    runtime_root: str,
) -> GVisorBrowserSession:
    """Exec the browser driver into the conversation workspace's desk guest.

    Raises :class:`BrowserSessionsBusyError` at the slot cap and
    :class:`BrowserSessionError` on any launch / handshake failure (cleaning up
    the driver exec first). Never starts a second container. ``runsc_path`` is
    unused (sandboxd owns argv); ``runtime_root`` is forwarded to desk attach.
    """
    del runsc_path
    if not _IS_LINUX:
        raise BrowserSessionError("云端浏览器仅在 Linux + gVisor 环境可用")

    root = (request.workspace_root or "").strip()
    if not root:
        raise BrowserSessionError("云端浏览器需要已挂载的工作区盘（禁止无盘 jail）。")
    workspace_path = Path(root)
    if not workspace_path.is_dir():
        raise BrowserSessionError("云端浏览器需要已挂载的工作区盘（禁止无盘 jail）。")
    workspace = str(workspace_path.resolve())
    data_dir = str(Path(settings.data_dir).resolve())
    if workspace == data_dir:
        raise BrowserSessionError("禁止把整份 DATA_DIR 绑进云桌 guest")

    from agentcore.tools.sandbox.gvisor import attach_workspace_desk

    slot = await _alloc_slot(int(settings.browser_max_sessions))
    client = get_sandboxd_client()
    stdio: Any = None
    exec_id: str | None = None
    driver_script: Path | None = None
    logged_fail = False

    def _note_fail(exc: BaseException) -> None:
        nonlocal logged_fail
        if logged_fail:
            return
        logged_fail = True
        _log_session_open_failed(conversation_id=request.conversation_id, exc=exc)

    try:
        desk = await attach_workspace_desk(workspace, runtime_root=runtime_root)
        proxy = await ensure_browser_proxy()
        proxy_url = f"http://{desk.host_ip}:{proxy.port}"
        driver_name = f"browser_driver_{slot}_{uuid.uuid4().hex[:8]}.py"
        driver_script = desk.scratch_dir / driver_name
        shutil.copy(Path(__file__).parent / "driver.py", driver_script)

        stdio = await client.exec_stdio(
            container_id=desk.container_id,
            argv=["python3", "-u", f"/scratch/{driver_name}"],
            cwd="/workspace",
            env=_driver_env(
                proxy_url=proxy_url,
                width=request.viewport_width,
                height=request.viewport_height,
                jpeg_quality=request.jpeg_quality,
            ),
        )
        exec_id = str(getattr(stdio, "container_id", "") or "")
        if not exec_id:
            raise BrowserSessionError("浏览器会话启动失败：exec stdio missing exec_id")
        if exec_id == f"{desk.container_id}-exec":
            raise BrowserSessionError("浏览器会话启动失败：exec 跟踪 id 与 execute() 撞车")

        channel = StdioRpcChannel(write=stdio.write, readline=stdio.readline)
        channel.start()
        ready = await channel.wait_ready(timeout=90)
        if not ready.get("ok", True):
            raise BrowserSessionError("浏览器启动失败：" + str(ready.get("error") or "unknown"))

        session = GVisorBrowserSession(
            conversation_id=request.conversation_id,
            slot=slot,
            exec_id=exec_id,
            desk_container_id=desk.container_id,
            driver_script=driver_script,
            stdio=stdio,
            channel=channel,
            client=client,
        )
        channel.set_event_handler(session._handle_driver_event)
        logger.info(
            "browser.session_opened",
            conversation_id=request.conversation_id,
            slot=slot,
            exec_id=exec_id,
            desk_container_id=desk.container_id,
        )
        return session
    except Exception as exc:  # noqa: BLE001 - any launch failure → explainable error
        _note_fail(exc)
        await _cleanup_partial(stdio, exec_id, slot, client, driver_script)
        raise _as_browser_launch_error(exc) from exc


async def _cleanup_partial(
    stdio: Any,
    exec_id: str | None,
    slot: int,
    client: SandboxdClient,
    driver_script: Path | None,
) -> None:
    if stdio is not None:
        with contextlib.suppress(Exception):
            await stdio.aclose()
    if exec_id:
        with contextlib.suppress(Exception):
            await client.kill(exec_id)
    await _free_slot(slot)
    if driver_script is not None:
        with contextlib.suppress(Exception):
            driver_script.unlink(missing_ok=True)
