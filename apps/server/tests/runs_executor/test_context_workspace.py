from agentcore.runtime.runs.executor.context import (
    _build_messages,
    _safe_index_files,
)
from agentcore.runtime.runs.types import RunSpec
from tests.client_tool_fulfill_testutil import await_captured_event
from tests.runs_executor.conftest import _plan, _state


def test_worker_opening_has_no_workspace_listing_channel():
    plan = _plan(
        RunSpec(run_id="me", agent_id="me", role="我", task="干活", depends_on=["dep"]),
        RunSpec(run_id="dep", agent_id="dep", role="前置", task="前置"),
        RunSpec(run_id="peer", agent_id="peer", role="并行队友", task="别的"),
    )
    completed = {
        "dep": _state("前置产物", files=["dep.py"]),
        "peer": _state(files=["peer/out.json"]),
    }
    sink: list = []
    msgs = _build_messages(
        plan, plan.by_id("me"), completed, "SYS", "原始请求", blocks_sink=sink
    )
    assert all(b.channel != "workspace" for b in sink)
    user = msgs[1].content or ""
    assert "你的任务" in user
    assert "dep.py" in user
    assert "peer/out.json" not in user


async def test_safe_index_files_swallows_backend_failure():
    class _Boom:
        async def index_files(self, **_kw):
            raise RuntimeError("desktop dropped")

    class _Ok:
        def __init__(self) -> None:
            self.order: str | None = None

        async def index_files(self, *, order: str = "path"):
            self.order = order
            return (["a.txt", "b.txt"], True)

    assert await _safe_index_files(_Boom()) == []  # failure → empty, never raises
    assert await _safe_index_files(object()) == []  # backend without indexing → empty
    ok = _Ok()
    assert await _safe_index_files(ok) == ["a.txt", "b.txt"]  # paths, flag dropped
    assert ok.order == "recent"


async def test_safe_index_files_timeout_does_not_sticky_dead_channel():
    """Best-effort index hangs must not sticky-dead the shared file channel.

    Bare tool-side INDEX_FILES still counts toward sticky (control); ``_safe_index_files``
    wraps ``index_io_mode`` so N=2 ambient hangs leave the channel alive for real tools.
    """
    import asyncio

    import pytest

    from agentcore.runtime.interaction import InteractionRegistry
    from agentcore.workspace.channel import WorkspaceChannel, WorkspaceOp
    from agentcore.workspace.local import LocalWorkspace
    from agentcore.workspace.protocol import WorkspaceIOError

    conv = "conv-ambient-index"
    root_id = "root-ambient"

    async def _await_request():
        return await await_captured_event()

    registry_bare = InteractionRegistry()
    channel_bare = WorkspaceChannel(
        user_id="u-test",
        conversation_id=conv,
        registry=registry_bare,
        timeout_seconds=0.05,
        root_id=root_id,
    )
    for _ in range(2):
        with pytest.raises(WorkspaceIOError, match="活性挂起"):
            await channel_bare.request(WorkspaceOp.INDEX_FILES, {"cap": 10, "order": "path"})
    assert channel_bare._dead is True  # noqa: SLF001

    registry = InteractionRegistry()
    channel = WorkspaceChannel(
        user_id="u-test",
        conversation_id=conv,
        registry=registry,
        timeout_seconds=0.05,
        root_id=root_id,
    )
    backend = LocalWorkspace(channel)
    assert await _safe_index_files(backend) == []
    assert await _safe_index_files(backend) == []
    assert channel._dead is False  # noqa: SLF001
    from tests.client_tool_fulfill_testutil import DELIVERED_EVENTS

    DELIVERED_EVENTS.clear()

    task = asyncio.create_task(channel.request(WorkspaceOp.READ, {"path": "a.txt"}))
    event = await _await_request()
    assert event.payload["op"] == "read"
    assert registry.resolve(
        event.payload["request_id"],
        {"ok": True, "value": "alive"},
        conversation_id=conv,
    )
    assert await task == "alive"
