"""Prepare / turn gate: sticky channel-dead must abort before assemble + LLM."""

from __future__ import annotations

import pytest

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import error_fields_for
from agentcore.fulfill.hub import default_fulfiller_hub
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.pipeline.prepare import prepare_fresh_turn
from agentcore.workspace.channel import (
    WorkspaceChannel,
    raise_if_backend_channel_dead,
)
from agentcore.workspace.limits import CHANNEL_DEAD_PREPARE_ABORT, is_channel_dead_detail
from agentcore.workspace.local import LocalWorkspace
from agentcore.workspace.protocol import WorkspaceIOError

pytestmark = pytest.mark.anyio

CONV = "conv-channel-dead-prepare"
USER = "u1"
ROOT = "root-dead"


def _dead_local() -> LocalWorkspace:
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=InteractionRegistry(),
        timeout_seconds=5.0,
        root_id=ROOT,
    )
    channel._dead = True  # noqa: SLF001 — sticky-dead latch for the gate under test
    return LocalWorkspace(channel)


@pytest.fixture
def workspace_fulfiller():
    """Presence gate requires a live workspace fulfiller for this root."""
    hub = default_fulfiller_hub()
    session = hub.register(
        USER, "dev-channel-dead", caps=["workspace"], roots=[ROOT]
    )
    try:
        yield session
    finally:
        hub.unregister(session)


def test_raise_if_backend_channel_dead_raises_honest_io_error():
    backend = _dead_local()
    with pytest.raises(WorkspaceIOError, match="本机工作区通道无响应") as ei:
        raise_if_backend_channel_dead(backend)
    assert is_channel_dead_detail(str(ei.value))


def test_raise_if_backend_channel_dead_noop_when_alive():
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=InteractionRegistry(),
        timeout_seconds=5.0,
        root_id="root-alive",
    )
    raise_if_backend_channel_dead(LocalWorkspace(channel))  # no raise
    raise_if_backend_channel_dead(None)


def test_error_fields_for_surfaces_channel_dead_prepare_abort():
    code, message, _ctx = error_fields_for(
        WorkspaceIOError(CHANNEL_DEAD_PREPARE_ABORT),
        fallback_code=ErrorCode.STREAM_ERROR,
        fallback_message="服务出错了，请稍后重试。",
    )
    assert code == ErrorCode.STREAM_ERROR
    assert "本机工作区通道无响应" in message
    assert "服务出错了" not in message


async def test_prepare_fresh_turn_aborts_when_channel_dead_skips_llm(
    monkeypatch, workspace_fulfiller
):
    """Sticky-dead before prepare → WorkspaceIOError; build_turn_router never runs."""
    backend = _dead_local()
    llm_calls: list[str] = []

    async def _should_not_build(*_a, **_k):
        llm_calls.append("build")
        raise AssertionError("LLM must not be built when channel is sticky-dead")

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

    with pytest.raises(WorkspaceIOError, match="本机工作区通道无响应"):
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
    assert llm_calls == []
