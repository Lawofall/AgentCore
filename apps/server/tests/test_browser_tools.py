"""Browser tools: D11 governance five-dim, cloud/local gate, execute + keyframe caps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.config import settings
from agentcore.core.types import AutonomyPolicy, ToolApproval, ToolCategory, recipe_to_axes
from agentcore.runtime.browser.desktop_bridge import (
    reset_desktop_bridge_health_for_tests,
    set_desktop_bridge_health_for_tests,
)
from agentcore.runtime.browser.keyframes import KeyframeTracker
from agentcore.tools.builtin import (
    browser_execution_enabled_for,
    browser_host_kind_for,
    build_worker_registry,
)
from agentcore.tools.builtin.browser import (
    BROWSER_TOOL_NAMES,
    BrowserTool,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    ToolSurface,
    tool_registration,
)


def _alias(action: str) -> type[BrowserTool]:
    class _Aliased(BrowserTool):
        async def execute(self, arguments, context):  # type: ignore[no-untyped-def]
            args = dict(arguments)
            args.setdefault("action", action)
            return await super().execute(args, context)

    _Aliased.__name__ = f"Browser{action.title()}Tool"
    return _Aliased


BrowserNavigateTool = _alias("navigate")
BrowserClickTool = _alias("click")
BrowserTypeTool = _alias("type")
BrowserScrollTool = _alias("scroll")
BrowserSnapshotTool = _alias("snapshot")
BrowserConsoleTool = _alias("console")
BrowserScreenshotTool = _alias("screenshot")
from agentcore.tools.sandbox.browser.netns import (
    EGRESS_UNAVAILABLE_CODE,
    browser_netns_health,
    set_browser_netns_health_for_tests,
)
from agentcore.tools.sandbox.browser.protocol import (
    BrowserCommandResult,
    BrowserDriverCrashedError,
    BrowserSessionAcquireError,
    BrowserSessionError,
    BrowserSessionsBusyError,
)
from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import LocalBackend

_BROWSER_NAMES = frozenset({"browser"})


def _fail_text(result) -> str:
    """Browser failures put the message in ``error`` only (avoid output+error double)."""
    return result.error or result.output or ""


# -- governance (D11 五维) ------------------------------------------------------
def test_navigate_is_builtin_both():
    reg = tool_registration(BrowserTool)
    schema = BrowserTool().schema
    assert schema.name == "browser"
    assert reg.surface is ToolSurface.BUILTIN
    assert reg.audience == AUDIENCE_BOTH
    assert reg.execution_class is True
    assert reg.browser_class is True
    assert schema.approval is ToolApproval.GRANTABLE
    assert schema.category is ToolCategory.EXECUTION
    assert frozenset({"browser"}) == BROWSER_TOOL_NAMES


def test_interactive_browser_tools_are_builtin_both():
    reg = tool_registration(BrowserTool)
    schema = BrowserTool().schema
    assert reg.surface is ToolSurface.BUILTIN
    assert reg.audience == AUDIENCE_BOTH
    assert reg.browser_class and reg.execution_class
    assert schema.approval is ToolApproval.GRANTABLE


def test_screenshot_is_runtime_worker_only_action():
    reg = tool_registration(BrowserTool)
    schema = BrowserTool().schema
    assert reg.surface is ToolSurface.BUILTIN
    assert reg.audience == AUDIENCE_BOTH
    assert "screenshot" in schema.parameters["properties"]["action"]["enum"]


def test_ceo_registry_holds_interactive_browser_when_include_browser():
    from agentcore.tools.builtin import build_ceo_tool_registry

    off = {s.name for s in build_ceo_tool_registry().list_all()}
    assert "browser" not in off
    assert not (_BROWSER_NAMES & off)

    on = {s.name for s in build_ceo_tool_registry(include_browser=True).list_all()}
    assert on >= _BROWSER_NAMES
    assert "browser_screenshot" not in on
    assert (
        build_ceo_tool_registry(include_browser=True).get("browser").schema.approval
        is ToolApproval.GRANTABLE
    )


# -- cloud / local gate --------------------------------------------------------
def _server_backend(tmp_path: Path) -> ServerWorkspace:
    return ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())


def test_gate_requires_server_plus_gvisor_plus_health(tmp_path, monkeypatch):
    backend = _server_backend(tmp_path)
    assert browser_execution_enabled_for(None) is False
    reset_desktop_bridge_health_for_tests()
    # True local engine: no Bridge + no gVisor → withhold (no fake success).
    monkeypatch.setattr(settings, "gvisor_enabled", False)
    assert browser_execution_enabled_for(LocalBackend()) is False
    assert browser_execution_enabled_for(backend) is False  # no gVisor isolation
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    assert browser_execution_enabled_for(backend) is True
    set_cloud_sandbox_health_for_tests(False)
    assert browser_execution_enabled_for(backend) is False  # probe says unhealthy


def test_gate_withholds_when_browser_netns_unhealthy(tmp_path, monkeypatch):
    """Cloud sandbox ok but netns capability False → do not assemble ``browser``."""
    backend = _server_backend(tmp_path)
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    set_browser_netns_health_for_tests(False)
    assert browser_execution_enabled_for(backend) is False


def test_gate_netns_unprobed_keeps_cloud_health_semantics(tmp_path, monkeypatch):
    """None netns health + cloud healthy → still True (tests / unbooted compatibility)."""
    backend = _server_backend(tmp_path)
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    assert browser_netns_health() is None
    assert browser_execution_enabled_for(backend) is True


def test_gate_local_requires_desktop_bridge_when_no_gvisor(monkeypatch):
    """真·本地引擎：无 gVisor 时仅 Bridge 可装配。"""
    reset_desktop_bridge_health_for_tests()
    monkeypatch.setattr(settings, "gvisor_enabled", False)
    assert browser_execution_enabled_for(LocalBackend()) is False
    assert browser_host_kind_for(LocalBackend()) is None
    set_desktop_bridge_health_for_tests(True)
    assert browser_execution_enabled_for(LocalBackend()) is True
    assert browser_host_kind_for(LocalBackend()) == "local"
    set_desktop_bridge_health_for_tests(False)
    assert browser_execution_enabled_for(LocalBackend()) is False
    assert browser_host_kind_for(LocalBackend()) is None
    reset_desktop_bridge_health_for_tests()


def test_gate_local_bridge_session_falls_back_to_sandbox(monkeypatch):
    """过桥：location=local、无 Bridge、gVisor 健康 → enabled 且 host_kind=sandbox."""
    reset_desktop_bridge_health_for_tests()
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    assert browser_execution_enabled_for(LocalBackend()) is True
    assert browser_host_kind_for(LocalBackend()) == "sandbox"


def test_gate_local_healthy_bridge_prefers_local_over_sandbox(monkeypatch):
    """有健康 Bridge → host_kind=local（即便 gVisor 也健康）。"""
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    set_desktop_bridge_health_for_tests(True)
    assert browser_execution_enabled_for(LocalBackend()) is True
    assert browser_host_kind_for(LocalBackend()) == "local"
    reset_desktop_bridge_health_for_tests()


def test_worker_registry_includes_browser_only_on_gvisor_cloud(tmp_path, monkeypatch):
    backend = _server_backend(tmp_path)
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    names = {s.name for s in build_worker_registry(backend=backend).list_all()}
    assert names >= _BROWSER_NAMES

    # CAUTIOUS (command=ask) withholds the whole execution class (browser included).
    observe = {
        s.name
        for s in build_worker_registry(
            backend=backend, permission_axes=recipe_to_axes(AutonomyPolicy.CAUTIOUS)
        ).list_all()
    }
    assert not (_BROWSER_NAMES & observe)


def test_worker_registry_excludes_browser_without_gvisor(tmp_path, monkeypatch):
    backend = _server_backend(tmp_path)
    monkeypatch.setattr(settings, "gvisor_enabled", False)
    names = {s.name for s in build_worker_registry(backend=backend).list_all()}
    assert not (_BROWSER_NAMES & names)


def test_worker_registry_excludes_browser_on_local_without_bridge_or_gvisor(monkeypatch):
    reset_desktop_bridge_health_for_tests()
    monkeypatch.setattr(settings, "gvisor_enabled", False)
    names = {s.name for s in build_worker_registry(backend=LocalBackend()).list_all()}
    assert not (_BROWSER_NAMES & names)


def test_worker_registry_includes_browser_on_local_bridge_session_sandbox(monkeypatch):
    """过桥无 Bridge + gVisor → worker 装配 ``browser``（host_kind 由工具侧解析为 sandbox）。"""
    reset_desktop_bridge_health_for_tests()
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    names = {s.name for s in build_worker_registry(backend=LocalBackend()).list_all()}
    assert names >= _BROWSER_NAMES
    assert browser_host_kind_for(LocalBackend()) == "sandbox"


def test_worker_registry_includes_browser_on_local_with_bridge(monkeypatch):
    set_desktop_bridge_health_for_tests(True)
    names = {s.name for s in build_worker_registry(backend=LocalBackend()).list_all()}
    assert names >= _BROWSER_NAMES
    assert browser_host_kind_for(LocalBackend()) == "local"
    reset_desktop_bridge_health_for_tests()


# -- execute -------------------------------------------------------------------
class _FakeSession:
    def __init__(self, result=None, crash=False):
        self._result = result
        self._crash = crash
        self.sent = []

    async def send(self, command):
        self.sent.append(command)
        if self._crash:
            raise BrowserDriverCrashedError("driver died")
        return self._result


class _FakeRegistry:
    def __init__(
        self,
        session=None,
        keyframes=None,
        busy=False,
        taken_over=False,
        acquire_error: Exception | None = None,
    ):
        self._session = session
        self._keyframes = keyframes or KeyframeTracker()
        self._busy = busy
        self._taken_over = taken_over
        self._acquire_error = acquire_error
        self.closed: list[str] = []
        self.last_request = None

    def is_taken_over(self, cid, *, session_id=None, run_id=None):
        # M2 接管互斥: the tool consults this before acquiring; default False (no takeover).
        return self._taken_over

    def peek_entry(self, cid, *, session_id=None, run_id=None):
        if self._session is None:
            return None
        from types import SimpleNamespace

        # Fake single-tab: session_id mirrors conversation for crash-drop assertions.
        host = getattr(self.last_request, "host_kind", None) if self.last_request else None
        return SimpleNamespace(session_id=cid, host_kind=host)

    async def acquire(self, request):
        self.last_request = request
        if self._busy:
            raise BrowserSessionsBusyError("云端浏览器会话已满")
        if self._acquire_error is not None:
            raise self._acquire_error
        return self._session, self._keyframes

    async def close(self, cid):
        self.closed.append(cid)

    async def close_session(self, session_id):
        self.closed.append(session_id)


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="w1",
        backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox()),
        user_id="u1",
        conversation_id="c1",
    )


def _worker_ctx(tmp_path: Path) -> ToolContext:
    from unittest.mock import MagicMock

    return ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="w1",
        backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox()),
        user_id="u1",
        conversation_id="c1",
        escalation=MagicMock(),
    )


@pytest.mark.asyncio
async def test_navigate_builds_display_contract_and_writes_keyframe(tmp_path):
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={
                "final_url": "https://example.com/",
                "title": "Example Domain",
                "http_status": 200,
            },
            frame=b"\xff\xd8\xff\xe0jpeg",
        )
    )
    tool = BrowserNavigateTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"url": "https://example.com/"}, _ctx(tmp_path))

    assert result.success
    d = result.display
    assert d["kind"] == "browser" and d["action"] == "navigate"
    assert d["url"] == "https://example.com/" and d["title"] == "Example Domain"
    assert d["frame"] == "browser/step-0001.jpg"
    # A 推送绑页：成功路径必带 session_id + host_kind（FakeRegistry peek → cid）。
    assert d["session_id"] == "c1" and d["host_kind"] == "sandbox"
    # keyframe actually landed in the workspace (引用即驻留)
    assert (tmp_path / "browser" / "step-0001.jpg").read_bytes() == b"\xff\xd8\xff\xe0jpeg"
    # model-facing output is JSON with an untrusted-content boundary
    payload = json.loads(result.output)
    assert payload["action"] == "navigate" and payload["http_status"] == 200
    assert payload["untrusted_web_content"]["source_url"] == "https://example.com/"
    assert "note" in payload["untrusted_web_content"]


@pytest.mark.asyncio
async def test_snapshot_wraps_tree_untrusted_no_keyframe(tmp_path):
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={
                "final_url": "https://example.com/",
                "title": "Example Domain",
                "snapshot_version": 1,
                "elements": "[e1] link: More",
                "aria": "- document",
            },
        )
    )
    tool = BrowserSnapshotTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({}, _ctx(tmp_path))
    assert result.success
    assert "frame" not in result.display  # read-only action captures no keyframe
    payload = json.loads(result.output)
    uw = payload["untrusted_web_content"]
    assert uw["elements"] == "[e1] link: More" and uw["accessibility_tree"] == "- document"
    assert payload["snapshot_version"] == 1


@pytest.mark.asyncio
async def test_console_wraps_ring_buffer_untrusted_no_keyframe(tmp_path):
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={
                "final_url": "https://example.com/app",
                "title": "App",
                "messages": [
                    {"level": "error", "text": "Uncaught TypeError: x", "timestamp": 1.0},
                ],
                "errors": [
                    {
                        "message": "x is not defined",
                        "stack": "TypeError: x is not defined\n    at app.js:1",
                        "timestamp": 1.1,
                    },
                ],
                "truncated": {"messages_dropped": 0, "errors_dropped": 0},
            },
        )
    )
    tool = BrowserConsoleTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({}, _ctx(tmp_path))
    assert result.success
    assert session.sent[0].action == "console"
    assert "frame" not in (result.display or {})
    payload = json.loads(result.output)
    assert payload["action"] == "console"
    uw = payload["untrusted_web_content"]
    assert uw["console_messages"][0]["level"] == "error"
    assert uw["console_errors"][0]["message"] == "x is not defined"
    assert payload["truncated"] == {"messages_dropped": 0, "errors_dropped": 0}
    assert "读取页面 console" in (result.display or {}).get("detail", "")


@pytest.mark.asyncio
async def test_type_success_returns_current_snapshot_version(tmp_path):
    """Mutation success must surface bumped snapshot_version + elements (same as snapshot)."""
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={
                "final_url": "https://example.com/",
                "title": "Example",
                "snapshot_version": 3,
                "elements": "[e1] input: Name\n[e2] button: Go",
                "aria": "- document",
            },
            frame=b"\xff\xd8\xff\xe0jpeg",
        )
    )
    tool = BrowserTypeTool(registry=_FakeRegistry(session=session))
    result = await tool.execute(
        {"ref": "e1", "text": "hello", "snapshot_version": 2}, _ctx(tmp_path)
    )
    assert result.success
    payload = json.loads(result.output)
    assert payload["snapshot_version"] == 3
    assert payload["action"] == "type"
    uw = payload["untrusted_web_content"]
    assert uw["elements"] == "[e1] input: Name\n[e2] button: Go"
    assert uw["accessibility_tree"] == "- document"

    click = BrowserClickTool(registry=_FakeRegistry(session=session))
    clicked = await click.execute({"ref": "e2", "snapshot_version": 3}, _ctx(tmp_path))
    assert clicked.success
    clicked_payload = json.loads(clicked.output)
    assert clicked_payload["snapshot_version"] == 3
    assert clicked_payload["untrusted_web_content"]["elements"] == (
        "[e1] input: Name\n[e2] button: Go"
    )


@pytest.mark.asyncio
async def test_navigate_success_includes_elements_in_untrusted(tmp_path):
    """Navigate (mutation) success wraps driver elements/aria like snapshot."""
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={
                "final_url": "https://example.com/",
                "title": "Example Domain",
                "http_status": 200,
                "snapshot_version": 1,
                "elements": "[e1] link: More information...",
                "aria": "- document\n  - heading",
            },
            frame=b"\xff\xd8\xff\xe0jpeg",
        )
    )
    tool = BrowserNavigateTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"url": "https://example.com/"}, _ctx(tmp_path))
    assert result.success
    payload = json.loads(result.output)
    assert payload["snapshot_version"] == 1
    uw = payload["untrusted_web_content"]
    assert uw["elements"] == "[e1] link: More information..."
    assert uw["accessibility_tree"] == "- document\n  - heading"


@pytest.mark.asyncio
async def test_ref_stale_error_is_not_doubled(tmp_path):
    """Driver ValueError + host prefix must not appear twice in model-facing text."""
    session = _FakeSession(
        BrowserCommandResult(
            ok=False,
            error="ValueError: ref 版本过期（快照 v1 ≠ 当前 v2）：页面已变化，请重新 browser_snapshot 获取最新 ref",
        )
    )
    tool = BrowserClickTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"ref": "e1", "snapshot_version": 1}, _ctx(tmp_path))
    assert not result.success
    text = _fail_text(result)
    assert text.count("ref 版本过期") == 1
    assert "ValueError:" not in text
    assert result.output == ""


@pytest.mark.asyncio
async def test_busy_returns_explainable_failure(tmp_path):
    tool = BrowserNavigateTool(registry=_FakeRegistry(busy=True))
    result = await tool.execute({"url": "https://x/"}, _ctx(tmp_path))
    assert not result.success and "已满" in _fail_text(result)


@pytest.mark.asyncio
async def test_acquire_session_not_found_metadata_code(tmp_path):
    tool = BrowserNavigateTool(
        registry=_FakeRegistry(
            acquire_error=BrowserSessionAcquireError(
                "session_not_found: 浏览器会话不存在（x）",
                code="session_not_found",
            )
        )
    )
    result = await tool.execute({"url": "https://x/", "session_id": "x"}, _ctx(tmp_path))
    assert not result.success
    assert (result.metadata or {}).get("code") == "session_not_found"


@pytest.mark.asyncio
async def test_egress_unavailable_retires_all_browser_tools(tmp_path):
    """NetnsError / egress hard-fail: one shot → retire the whole ``browser`` surface."""
    tool = BrowserNavigateTool(
        registry=_FakeRegistry(
            acquire_error=BrowserSessionError(
                "云端浏览器沙箱网络隔离不可用（netns 创建失败）",
                code=EGRESS_UNAVAILABLE_CODE,
            )
        )
    )
    result = await tool.execute({"url": "https://x/"}, _ctx(tmp_path))
    assert not result.success
    assert (result.metadata or {}).get("code") == EGRESS_UNAVAILABLE_CODE
    assert set(result.metadata.get("retire_tools") or []) == BROWSER_TOOL_NAMES
    assert "retire_message" in (result.metadata or {})
    assert "web_search" in _fail_text(result) and "browser" in _fail_text(result)
    # No double「浏览器会话启动失败」prefix on the classified path.
    assert _fail_text(result).count("浏览器会话启动失败") == 0
    # Sticky: next turn's assembly gate must see netns as unavailable.
    assert browser_netns_health() is False


@pytest.mark.asyncio
async def test_egress_unavailable_from_wrapped_netns_message(tmp_path):
    """Legacy wrapped NetnsError string still classifies without an explicit code."""
    tool = BrowserNavigateTool(
        registry=_FakeRegistry(
            acquire_error=BrowserSessionError(
                "浏览器会话启动失败：NetnsError: ip netns add acbrw0 failed (1): "
                "mkdir /run/netns failed: Permission denied"
            )
        )
    )
    result = await tool.execute({"url": "https://x/"}, _ctx(tmp_path))
    assert not result.success
    assert (result.metadata or {}).get("code") == EGRESS_UNAVAILABLE_CODE
    assert set(result.metadata.get("retire_tools") or []) == BROWSER_TOOL_NAMES
    assert browser_netns_health() is False


@pytest.mark.asyncio
async def test_session_error_does_not_double_prefix(tmp_path):
    tool = BrowserNavigateTool(
        registry=_FakeRegistry(
            acquire_error=BrowserSessionError(
                "浏览器会话启动失败：RpcChannelClosedError: driver stdio channel closed"
            )
        )
    )
    result = await tool.execute({"url": "https://x/"}, _ctx(tmp_path))
    assert not result.success
    assert _fail_text(result).count("浏览器会话启动失败") == 1


@pytest.mark.asyncio
async def test_acquire_session_bound_elsewhere_metadata_code(tmp_path):
    tool = BrowserNavigateTool(
        registry=_FakeRegistry(
            acquire_error=BrowserSessionAcquireError(
                "session_bound_elsewhere: 浏览器会话已绑定 local",
                code="session_bound_elsewhere",
            )
        )
    )
    result = await tool.execute({"url": "https://x/"}, _ctx(tmp_path))
    assert not result.success
    assert (result.metadata or {}).get("code") == "session_bound_elsewhere"


@pytest.mark.asyncio
async def test_driver_crash_drops_session_and_informs(tmp_path):
    reg = _FakeRegistry(session=_FakeSession(crash=True))
    tool = BrowserNavigateTool(registry=reg)
    result = await tool.execute({"url": "https://x/"}, _ctx(tmp_path))
    assert not result.success
    assert "页面状态已丢失" in _fail_text(result) and "重新开始" in _fail_text(result)
    assert reg.closed == ["c1"]  # dead session dropped → next call rebuilds


@pytest.mark.asyncio
async def test_keyframe_count_cap_stops_capturing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "browser_keyframe_max_per_turn", 1)
    kf = KeyframeTracker()
    session = _FakeSession(
        BrowserCommandResult(
            ok=True, data={"final_url": "https://x/", "title": "T"}, frame=b"\xff\xd8x"
        )
    )
    tool = BrowserNavigateTool(registry=_FakeRegistry(session=session, keyframes=kf))
    first = await tool.execute({"url": "https://x/"}, _ctx(tmp_path))
    second = await tool.execute({"url": "https://x/"}, _ctx(tmp_path))
    assert first.display.get("frame") == "browser/step-0001.jpg"
    assert "frame" not in second.display  # over per-turn cap → no more frames
    assert "上限" in json.loads(second.output).get("note", "")


@pytest.mark.asyncio
async def test_keyframe_size_cap_skips_oversized_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "browser_keyframe_max_bytes", 4)
    session = _FakeSession(
        BrowserCommandResult(
            ok=True, data={"final_url": "https://x/", "title": "T"}, frame=b"\xff\xd8oversized"
        )
    )
    tool = BrowserNavigateTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"url": "https://x/"}, _ctx(tmp_path))
    assert result.success and "frame" not in result.display
    assert "大小上限" in json.loads(result.output).get("note", "")


@pytest.mark.asyncio
async def test_navigate_missing_frame_succeeds_with_honest_note(tmp_path):
    """Case C: want_frame but frame is None → navigate stays ok, note warns against pixels."""
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={"final_url": "https://example.com/", "title": "Example", "http_status": 200},
            frame=None,
        )
    )
    tool = BrowserNavigateTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"url": "https://example.com/"}, _ctx(tmp_path))
    assert result.success
    assert "frame" not in (result.display or {})
    payload = json.loads(result.output)
    note = payload.get("note") or ""
    assert "未截到画面" in note
    assert "snapshot" in note.lower() or "browser_snapshot" in note


@pytest.mark.asyncio
async def test_screenshot_missing_frame_is_weak_failure(tmp_path):
    """Case C: browser_screenshot without a frame must not mark success."""
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={"final_url": "https://example.com/", "title": "Example"},
            frame=None,
        )
    )
    tool = BrowserScreenshotTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({}, _worker_ctx(tmp_path))
    assert result.success is False
    assert "未截到画面" in _fail_text(result)
    assert (result.metadata or {}).get("code") == "no_frame"


@pytest.mark.asyncio
async def test_ceo_screenshot_is_structured_delegate_failure(tmp_path):
    tool = BrowserScreenshotTool(registry=_FakeRegistry(session=_FakeSession()))
    result = await tool.execute({}, _ctx(tmp_path))
    assert result.success is False
    assert result.contract_failure is True
    assert "delegate" in _fail_text(result)


@pytest.mark.asyncio
async def test_missing_url_is_rejected(tmp_path):
    tool = BrowserNavigateTool(registry=_FakeRegistry(session=_FakeSession()))
    result = await tool.execute({}, _ctx(tmp_path))
    assert not result.success and "url" in _fail_text(result)


@pytest.mark.asyncio
async def test_type_password_blocked_maps_to_tool_result(tmp_path):
    """Driver password hard-reject → metadata.code=password_blocked, no fill semantics."""
    session = _FakeSession(
        BrowserCommandResult(
            ok=False,
            data={},
            error="ValueError: password_blocked: AI 不得填写密码框",
        )
    )
    tool = BrowserTypeTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"ref": "e1", "text": "secret"}, _ctx(tmp_path))
    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "password_blocked"
    # Fake ctx has no escalation channel → CEO path (ask_user), not escalate-only.
    assert "ask_user(browser_login=true)" in _fail_text(result)
    assert "browser_login" in _fail_text(result)
    assert session.sent and session.sent[0].action == "type"


def test_browser_type_schema_guides_password_login():
    desc = BrowserTypeTool().schema.description
    assert "password_blocked" in desc or "password" in desc.lower()
    assert "browser_login" in desc
    assert "ask_user" in desc
    assert "escalate" in desc
    assert "M0 不支持登录" not in desc


def test_mutation_schemas_require_receipt_verification():
    """click/type/scroll/navigate: ref table + must verify typed/clicked; snapshot when needed."""
    for tool in (
        BrowserNavigateTool(),
        BrowserClickTool(),
        BrowserTypeTool(),
        BrowserScrollTool(),
    ):
        desc = tool.schema.description
        assert "elements" in desc
        assert "visible_text" in desc
        assert "browser(action=snapshot)" in desc
        assert "验收" in desc or "matched" in desc or "was_disabled" in desc
        # Old "only snapshot when needed / success already enough" framing is gone.
        assert "仅必要" not in desc
        assert "可直接用于下一步；仅当" not in desc


# -- 甲/乙：本会话 HTML 相对路径 ------------------------------------------------
def test_navigate_schema_mentions_workspace_relative_path():
    schema = BrowserNavigateTool().schema
    assert "相对" in schema.description or "site/index.html" in schema.description
    url_desc = schema.parameters["properties"]["url"]["description"]
    assert "相对" in url_desc or "site/index.html" in url_desc
    assert "file://" in url_desc or "file://" in schema.description


@pytest.mark.asyncio
async def test_sandbox_relative_path_fails_honestly_no_fake_success(tmp_path):
    """乙：云端沙箱相对路径 → ToolResult 失败，引导完整预览；不派发 driver。"""
    session = _FakeSession(
        BrowserCommandResult(ok=True, data={"final_url": "https://x/", "title": "T"})
    )
    tool = BrowserNavigateTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"url": "site/index.html"}, _ctx(tmp_path))
    assert result.success is False
    assert "完整预览" in _fail_text(result)
    assert session.sent == []


@pytest.mark.asyncio
async def test_sandbox_workspace_url_fails_honestly(tmp_path):
    session = _FakeSession(
        BrowserCommandResult(ok=True, data={"final_url": "https://x/", "title": "T"})
    )
    tool = BrowserNavigateTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"url": "workspace://c1/site/index.html"}, _ctx(tmp_path))
    assert result.success is False
    assert "完整预览" in _fail_text(result)
    assert session.sent == []


@pytest.mark.asyncio
async def test_sandbox_file_url_rejected(tmp_path):
    session = _FakeSession()
    tool = BrowserNavigateTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"url": "file:///tmp/x.html"}, _ctx(tmp_path))
    assert result.success is False
    assert session.sent == []


@pytest.mark.asyncio
async def test_local_relative_path_rewritten_to_workspace(tmp_path, monkeypatch):
    """甲：Local Bridge 健康时相对路径 → 改写为 workspace:// 再派发。"""
    set_desktop_bridge_health_for_tests(True)
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={
                "final_url": "workspace://conv.conv-id/site/index.html",
                "title": "Index",
                "http_status": None,
            },
            frame=b"\xff\xd8\xff",
        )
    )
    reg = _FakeRegistry(session=session)
    tool = BrowserNavigateTool(registry=reg)
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
        conversation_id="Conv-ID",
    )
    result = await tool.execute({"url": "site/index.html"}, ctx)
    assert result.success, result.output
    assert session.sent
    assert session.sent[0].args["url"] == "workspace://conv.conv-id/site/index.html"
    assert reg.last_request is not None
    assert reg.last_request.host_kind == "local"
    assert result.display["host_kind"] == "local"
    reset_desktop_bridge_health_for_tests()


@pytest.mark.asyncio
async def test_bridge_session_sandbox_relative_path_fails_honestly(tmp_path, monkeypatch):
    """过桥无 Bridge + gVisor：相对路径诚实失败；acquire 须 host_kind=sandbox。"""
    reset_desktop_bridge_health_for_tests()
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    session = _FakeSession(
        BrowserCommandResult(ok=True, data={"final_url": "https://x/", "title": "T"})
    )
    reg = _FakeRegistry(session=session)
    tool = BrowserNavigateTool(registry=reg)
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
        conversation_id="c1",
    )
    result = await tool.execute({"url": "site/index.html"}, ctx)
    assert result.success is False
    assert "完整预览" in _fail_text(result)
    assert session.sent == []
    # https still acquires sandbox (not open_local_bridge_session)
    result_ok = await tool.execute({"url": "https://example.com/"}, ctx)
    assert result_ok.success
    assert reg.last_request is not None
    assert reg.last_request.host_kind == "sandbox"
    assert result_ok.display["host_kind"] == "sandbox"


def test_classify_and_rewrite_navigate_targets():
    from agentcore.runtime.browser.navigate_target import (
        classify_navigate_target,
        rewrite_local_navigate_url,
    )

    assert classify_navigate_target("https://example.com/") == "http"
    assert classify_navigate_target("workspace://c1/a.html") == "workspace"
    assert classify_navigate_target("site/index.html") == "relative"
    assert classify_navigate_target("file:///tmp/x") == "invalid"
    assert classify_navigate_target("../secret") == "invalid"
    assert (
        rewrite_local_navigate_url("site/index.html", "Conv-ID")
        == "workspace://conv.conv-id/site/index.html"
    )
    assert rewrite_local_navigate_url("https://example.com/", "c1") == "https://example.com/"


# -- Post-conditions (typed / clicked) + visible_text --------------------------
@pytest.mark.asyncio
async def test_type_matched_false_is_tool_failure(tmp_path):
    """Executor reports matched=false → tool success=False with actionable error + evidence."""
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={
                "final_url": "https://example.com/chat",
                "title": "Chat",
                "snapshot_version": 4,
                "elements": (
                    '[e1] textarea: composer | placeholder="Type…" | value=""\n'
                    "---\n"
                    "visible_text: Alice: hi"
                ),
                "aria": "- document",
                "typed": {
                    "ref": "e1",
                    "requested_chars": 5,
                    "actual_chars": 0,
                    "matched": False,
                    "method": "cdp_insertText",
                },
            },
            frame=b"\xff\xd8\xff\xe0jpeg",
        )
    )
    tool = BrowserTypeTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"ref": "e1", "text": "hello"}, _ctx(tmp_path))
    assert result.success is False
    assert result.metadata.get("code") == "postcondition_failed"
    err = _fail_text(result)
    assert "写入未生效" in err
    assert "不是「动作根本没发生」" in err
    assert "matched=false" in err
    payload = json.loads(result.output)
    assert payload["typed"]["matched"] is False
    assert payload["typed"]["actual_chars"] == 0
    uw = payload["untrusted_web_content"]
    assert uw["visible_text"] == "Alice: hi"
    assert "visible_text:" not in (uw.get("elements") or "")
    assert "[e1] textarea" in (uw.get("elements") or "")


@pytest.mark.asyncio
async def test_type_matched_true_remains_success(tmp_path):
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={
                "final_url": "https://example.com/",
                "title": "Example",
                "snapshot_version": 2,
                "elements": '[e1] textarea: composer | value="hello"',
                "typed": {
                    "ref": "e1",
                    "requested_chars": 5,
                    "actual_chars": 5,
                    "matched": True,
                    "method": "cdp_insertText",
                },
            },
            frame=b"\xff\xd8\xff\xe0jpeg",
        )
    )
    tool = BrowserTypeTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"ref": "e1", "text": "hello"}, _ctx(tmp_path))
    assert result.success is True
    payload = json.loads(result.output)
    assert payload["typed"]["matched"] is True


@pytest.mark.asyncio
async def test_click_was_disabled_is_tool_failure(tmp_path):
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={
                "final_url": "https://example.com/",
                "title": "Example",
                "snapshot_version": 5,
                "elements": "[e2] button disabled: Send",
                "clicked": {
                    "ref": "e2",
                    "was_disabled": True,
                    "role": "button",
                    "name": "Send",
                },
            },
            frame=b"\xff\xd8\xff\xe0jpeg",
        )
    )
    tool = BrowserClickTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({"ref": "e2"}, _ctx(tmp_path))
    assert result.success is False
    assert result.metadata.get("code") == "postcondition_failed"
    err = _fail_text(result)
    assert "禁用" in err
    assert "was_disabled=true" in err
    assert "不是「动作根本没发生」" in err
    payload = json.loads(result.output)
    assert payload["clicked"]["was_disabled"] is True


@pytest.mark.asyncio
async def test_visible_text_capped_in_untrusted(tmp_path):
    from agentcore.tools.builtin.browser import _VISIBLE_TEXT_MAX

    huge = "x" * (_VISIBLE_TEXT_MAX + 500)
    session = _FakeSession(
        BrowserCommandResult(
            ok=True,
            data={
                "final_url": "https://example.com/",
                "title": "T",
                "snapshot_version": 1,
                "elements": f"[e1] button: Go\n---\nvisible_text: {huge}",
            },
        )
    )
    tool = BrowserSnapshotTool(registry=_FakeRegistry(session=session))
    result = await tool.execute({}, _ctx(tmp_path))
    assert result.success
    uw = json.loads(result.output)["untrusted_web_content"]
    assert len(uw["visible_text"]) <= _VISIBLE_TEXT_MAX
    assert uw["visible_text"].endswith("x" * 20)
    assert "visible_text:" not in (uw.get("elements") or "")
    assert "[e1] button: Go" in uw["elements"]
