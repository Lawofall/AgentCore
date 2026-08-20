"""Captain crash salvage keeps the run's real shape (rounds + 收口档位).

A CEO that dies on round 30 used to be reported as a「0 轮」run that somehow took
minutes, and a finish stamp the loop had already made (DEGRADED / UNPRODUCTIVE /
PAUSED) silently fell back to the rounds-derived default. Both facts exist on the
loop's out-param bag before the crash — the salvage path must read them.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.events import EventSink, EventType, FinishReason
from agentcore.runtime.runs.executor.captain import _drive_captain_loop
from agentcore.runtime.runs.types import RunKind, RunPhase, RunSpec
from agentcore.tools.protocol import ToolContext
from tests.llm_helpers import make_profile_params


def _spec(run_id: str) -> RunSpec:
    return RunSpec(
        run_id=run_id,
        agent_id=run_id,
        agent_name="CEO",
        kind=RunKind.CAPTAIN,
        task="带队干活",
        role="CEO",
        depth=0,
        parent_run_id=None,
    )


def _tool_ctx(run_id: str) -> ToolContext:
    return ToolContext.create(
        execution_id="exec-salvage",
        run_id=run_id,
        agent_id=run_id,
        backend=MagicMock(),
        user_id="u1",
        conversation_id="c1",
    )


async def _drive(monkeypatch: Any, fake_loop: Any, run_id: str):
    monkeypatch.setattr(
        "agentcore.runtime.runs.executor.captain.react_loop",
        fake_loop,
    )
    monkeypatch.setattr(
        "agentcore.runtime.browser.registry.default_browser_session_registry",
        lambda: MagicMock(unbind_run=lambda _rid: 0),
    )
    spec = _spec(run_id)
    return await _drive_captain_loop(
        spec=spec,
        messages=[],
        received_blocks=[],
        llm=MagicMock(),
        tools=MagicMock(),
        sink=MagicMock(),
        tool_ctx=_tool_ctx(spec.run_id),
        profile=make_profile_params(),
        turn_model="test-model",
        citation_sink=[],
        approval_gate=None,
    )


@pytest.mark.asyncio
async def test_captain_crash_reports_rounds_reached_and_keeps_finish_override(
    monkeypatch: Any,
) -> None:
    async def _crash_late(**kwargs: Any):
        out = kwargs["out"]
        out.rounds[:] = [30]  # the loop stamps this at each round's TOP
        out.usage.append(TokenUsage(input_tokens=1000, output_tokens=200))
        out.finish_override.append(FinishReason.DEGRADED)
        raise RuntimeError("captain crashed on round 30")

    state = await _drive(monkeypatch, _crash_late, "captain-late-crash")

    assert state.phase is RunPhase.FAILED
    assert state.rounds == 30
    assert state.finish_override is FinishReason.DEGRADED
    assert state.usage.get("input") == 1000  # B-deep 失败计费 unchanged


@pytest.mark.asyncio
async def test_captain_crash_before_first_round_reports_zero(monkeypatch: Any) -> None:
    """No round ever started → 0 is the honest number, and no stamp is invented."""

    async def _crash_early(**_kwargs: Any):
        raise RuntimeError("captain crashed before round 1")

    state = await _drive(monkeypatch, _crash_early, "captain-early-crash")

    assert state.phase is RunPhase.FAILED
    assert state.rounds == 0
    assert state.finish_override is None


@pytest.mark.asyncio
async def test_captain_mid_cancel_emits_run_cancelled(monkeypatch: Any) -> None:
    """CEO 中途停止：run_started 之后必须有 run_cancelled（CancelledError 不能空穿）。"""

    async def _hang(**_kwargs: Any):
        await asyncio.sleep(30)
        raise AssertionError("should have been cancelled")

    monkeypatch.setattr(
        "agentcore.runtime.runs.executor.captain.react_loop",
        _hang,
    )
    monkeypatch.setattr(
        "agentcore.runtime.browser.registry.default_browser_session_registry",
        lambda: MagicMock(unbind_run=lambda _rid: 0),
    )
    sink = EventSink()
    spec = _spec("captain-stop")
    task = asyncio.create_task(
        _drive_captain_loop(
            spec=spec,
            messages=[],
            received_blocks=[],
            llm=MagicMock(),
            tools=MagicMock(),
            sink=sink,
            tool_ctx=_tool_ctx(spec.run_id),
            profile=make_profile_params(),
            turn_model="test-model",
            citation_sink=[],
            approval_gate=None,
        )
    )
    for _ in range(200):
        if any(
            e.type is EventType.RUN_STARTED and e.payload.get("run_id") == "captain-stop"
            for e in sink._history  # noqa: SLF001
        ):
            break
        await asyncio.sleep(0.01)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pytest.fail("captain never emitted run_started")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    cancelled = [
        e
        for e in sink._history  # noqa: SLF001
        if e.type is EventType.RUN_CANCELLED and e.payload.get("run_id") == "captain-stop"
    ]
    assert len(cancelled) == 1
    assert cancelled[0].payload.get("reason") == "stop"
    assert not any(e.type is EventType.RUN_FAILED for e in sink._history)  # noqa: SLF001
