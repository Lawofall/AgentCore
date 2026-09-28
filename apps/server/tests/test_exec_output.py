"""Desktop EXECUTE output chunks share the sandbox on_output callback."""

from __future__ import annotations

import pytest

from agentcore.workspace.channel import WorkspaceChannel, WorkspaceOp
from agentcore.workspace.exec_output import (
    bind_exec_output,
    feed_exec_output,
    unbind_exec_output,
)


def test_feed_requires_a_bound_sink_and_a_real_chunk() -> None:
    seen: list[tuple[str, str]] = []
    assert feed_exec_output("missing", "stdout", "hi") is False
    bind_exec_output("op-1", lambda stream, chunk: seen.append((stream, chunk)))
    assert feed_exec_output("op-1", "stdout", "hi") is True
    assert feed_exec_output("op-1", "nope", "hi") is False
    assert feed_exec_output("op-1", "stderr", "") is False
    unbind_exec_output("op-1")
    assert feed_exec_output("op-1", "stdout", "late") is False
    assert seen == [("stdout", "hi")]


def test_feed_caps_a_chunk() -> None:
    seen: list[str] = []
    bind_exec_output("op-2", lambda _stream, chunk: seen.append(chunk))
    assert feed_exec_output("op-2", "stdout", "x" * 9000) is True
    assert len(seen[0]) == 8192
    unbind_exec_output("op-2")


class _Registry:
    request_id = ""

    async def suspend(
        self,
        request_id: str,
        conversation_id: str,
        *,
        kind: object,
        payload: dict | None = None,
        timeout: float | None,
        on_suspended: object = None,
    ) -> dict:
        del conversation_id, kind, payload, timeout, on_suspended
        self.request_id = request_id
        assert feed_exec_output(request_id, "stdout", "live")
        return {"ok": True, "value": {"stdout": "live\n"}}


@pytest.mark.asyncio
async def test_channel_request_feeds_output_until_the_op_settles() -> None:
    seen: list[str] = []
    registry = _Registry()
    channel = WorkspaceChannel(
        user_id="u",
        conversation_id="c",
        registry=registry,  # type: ignore[arg-type]
        timeout_seconds=5,
        root_id="root",
    )
    value = await channel.request(
        WorkspaceOp.EXECUTE,
        {"code": "print(1)", "language": "python"},
        on_output=lambda _stream, chunk: seen.append(chunk),
    )
    assert value == {"stdout": "live\n"}
    assert seen == ["live"]
    assert feed_exec_output(registry.request_id, "stdout", "after") is False
