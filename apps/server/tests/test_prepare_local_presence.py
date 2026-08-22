"""Presence gate + prepare local IO budget + honest three-case aborts."""

from __future__ import annotations

import asyncio
import time

import pytest

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import error_fields_for
from agentcore.fulfill.hub import default_fulfiller_hub
from agentcore.fulfill.origin import origin_device
from agentcore.runtime.context import detect_workspace_git
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.pipeline.errors import (
    LOCAL_CHANNEL_DEAD,
    LOCAL_DESKTOP_OFFLINE,
    LOCAL_ORIGIN_DEVICE_OFFLINE,
    LOCAL_ROOT_NOT_HELD,
    await_prepare_local_io,
    bind_prepare_local_io_deadline,
    prepare_local_io_budget,
    prepare_local_io_budget_active,
    prepare_local_io_span,
    raise_if_local_workspace_fulfiller_absent,
    remaining_prepare_local_io_budget,
    reset_prepare_local_io_deadline,
)
from agentcore.runtime.pipeline.prepare import prepare_fresh_turn
from agentcore.tools.sandbox.exec_languages import resolve_exec_languages
from agentcore.workspace.channel import WorkspaceChannel, WorkspaceOp
from agentcore.workspace.local import LocalWorkspace
from agentcore.workspace.protocol import WorkspaceIOError

pytestmark = pytest.mark.anyio

CONV = "conv-presence-gate"
USER = "presence-user"
ROOT = "root-presence"


def _local(root_id: str = ROOT, *, timeout: float = 5.0) -> LocalWorkspace:
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=InteractionRegistry(),
        timeout_seconds=timeout,
        root_id=root_id,
    )
    return LocalWorkspace(channel)


@pytest.fixture(autouse=True)
def _clear_fulfillers():
    """Isolate hub state — parallel tests must not see leftover devices."""
    hub = default_fulfiller_hub()
    # Drop any sessions left for our test user (best-effort isolation).
    while hub.connection_count(USER) > 0:
        session = hub.find(USER, root_id=None, channel="workspace")
        if session is None:
            break
        hub.unregister(session)
    yield
    while hub.connection_count(USER) > 0:
        session = hub.find(USER, root_id=None, channel="workspace")
        if session is None:
            break
        hub.unregister(session)


def test_presence_gate_desktop_offline():
    backend = _local()
    with pytest.raises(WorkspaceIOError) as ei:
        raise_if_local_workspace_fulfiller_absent(user_id=USER, backend=backend)
    assert str(ei.value) == LOCAL_DESKTOP_OFFLINE
    assert "重新生成" in str(ei.value)
    assert "不要再次发送" in str(ei.value)


def test_presence_gate_root_not_held():
    hub = default_fulfiller_hub()
    session = hub.register(
        USER, "dev-other-root", caps=["workspace"], roots=["other-root"]
    )
    try:
        backend = _local(ROOT)
        with pytest.raises(WorkspaceIOError) as ei:
            raise_if_local_workspace_fulfiller_absent(user_id=USER, backend=backend)
        assert str(ei.value) == LOCAL_ROOT_NOT_HELD
        assert "重新生成" in str(ei.value)
    finally:
        hub.unregister(session)


@pytest.mark.real_fulfill_dispatch
async def test_root_revoked_mid_turn_reads_as_root_not_held_not_no_fulfiller():
    """The gate runs once; roots can go away after it passed.

    Delivery then has to reach the same verdict the gate would have — the desktop
    is right there fulfilling other channels, so 无履约方 tells the user nothing
    they can act on.
    """
    hub = default_fulfiller_hub()
    session = hub.register(USER, "dev-mid-turn", caps=["workspace"], roots=[ROOT])
    try:
        backend = _local(ROOT)
        raise_if_local_workspace_fulfiller_absent(user_id=USER, backend=backend)

        # The device reconnects without the root (revoked while it was away):
        # re-registering the same (user, device) replaces the session.
        session = hub.register(
            USER, "dev-mid-turn", caps=["workspace"], roots=["some-other-root"]
        )

        t0 = time.monotonic()
        with pytest.raises(WorkspaceIOError) as ei:
            await backend.read("notes.md")
        elapsed = time.monotonic() - t0
        assert LOCAL_ROOT_NOT_HELD in str(ei.value)
        assert "无履约方" not in str(ei.value)
        # Settled on the spot — no waiting out the channel deadline.
        assert elapsed < 1.0
    finally:
        hub.unregister(session)


def test_presence_gate_passes_when_root_held():
    hub = default_fulfiller_hub()
    session = hub.register(USER, "dev-ok", caps=["workspace"], roots=[ROOT])
    try:
        raise_if_local_workspace_fulfiller_absent(
            user_id=USER, backend=_local(ROOT)
        )
    finally:
        hub.unregister(session)


def test_presence_gate_origin_device_offline():
    """Rootless local channel + a peer online: say which device is missing."""
    hub = default_fulfiller_hub()
    session = hub.register(USER, "dev-B", caps=["workspace"], roots=[])
    try:
        with origin_device("dev-A"), pytest.raises(WorkspaceIOError) as ei:
            raise_if_local_workspace_fulfiller_absent(
                user_id=USER, backend=_local("")
            )
        assert str(ei.value) == LOCAL_ORIGIN_DEVICE_OFFLINE
        assert "发起本回合的设备不在线" in str(ei.value)
        assert "重新生成" in str(ei.value)
    finally:
        hub.unregister(session)


def test_presence_gate_passes_for_the_origin_device():
    hub = default_fulfiller_hub()
    session = hub.register(USER, "dev-A", caps=["workspace"], roots=[])
    try:
        with origin_device("dev-A"):
            raise_if_local_workspace_fulfiller_absent(
                user_id=USER, backend=_local("")
            )
    finally:
        hub.unregister(session)


def test_presence_gate_ignores_origin_when_the_root_decides():
    """Root-bound turns keep their existing location logic (boundary held)."""
    hub = default_fulfiller_hub()
    session = hub.register(USER, "dev-B", caps=["workspace"], roots=[ROOT])
    try:
        with origin_device("dev-A"):
            raise_if_local_workspace_fulfiller_absent(
                user_id=USER, backend=_local(ROOT)
            )
    finally:
        hub.unregister(session)


def test_presence_gate_single_device_answer_is_unchanged():
    """No peer online → the honest answer stays '桌面未连接', not '换台电脑'."""
    with origin_device("dev-A"), pytest.raises(WorkspaceIOError) as ei:
        raise_if_local_workspace_fulfiller_absent(user_id=USER, backend=_local(""))
    assert str(ei.value) == LOCAL_DESKTOP_OFFLINE


def test_error_fields_for_prepare_abort_messages():
    for message in (
        LOCAL_DESKTOP_OFFLINE,
        LOCAL_ROOT_NOT_HELD,
        LOCAL_CHANNEL_DEAD,
        LOCAL_ORIGIN_DEVICE_OFFLINE,
    ):
        code, text, _ctx = error_fields_for(
            WorkspaceIOError(message),
            fallback_code=ErrorCode.STREAM_ERROR,
            fallback_message="服务出错了，请稍后重试。",
        )
        assert code == ErrorCode.STREAM_ERROR
        assert text == message
        assert "服务出错了" not in text


async def test_prepare_aborts_desktop_offline_skips_llm(monkeypatch):
    backend = _local()
    llm_calls: list[str] = []

    async def _should_not_build(*_a, **_k):
        llm_calls.append("build")
        raise AssertionError("LLM must not run without fulfiller")

    import agentcore.runtime.pipeline as pipeline_pkg

    monkeypatch.setattr(pipeline_pkg, "build_turn_router", _should_not_build)

    async def _empty_rules(*_a, **_k):
        return ""

    async def _empty_catalog(*_a, **_k):
        return []

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.assemble_turn_rules", _empty_rules
    )
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.load_folder_catalog", _empty_catalog
    )

    with pytest.raises(WorkspaceIOError) as ei:
        await prepare_fresh_turn(
            conversation_id=CONV,
            user_id=USER,
            backend=backend,
            sink=EventSink(),
            folder_id=None,
            board_id=None,
            attachments=None,
            permission_axes=None,
            llm_credentials=None,
            x_client_platform="desktop",
        )
    assert str(ei.value) == LOCAL_DESKTOP_OFFLINE
    assert llm_calls == []


async def test_prepare_aborts_root_not_held_skips_llm(monkeypatch):
    hub = default_fulfiller_hub()
    session = hub.register(
        USER, "dev-wrong-root", caps=["workspace"], roots=["wrong"]
    )
    try:
        backend = _local(ROOT)
        llm_calls: list[str] = []

        async def _should_not_build(*_a, **_k):
            llm_calls.append("build")
            raise AssertionError("LLM must not run when root not held")

        import agentcore.runtime.pipeline as pipeline_pkg

        monkeypatch.setattr(pipeline_pkg, "build_turn_router", _should_not_build)

        async def _empty_rules(*_a, **_k):
            return ""

        async def _empty_catalog(*_a, **_k):
            return []

        monkeypatch.setattr(
            "agentcore.runtime.pipeline.prepare.assemble_turn_rules",
            _empty_rules,
        )
        monkeypatch.setattr(
            "agentcore.runtime.pipeline.prepare.load_folder_catalog",
            _empty_catalog,
        )

        with pytest.raises(WorkspaceIOError) as ei:
            await prepare_fresh_turn(
                conversation_id=CONV,
                user_id=USER,
                backend=backend,
                sink=EventSink(),
                folder_id=None,
                board_id=None,
                attachments=None,
                permission_axes=None,
                llm_credentials=None,
                x_client_platform="desktop",
            )
        assert str(ei.value) == LOCAL_ROOT_NOT_HELD
        assert llm_calls == []
    finally:
        hub.unregister(session)


async def test_prepare_budget_exhaustion_aborts_as_channel_dead():
    """Wall-clock budget cuts a hung local IO instead of summing per-op timeouts."""

    async def _hang():
        await asyncio.sleep(5.0)
        return "never"

    t0 = time.monotonic()
    with prepare_local_io_budget(0.05), pytest.raises(WorkspaceIOError) as ei:
        await await_prepare_local_io(_hang())
    elapsed = time.monotonic() - t0
    assert str(ei.value) == LOCAL_CHANNEL_DEAD
    assert elapsed < 1.0


async def test_prepare_first_liveness_timeout_aborts_including_probe_exec():
    """Under prepare budget, probe_exec hang aborts immediately (N=1 prepare posture)."""
    hub = default_fulfiller_hub()
    session = hub.register(USER, "dev-probe", caps=["workspace"], roots=[ROOT])
    try:

        class _HangChannel:
            async def request(self, op, args, *, timeout=None):
                raise WorkspaceIOError(
                    f"local workspace op '{op}' timed out（活性挂起）"
                )

        class _Local:
            location = "local"

            def __init__(self) -> None:
                self._channel = _HangChannel()

        with prepare_local_io_budget(5.0), pytest.raises(WorkspaceIOError) as ei:
            await resolve_exec_languages(_Local())
        assert str(ei.value) == LOCAL_CHANNEL_DEAD
    finally:
        hub.unregister(session)


async def test_probe_exec_outside_budget_still_fail_closes_advertise():
    """Without prepare budget, probe hang keeps advertise fail-closed (execution posture)."""

    class _HangChannel:
        async def request(self, op, args, *, timeout=None):
            assert op == WorkspaceOp.PROBE_EXEC or op == "probe_exec"
            raise WorkspaceIOError(
                "local workspace op 'probe_exec' timed out（活性挂起）"
            )

    class _Local:
        location = "local"

        def __init__(self) -> None:
            self._channel = _HangChannel()

    langs = await resolve_exec_languages(_Local())
    assert langs == ()


class _HangingDeskChannel:
    """Every desktop round-trip times out (liveness hang)."""

    async def request(self, op, args, *, timeout=None):
        raise WorkspaceIOError(f"local workspace op '{op}' timed out（活性挂起）")


class _HangingDesk:
    """Local desktop-channel backend that never answers — a hung desk."""

    location = "local"

    def __init__(self) -> None:
        self._channel = _HangingDeskChannel()

    async def exists(self, path: str) -> bool:
        raise WorkspaceIOError("local workspace op 'exists' timed out（活性挂起）")


async def test_execution_phase_ignores_turn_prepare_deadline():
    """执行期不受此闸 (双模式工作区 §7.7): the turn-wide deadline gates only prepare spans.

    Cross-desk delegation re-probes a TARGET desk mid-execution. A hang there must
    degrade that probe, never abort the turn with the 本机-desk channel-dead copy.
    """
    token = bind_prepare_local_io_deadline(0.0)  # prepare clock already spent
    try:
        assert not prepare_local_io_budget_active()
        assert await resolve_exec_languages(_HangingDesk()) == ()
        assert (await detect_workspace_git(_HangingDesk())).present is None
    finally:
        reset_prepare_local_io_deadline(token)


async def test_prepare_span_adopts_turn_deadline_then_releases_the_gate():
    """Inside a span the budget is in force on the turn's shared clock; outside it is not."""
    token = bind_prepare_local_io_deadline(5.0)
    try:
        assert not prepare_local_io_budget_active()
        with prepare_local_io_span(_HangingDesk()):
            assert prepare_local_io_budget_active()
            remaining = remaining_prepare_local_io_budget()
            assert remaining is not None and 0.0 < remaining <= 5.0
            with pytest.raises(WorkspaceIOError) as ei:
                await resolve_exec_languages(_HangingDesk())
            assert str(ei.value) == LOCAL_CHANNEL_DEAD
        assert not prepare_local_io_budget_active()
        assert remaining_prepare_local_io_budget() is None
    finally:
        reset_prepare_local_io_deadline(token)


async def test_prepare_span_is_noop_for_backends_without_a_desktop_channel():
    """Cloud / sidecar Path-backed workspaces do no desktop round-trips — nothing to cap."""

    class _Cloud:
        location = "server"

    class _SidecarLocal:
        location = "local"
        _channel = None

    for backend in (_Cloud(), _SidecarLocal()):
        with prepare_local_io_span(backend):
            assert not prepare_local_io_budget_active()
