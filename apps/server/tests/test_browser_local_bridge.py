"""Local Bridge session + execution gate (M1 · C1/C4)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

from agentcore.runtime.browser.desktop_bridge import (
    desktop_bridge_unauthorized,
    ensure_desktop_bridge_health,
    probe_desktop_bridge_sync,
    reset_desktop_bridge_health_for_tests,
    set_desktop_bridge_health_for_tests,
)
from agentcore.runtime.browser.local_session import (
    BRIDGE_UNAUTHORIZED_CODE,
    LocalBridgeSession,
    open_local_bridge_session,
)
from agentcore.runtime.browser.registry import BrowserSessionRegistry
from agentcore.tools.builtin.browser import BrowserTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.browser.protocol import BrowserSessionError, BrowserSessionRequest
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import LocalBackend


class _FakeBridgeServer(ThreadingHTTPServer):
    """Per-test loopback Bridge. CPython's HTTPServer sets allow_reuse_address=1;
    on Windows that is SO_REUSEADDR and lets another xdist worker bind the same
    port — its shutdown then RSTs in-flight 401s into host_unavailable.
    """

    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        self.token = ""
        self.navigations: list[dict] = []
        self.fail_host = False
        self.expire_after_posts: int | None = None
        self.post_count = 0
        self._state_lock = threading.Lock()
        super().__init__(addr, handler)

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _FakeBridgeHandler(BaseHTTPRequestHandler):
    """Minimal DesktopBrowserBridge stand-in for unit tests."""

    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):  # noqa: D401 - silence test noise
        return

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError):
            # Windows urllib closes as soon as it sees 401; the handler flush then
            # raises. Swallow so the accept loop stays up for the next request.
            return

    def _server(self) -> _FakeBridgeServer:
        return self.server  # type: ignore[return-value]

    def _auth_ok(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {self._server().token}"

    def _token_expired(self) -> bool:
        srv = self._server()
        if srv.expire_after_posts is None:
            return False
        with srv._state_lock:
            srv.post_count += 1
            return srv.post_count > srv.expire_after_posts

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if not self._auth_ok():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        if self.path.startswith("/health"):
            self._json(200, {"ok": True, "service": "desktop-browser-bridge"})
            return
        self._json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._auth_ok() or self._token_expired():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "invalid_json"})
            return
        srv = self._server()
        if srv.fail_host:
            self._json(
                503,
                {"ok": False, "error": "host_unavailable: no window", "code": "host_unavailable"},
            )
            return
        if self.path.startswith("/command") or self.path.startswith("/navigate"):
            action = body.get("action") or "navigate"
            args = body.get("args") or body
            url = args.get("url") or body.get("url") or ""
            page_id = body.get("pageId") or body.get("session_id") or ""
            conversation_id = body.get("conversationId") or body.get("conversation_id") or ""
            srv.navigations.append(
                {
                    "pageId": page_id,
                    "conversationId": conversation_id,
                    "action": action,
                    "url": url,
                    "args": args,
                }
            )
            data: dict = {
                "final_url": url or "https://example.com/",
                "title": "Example Domain",
                "http_status": None,
            }
            if action == "screenshot":
                # Non-empty base64 + dims — LocalBridgeSession live poll contract.
                data["frame_b64"] = "Zm9v"  # b"foo"
                data["width"] = 1280
                data["height"] = 800
            self._json(200, {"ok": True, "data": data})
            return
        self._json(404, {"ok": False, "error": "not_found"})


def _wait_bridge_accepting(base: str, token: str, *, timeout_s: float = 2.0) -> None:
    """Block until serve_forever is accepting (bind ≠ accept on Windows)."""
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        req = Request(
            f"{base}/health",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(req, timeout=0.2) as resp:  # noqa: S310 - loopback test fixture
                if resp.status == 200:
                    return
        except (URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(0.01)
    raise RuntimeError(f"fake DesktopBrowserBridge did not accept on {base}: {last}")


@pytest.fixture()
def fake_bridge(monkeypatch):
    reset_desktop_bridge_health_for_tests()
    server = _FakeBridgeServer(("127.0.0.1", 0), _FakeBridgeHandler)
    token = "test-bridge-token-abc"
    server.token = token
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("AGENTCORE_BROWSER_BRIDGE_URL", base)
    monkeypatch.setenv("AGENTCORE_BROWSER_BRIDGE_TOKEN", token)
    try:
        _wait_bridge_accepting(base, token)
        yield {"base": base, "token": token, "server": server}
    finally:
        with contextlib.suppress(Exception):
            server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()
        thread.join(timeout=2)
        reset_desktop_bridge_health_for_tests()


def test_probe_desktop_bridge_health(fake_bridge):
    assert probe_desktop_bridge_sync() is True
    assert ensure_desktop_bridge_health() is True


def test_turn_apply_clears_sticky_false(fake_bridge):
    """B-Arch-3: a failed probe must not pin the process after credentials refresh."""
    from agentcore.runtime.browser import desktop_bridge as db
    from agentcore.runtime.browser.desktop_bridge import apply_desktop_bridge_from_turn

    set_desktop_bridge_health_for_tests(False)
    assert ensure_desktop_bridge_health() is False

    apply_desktop_bridge_from_turn(
        {"baseUrl": fake_bridge["base"], "token": fake_bridge["token"]}
    )
    assert db.desktop_bridge_health() is None
    assert ensure_desktop_bridge_health() is True


def test_turn_apply_null_withholds(fake_bridge):
    from agentcore.runtime.browser.desktop_bridge import (
        apply_desktop_bridge_from_turn,
        desktop_bridge_configured,
    )

    apply_desktop_bridge_from_turn({"baseUrl": fake_bridge["base"], "token": fake_bridge["token"]})
    assert desktop_bridge_configured() is True
    apply_desktop_bridge_from_turn(None)
    assert desktop_bridge_configured() is False
    assert ensure_desktop_bridge_health() is False


@pytest.mark.asyncio
async def test_local_bridge_session_navigate_async(fake_bridge):
    from agentcore.tools.sandbox.browser.protocol import BrowserCommand

    sess = LocalBridgeSession(conversation_id="c1", session_id="sess-local-1")
    result = await sess.send(
        BrowserCommand(action="navigate", args={"url": "https://example.com/"})
    )
    assert result.ok
    assert result.data["final_url"] == "https://example.com/"
    assert fake_bridge["server"].navigations[-1]["pageId"] == "sess-local-1"
    assert fake_bridge["server"].navigations[-1]["action"] == "navigate"
    assert fake_bridge["server"].navigations[-1]["conversationId"] == "c1"


@pytest.mark.asyncio
async def test_local_bridge_rewrites_relative_path_to_workspace(fake_bridge):
    """甲：LocalBridgeSession 相对路径 → workspace:// 再 POST Bridge。"""
    from agentcore.tools.sandbox.browser.protocol import BrowserCommand

    sess = LocalBridgeSession(conversation_id="Conv-ID", session_id="sess-ws")
    result = await sess.send(
        BrowserCommand(action="navigate", args={"url": "site/index.html"})
    )
    assert result.ok
    expected = "workspace://conv.conv-id/site/index.html"
    assert result.data["final_url"] == expected
    assert fake_bridge["server"].navigations[-1]["url"] == expected
    assert fake_bridge["server"].navigations[-1]["args"]["url"] == expected


@pytest.mark.asyncio
async def test_local_bridge_rejects_file_url(fake_bridge):
    from agentcore.tools.sandbox.browser.protocol import BrowserCommand

    before = len(fake_bridge["server"].navigations)
    sess = LocalBridgeSession(conversation_id="c1", session_id="sess-bad")
    result = await sess.send(
        BrowserCommand(action="navigate", args={"url": "file:///tmp/x.html"})
    )
    assert not result.ok
    assert len(fake_bridge["server"].navigations) == before


@pytest.mark.asyncio
async def test_open_local_fails_without_bridge(monkeypatch):
    reset_desktop_bridge_health_for_tests()
    monkeypatch.delenv("AGENTCORE_BROWSER_BRIDGE_URL", raising=False)
    monkeypatch.delenv("AGENTCORE_BROWSER_BRIDGE_TOKEN", raising=False)
    set_desktop_bridge_health_for_tests(None)
    with pytest.raises(BrowserSessionError, match="host_unavailable"):
        await open_local_bridge_session(
            BrowserSessionRequest(conversation_id="c1", host_kind="local", session_id="s1")
        )


@pytest.mark.asyncio
async def test_tool_navigate_via_fake_bridge_updates_registry(fake_bridge, tmp_path: Path):
    set_desktop_bridge_health_for_tests(True)

    async def factory(req: BrowserSessionRequest):
        return await open_local_bridge_session(req)

    reg = BrowserSessionRegistry(factory=factory)
    LocalBackend()
    # LocalBackend may not be a full WorkspaceBackend for write_bytes — use ServerWorkspace
    # for keyframe writes while keeping location=local via a thin wrapper.
    ws = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())

    class _LocalWs:
        location = "local"

        def __getattr__(self, name):
            return getattr(ws, name)

    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="w1",
        backend=_LocalWs(),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c-local",
    )
    tool = BrowserTool(registry=reg)
    result = await tool.execute({"action": "navigate", "url": "https://example.com/"}, ctx)
    assert result.success, result.output
    infos = reg.list_by_conversation("c-local")
    assert len(infos) == 1
    assert infos[0].host_kind == "local"
    assert infos[0].url == "https://example.com/"
    assert infos[0].title == "Example Domain"
    assert fake_bridge["server"].navigations
    assert fake_bridge["server"].navigations[0]["url"] == "https://example.com/"


@pytest.mark.asyncio
async def test_tool_host_unavailable_when_bridge_returns_503(fake_bridge, tmp_path: Path):
    fake_bridge["server"].fail_host = True
    set_desktop_bridge_health_for_tests(True)

    async def factory(req: BrowserSessionRequest):
        return await open_local_bridge_session(req)

    reg = BrowserSessionRegistry(factory=factory)
    ws = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())

    class _LocalWs:
        location = "local"

        def __getattr__(self, name):
            return getattr(ws, name)

    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="w1",
        backend=_LocalWs(),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c-local",
    )
    tool = BrowserTool(registry=reg)
    result = await tool.execute({"action": "navigate", "url": "https://example.com/"}, ctx)
    assert not result.success
    assert result.metadata and result.metadata.get("code") == "host_unavailable"


@pytest.mark.asyncio
async def test_mid_turn_token_expiry_reports_bridge_unauthorized(fake_bridge):
    """长回合洞：navigate 过了、token 中途失效 → snapshot 401。宿主活着，不得报不可用。"""
    from agentcore.tools.sandbox.browser.protocol import BrowserCommand

    set_desktop_bridge_health_for_tests(True)
    fake_bridge["server"].expire_after_posts = 1

    sess = LocalBridgeSession(conversation_id="c1", session_id="sess-expiry")
    first = await sess.send(
        BrowserCommand(action="navigate", args={"url": "https://example.com/"})
    )
    assert first.ok

    second = await sess.send(BrowserCommand(action="snapshot", args={}))
    assert not second.ok
    assert second.data.get("code") == BRIDGE_UNAUTHORIZED_CODE
    assert "host_unavailable" not in (second.error or "")
    # 宿主没挂：会话保持存活，下一回合换新凭证还能接着用这个标签页。
    assert sess.alive


@pytest.mark.asyncio
async def test_connection_refused_stays_host_unavailable(fake_bridge):
    """拆 401 不得殃及真·不可达：连接被拒仍是 host_unavailable，且判死会话。"""
    from agentcore.tools.sandbox.browser.protocol import BrowserCommand

    set_desktop_bridge_health_for_tests(True)
    fake_bridge["server"].shutdown()
    fake_bridge["server"].server_close()

    sess = LocalBridgeSession(conversation_id="c1", session_id="sess-down")
    result = await sess.send(BrowserCommand(action="snapshot", args={}))
    assert not result.ok
    assert result.data.get("code") == "host_unavailable"
    assert not sess.alive


@pytest.mark.asyncio
async def test_probe_401_reports_bridge_unauthorized(fake_bridge, monkeypatch):
    """探活拿到 401 同样是「凭据失效」：宿主活着，会话面不得折叠成 host_unavailable。"""
    from agentcore.tools.sandbox.browser.protocol import BrowserCommand

    assert probe_desktop_bridge_sync() is True
    assert desktop_bridge_unauthorized() is False

    monkeypatch.setenv("AGENTCORE_BROWSER_BRIDGE_TOKEN", "stale-token")
    assert probe_desktop_bridge_sync() is False
    assert desktop_bridge_unauthorized() is True

    sess = LocalBridgeSession(conversation_id="c1", session_id="sess-stale-probe")
    result = await sess.send(BrowserCommand(action="snapshot", args={}))
    assert not result.ok
    assert result.data.get("code") == BRIDGE_UNAUTHORIZED_CODE
    assert "host_unavailable" not in (result.error or "")

    with pytest.raises(BrowserSessionError) as excinfo:
        await open_local_bridge_session(
            BrowserSessionRequest(conversation_id="c1", host_kind="local", session_id="s-stale")
        )
    assert excinfo.value.code == BRIDGE_UNAUTHORIZED_CODE
    assert "host_unavailable" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_tool_mid_turn_401_maps_to_bridge_unauthorized(fake_bridge, tmp_path: Path):
    """工具面 metadata code 决定用户文案：401 必须与 host_unavailable 分开。"""
    set_desktop_bridge_health_for_tests(True)
    fake_bridge["server"].expire_after_posts = 1

    async def factory(req: BrowserSessionRequest):
        return await open_local_bridge_session(req)

    reg = BrowserSessionRegistry(factory=factory)
    ws = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())

    class _LocalWs:
        location = "local"

        def __getattr__(self, name):
            return getattr(ws, name)

    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="w1",
        backend=_LocalWs(),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c-local",
    )
    nav = await BrowserTool(registry=reg).execute(
        {"action": "navigate", "url": "https://example.com/"}, ctx
    )
    assert nav.success, nav.error

    snap = await BrowserTool(registry=reg).execute({"action": "snapshot"}, ctx)
    assert not snap.success
    assert snap.metadata and snap.metadata.get("code") == BRIDGE_UNAUTHORIZED_CODE
    assert "host_unavailable" not in (snap.error or "")


@pytest.mark.asyncio
async def test_local_screencast_start_emits_frames_and_stop_halts(fake_bridge):
    """Hub-style lifecycle: start_screencast polls Bridge screenshot → listener; stop cancels."""
    set_desktop_bridge_health_for_tests(True)
    sess = LocalBridgeSession(
        conversation_id="c1",
        session_id="sess-live-local",
        screencast_interval_s=0.05,
    )
    frames: list[dict] = []
    sess.set_frame_listener(lambda f: frames.append(dict(f)))

    await sess.start_screencast()
    # Wait for at least one poll cycle.
    for _ in range(40):
        if frames:
            break
        await asyncio.sleep(0.05)
    assert frames, "expected at least one live frame from Bridge screenshot poll"
    assert frames[0]["frame_b64"] == "Zm9v"
    assert frames[0]["width"] == 1280
    assert frames[0]["height"] == 800
    assert any(n["action"] == "screenshot" for n in fake_bridge["server"].navigations)
    assert any(
        n["action"] == "screenshot" and n["conversationId"] == "c1"
        for n in fake_bridge["server"].navigations
    )

    before = len(frames)
    await sess.stop_screencast()
    assert sess._screencast_task is None or sess._screencast_task.done()
    shot_at_stop = sum(1 for n in fake_bridge["server"].navigations if n["action"] == "screenshot")
    await asyncio.sleep(0.2)
    assert len(frames) == before
    shot_after = sum(1 for n in fake_bridge["server"].navigations if n["action"] == "screenshot")
    assert shot_after == shot_at_stop  # no further Bridge captures after stop

    await sess.close()


@pytest.mark.asyncio
async def test_local_screencast_via_live_hub_attach_detach(fake_bridge):
    """Attach → local session starts poll; last detach (grace=0) → stop_screencast."""
    from agentcore.runtime.browser.live import BrowserLiveHub
    from agentcore.runtime.events.types import EventType

    set_desktop_bridge_health_for_tests(True)
    sess = LocalBridgeSession(
        conversation_id="c-hub",
        session_id="sess-hub-local",
        screencast_interval_s=0.05,
    )
    hub = BrowserLiveHub(
        session_lookup=lambda cid, sid=None: sess if cid == "c-hub" else None,
        grace_seconds=0.01,
        max_queued_frames=8,
    )
    viewer = await hub.attach("c-hub")
    started = await asyncio.wait_for(viewer.get(), timeout=1.0)
    assert started.type is EventType.BROWSER_LIVE_STATUS
    assert started.payload["state"] == "started"

    frame_ev = None
    for _ in range(40):
        try:
            ev = await asyncio.wait_for(viewer.get(), timeout=0.1)
        except TimeoutError:
            continue
        if ev is not None and ev.type is EventType.BROWSER_LIVE_FRAME:
            frame_ev = ev
            break
    assert frame_ev is not None
    assert frame_ev.payload["frame_b64"] == "Zm9v"
    assert frame_ev.payload["width"] == 1280

    await hub.detach("c-hub", viewer)
    await asyncio.sleep(0.08)  # grace → stop
    assert sess._screencast_task is None or sess._screencast_task.done()
    await sess.close()

