"""``react_loop`` abnormal exits: honest cancel reason + terminal sinks still published.

Two facts the loop used to lose on the way out:

- a post-grace hard-timeout kill cancelled with a hardcoded ``"redirect"``, so the
  wire ``run_cancelled.reason`` told the user their worker had been re-tasked when
  it was actually killed on the timeout ceiling;
- the terminal sinks (tool failure facts / controller seed) were published only on
  the two clean returns, so a FAILED / CANCELLED RunState carried an empty
  ``tool_failures`` list and the CEO's「工具失败」section read healthier than the run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agentcore.core.types import ToolEffect, ToolFace
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.timeout_hard import (
    HardTimeoutPhase,
    arm_hard_timeout,
    disarm_hard_timeout,
)
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


class _FailingTool:
    def __init__(self, name: str = "search") -> None:
        self._name = name
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            face=ToolFace.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error="上游 503",
            effect=ToolEffect.CONTINUE,
        )


class _ToolThenBoom:
    """Round 0 calls the failing tool; round 1 raises (raise_on_error → propagates)."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 — duck-typed for the loop
        idx = self.calls
        self.calls += 1
        if idx == 0:
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(index=0, id="c1", function_name="search", arguments_delta="{}")
                ]
            )
            return
        raise RuntimeError("provider down")


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="w-abnormal",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _registry(*tools: Any) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


async def test_tool_failures_exported_when_round_raises():
    tool = _FailingTool()
    out = ReactLoopOut(tool_failures=[], controller_seed_out=[])

    with pytest.raises(RuntimeError, match="provider down"):
        await react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=_ToolThenBoom(),
            tools=_registry(tool),
            sink=EventSink(),
            tool_context=_context(),
            profile=make_profile_params(max_rounds=4),
            turn_model="primary",
            out=out,
            role="worker",
            run_id="w-abnormal",
            raise_on_error=True,
            approval_gate=None,
        )

    assert tool.calls == 1
    # The crash exit publishes the same facts a clean return would (缺口段不得比实际乐观).
    assert [f["tool_name"] for f in out.tool_failures] == ["search"]
    assert out.tool_failures[0]["failure_count"] == 1
    assert out.controller_seed_out and isinstance(out.controller_seed_out[0], dict)


async def _post_grace_guard(run_id: str):
    """Arm a real guard and walk it to「宽限轮已用尽」without touching private state."""
    guard = arm_hard_timeout(run_id, timeout_s=0.01, warn_ratio=0.0, grace_wall_s=600)
    assert guard is not None
    for _ in range(200):
        if guard.phase is HardTimeoutPhase.TIMED_OUT:
            break
        await asyncio.sleep(0.01)
    assert guard.phase is HardTimeoutPhase.TIMED_OUT
    assert guard.begin_grace_round() is True
    guard.end_grace_round()
    assert guard.blocks_new_work() is True
    return guard


async def test_post_grace_hard_timeout_cancels_with_worker_timeout_reason():
    provider = _ToolThenBoom()
    out = ReactLoopOut(controller_seed_out=[])
    guard = await _post_grace_guard("w-hard-timeout")
    try:
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await react_loop(
                messages=[LLMMessage(role="user", content="go")],
                llm=provider,
                tools=_registry(_FailingTool()),
                sink=EventSink(),
                tool_context=_context(),
                profile=make_profile_params(max_rounds=4),
                turn_model="primary",
                out=out,
                role="worker",
                run_id="w-hard-timeout",
                approval_gate=None,
            )
    finally:
        disarm_hard_timeout("w-hard-timeout")

    # The cancel arg IS the wire reason — it must name the timeout, not「改派」.
    assert excinfo.value.args[0] == "worker_timeout"
    assert guard.force_cancel_requested is True
    assert provider.calls == 0  # post-grace bans new LLM work
    assert out.controller_seed_out  # terminal sinks published on the kill path too
