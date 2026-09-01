"""Per-conversation short-exec serial lock.

Same ``conversation_id`` must not overlap in wall time; different / empty ids may.
The verify kernel deliberately does not take this lock (see code_execute_lock module doc).
"""

from __future__ import annotations

import asyncio
import inspect
import time

import agentcore.tools.builtin.run_verify as run_verify_mod
from agentcore.tools.builtin.code_execute_lock import code_execute_lock
from agentcore.tools.builtin.run_short import execute_short
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult


class _SlowBackend:
    """Mock backend that sleeps ``delay`` inside ``execute`` and logs windows."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.windows: list[tuple[float, float]] = []

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        start = time.monotonic()
        await asyncio.sleep(self.delay)
        end = time.monotonic()
        self.windows.append((start, end))
        return ExecutionResult(
            success=True,
            stdout="ok\n",
            stderr="",
            exit_code=0,
            duration_ms=int((end - start) * 1000),
        )


def _ctx(backend: _SlowBackend, conversation_id: str = "") -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,  # type: ignore[arg-type]
        user_id="u",
        conversation_id=conversation_id,
    )


def _windows_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    # Half-open style: touching endpoints (a.end == b.start) is not overlap.
    return a[0] < b[1] and b[0] < a[1]


async def test_same_conversation_serializes():
    backend = _SlowBackend(delay=0.05)
    args = {"code": "print(1)", "language": "python"}

    await asyncio.gather(
        execute_short(args, _ctx(backend, "conv-same")),
        execute_short(args, _ctx(backend, "conv-same")),
    )

    assert len(backend.windows) == 2
    assert not _windows_overlap(backend.windows[0], backend.windows[1])


async def test_different_conversations_overlap():
    backend = _SlowBackend(delay=0.05)
    args = {"code": "print(1)", "language": "python"}

    await asyncio.gather(
        execute_short(args, _ctx(backend, "conv-a")),
        execute_short(args, _ctx(backend, "conv-b")),
    )

    assert len(backend.windows) == 2
    assert _windows_overlap(backend.windows[0], backend.windows[1])


async def test_empty_conversation_id_does_not_serialize():
    backend = _SlowBackend(delay=0.05)
    args = {"code": "print(1)", "language": "python"}

    await asyncio.gather(
        execute_short(args, _ctx(backend, "")),
        execute_short(args, _ctx(backend, "")),
    )

    assert len(backend.windows) == 2
    assert _windows_overlap(backend.windows[0], backend.windows[1])


async def test_code_execute_lock_empty_is_noop():
    # Direct seam: empty id must not block a concurrent holder of a real key.
    events: list[str] = []

    async def with_cid() -> None:
        async with code_execute_lock("conv-x"):
            events.append("cid-enter")
            await asyncio.sleep(0.03)
            events.append("cid-exit")

    async def empty() -> None:
        async with code_execute_lock(""):
            events.append("empty-enter")
            await asyncio.sleep(0.01)
            events.append("empty-exit")

    await asyncio.gather(with_cid(), empty())
    assert set(events[:2]) == {"cid-enter", "empty-enter"}


def test_verify_kernel_bypasses_code_execute_lock():
    # Lock is mounted only on short-exec — the verify kernel must not import or call it
    # (would serialize minute-level verifies behind short scripts).
    source = inspect.getsource(run_verify_mod)
    assert "code_execute_lock" not in source
    assert "code_execute_lock" not in getattr(run_verify_mod, "__dict__", {})
