"""Presence-disconnect seeds the file-family into disabled_tools.

Teammates that never hit a disconnect envelope must still stop seeing ``file_*``
when ``session.workspace_channel_dead`` or live hub presence is gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.core.types import ToolCategory
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    clear_active_coordination,
    set_active_coordination,
)
from agentcore.runtime.engine import react_loop
from agentcore.runtime.engine.governance import (
    apply_workspace_channel_dead_retire,
    create_loop_controller,
    is_workspace_channel_sticky_dead,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.loop_controller import LoopController, ToolAttempt
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.channel import WorkspaceChannel
from agentcore.workspace.limits import WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS
from agentcore.workspace.local import LocalWorkspace
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params

pytestmark = pytest.mark.anyio


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


def _tool_chunk(name: str, args: str, *, call_id: str = "c") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


class _StubTool:
    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.FILESYSTEM,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(tool_call_id="", success=True, output="ok")


class _ToolsRecordingProvider:
    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.offered: list[list[str]] = []

    async def stream(self, request):  # noqa: ANN001
        self.offered.append([t["function"]["name"] for t in (request.tools or [])])
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


def _server_ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _absent_local_backend() -> LocalWorkspace:
    """Local channel backend with no fulfiller registered — files unreachable."""
    channel = WorkspaceChannel(
        user_id="u-absent-files",
        conversation_id="conv-dead-retire",
        registry=InteractionRegistry(),
        timeout_seconds=5.0,
        root_id="root-dead",
    )
    return LocalWorkspace(channel)


def test_apply_retire_from_session_workspace_channel_dead():
    """Session sticky flag alone seeds the full retire family (sibling path)."""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="exec-sibling",
        total_workers=2,
        conversation_id="conv-sibling",
    )
    session.workspace_channel_dead = True
    set_active_coordination(session)
    try:
        disabled: set[str] = set()
        controller = create_loop_controller(frozenset())
        assert is_workspace_channel_sticky_dead(_server_ctx()) is True
        assert (
            apply_workspace_channel_dead_retire(
                disabled_tools=disabled,
                controller=controller,
                tool_context=_server_ctx(),
            )
            is True
        )
        for name in WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS:
            assert name in disabled
        assert controller._workspace_channel_dead is True  # noqa: SLF001
        # Idempotent — no further refresh.
        assert (
            apply_workspace_channel_dead_retire(
                disabled_tools=disabled,
                controller=controller,
                tool_context=_server_ctx(),
            )
            is False
        )
    finally:
        clear_active_coordination()


def test_apply_retire_from_backend_channel_is_dead():
    """Worker backend channel sticky-dead seeds retire without a session flag."""
    clear_active_coordination()
    backend = _absent_local_backend()
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,
        user_id="u-absent-files",
        workspace_channel=backend._channel,
    )
    disabled: set[str] = set()
    controller = create_loop_controller(frozenset())
    assert is_workspace_channel_sticky_dead(ctx) is True
    assert (
        apply_workspace_channel_dead_retire(
            disabled_tools=disabled,
            controller=controller,
            tool_context=ctx,
        )
        is True
    )
    assert "file_read" in disabled
    assert "file_write" in disabled
    assert "index_files" in disabled
    assert controller._workspace_channel_dead is True  # noqa: SLF001


def test_backend_write_tools_retire_with_the_file_family():
    """Backend-bound export / land-bytes / read-bytes tools retire with the family.

    Left on the surface they fail on every call (``download_url`` even burns its
    network fetch first; ``read_image`` reads the image bytes before any vision
    call), which is exactly the thrash the family retire prevents.
    """
    clear_active_coordination()
    backend = _absent_local_backend()
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,
        user_id="u-absent-files",
        workspace_channel=backend._channel,
    )
    disabled: set[str] = set()
    assert (
        apply_workspace_channel_dead_retire(
            disabled_tools=disabled,
            controller=create_loop_controller(frozenset()),
            tool_context=ctx,
        )
        is True
    )
    for name in (
        "md_to_docx",
        "md_to_pdf",
        "archive_extract",
        "archive_create",
        "download_url",
        "read_image",
    ):
        assert name in WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS
        assert name in disabled


def test_single_op_timeout_does_not_seed_disabled_family():
    """channel_op (non-sticky) must not latch session or seed the retire family."""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="exec-op",
        total_workers=1,
        conversation_id="conv-op",
    )
    set_active_coordination(session)
    try:
        c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
        c.record(
            [
                ToolAttempt(
                    "op-to",
                    "file_read",
                    success=False,
                    error_summary="活性挂起",
                    meta={
                        "liveness_timeout": True,
                        "timeout_layer": "channel_op",
                    },
                )
            ]
        )
        assert c._workspace_channel_dead is False  # noqa: SLF001
        assert session.workspace_channel_dead is False
        disabled: set[str] = set()
        assert is_workspace_channel_sticky_dead(_server_ctx()) is False
        assert (
            apply_workspace_channel_dead_retire(
                disabled_tools=disabled,
                controller=c,
                tool_context=_server_ctx(),
            )
            is False
        )
        assert disabled == set()
        # Per-tool permanent retire still applies via breaker; family pens stay open.
        cb = c.tool_circuit_breaker()
        assert "file_read" in cb.disabled
        assert "file_write" not in cb.disabled
    finally:
        clear_active_coordination()


async def test_sibling_worker_seeds_disabled_from_session_channel_dead():
    """Fresh react_loop with session sticky must not offer file_* (no dead envelope)."""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="exec-react-sibling",
        total_workers=2,
        conversation_id="conv-react-sibling",
    )
    session.workspace_channel_dead = True
    set_active_coordination(session)
    try:
        reg = ToolRegistry()
        reg.register(_StubTool("file_read"))
        reg.register(_StubTool("file_write"))
        reg.register(_StubTool("other"))
        provider = _ToolsRecordingProvider([[_content_chunk("done")]])
        await react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=provider,
            tools=reg,
            sink=EventSink(),
            tool_context=_server_ctx(),
            profile=make_profile_params(max_rounds=3),
            turn_model="m",
            run_id="sibling-worker",
            role="worker",
            approval_gate=None,
        )
        assert provider.offered
        offered = provider.offered[0]
        assert "other" in offered
        assert "file_read" not in offered
        assert "file_write" not in offered
    finally:
        clear_active_coordination()


async def test_react_loop_round_poll_channel_is_dead_strips_file_family():
    """Alive at entry; mid-team presence stamp → next LLM round drops file family."""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e",
        total_workers=2,
        conversation_id="conv-round-poll",
    )
    set_active_coordination(session)
    try:
        ctx = _server_ctx()
        reg = ToolRegistry()
        reg.register(_StubTool("file_list"))
        reg.register(_StubTool("other"))

        def _mark_dead_after_round0() -> list[LLMMessage]:
            session.workspace_channel_dead = True
            return []

        provider = _ToolsRecordingProvider(
            [
                [_content_chunk("r0"), _tool_chunk("other", "{}")],
                [_content_chunk("done")],
            ]
        )
        await react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=provider,
            tools=reg,
            sink=EventSink(),
            tool_context=ctx,
            profile=make_profile_params(max_rounds=5),
            turn_model="m",
            run_id="poll-worker",
            role="worker",
            on_round_begin=_mark_dead_after_round0,
            approval_gate=None,
        )
        assert len(provider.offered) >= 2
        assert "file_list" in provider.offered[0]
        assert "other" in provider.offered[0]
        assert "file_list" not in provider.offered[1]
        assert "other" in provider.offered[1]
    finally:
        clear_active_coordination()


def test_apply_retire_revives_when_fulfiller_returns():
    """Live fulfiller clears the family retire so write tools are offered again."""
    from agentcore.fulfill.hub import default_fulfiller_hub
    from agentcore.runtime.interaction import InteractionRegistry
    from agentcore.workspace.channel import WorkspaceChannel
    from agentcore.workspace.local import LocalWorkspace

    clear_active_coordination()
    uid = "u-revive-files"
    root = "root-revive"
    channel = WorkspaceChannel(
        user_id=uid,
        conversation_id="conv-revive",
        registry=InteractionRegistry(),
        timeout_seconds=5.0,
        root_id=root,
    )
    backend = LocalWorkspace(channel)
    ctx = ToolContext.create(
        execution_id="e-revive",
        run_id="s",
        agent_id="a",
        backend=backend,
        user_id=uid,
        workspace_channel=channel,
    )
    disabled: set[str] = set()
    controller = create_loop_controller(frozenset())
    assert (
        apply_workspace_channel_dead_retire(
            disabled_tools=disabled,
            controller=controller,
            tool_context=ctx,
        )
        is True
    )
    assert "file_write" in disabled
    hub = default_fulfiller_hub()
    session = hub.register(
        uid, "dev-revive", caps=["workspace"], roots=[root]
    )
    try:
        assert is_workspace_channel_sticky_dead(ctx) is False
        assert (
            apply_workspace_channel_dead_retire(
                disabled_tools=disabled,
                controller=controller,
                tool_context=ctx,
            )
            is True
        )
        assert "file_write" not in disabled
        assert controller._workspace_channel_dead is False  # noqa: SLF001
    finally:
        hub.unregister(session)
