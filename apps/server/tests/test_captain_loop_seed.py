"""Batch-2 turn_paused foundations: captain loop mirror + controller seed + debate latch.

Covers G4/G5 pieces owned by this batch:
  * worker nested react_loop must not clobber the captain mirror (role gate, not
    deliverable_only)
  * controller seed restores latches (has_delegated / audit / debate)
  * note_delegate_batches stamps post_delegate with batch shape (nodes/deps)
  * debate successful returns enter the same post_delegate latch as delegate
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.core.types import ToolCategory
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    ToolCall,
    ToolCallDelta,
    ToolCallFunction,
)
from agentcore.runtime.engine.governance import (
    create_loop_controller,
    note_delegate_batches,
)
from agentcore.runtime.engine.loop import (
    CaptainLoopMirror,
    current_captain_loop,
    react_loop,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.loop_controller import LoopController, ToolAttempt
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


class _ScriptedProvider:
    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _StubTool:
    def __init__(self, name: str = "search") -> None:
        self._name = name
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(tool_call_id="", success=True, output="ok")


def _registry(tool: _StubTool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)
    return reg


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


def test_controller_seed_round_trip_json_safe():
    c = LoopController()
    c.mark_post_delegate(node_count=3, has_deps=True)
    c.mark_audit_gate_fired()
    c.mark_debate_gate_fired()
    c.mark_debate_executed()
    c.mark_turn_token_budget_gate_fired()
    seed = c.export_seed()
    assert seed == {
        "post_delegate": True,
        "delegate_count": 1,
        "audit_gate_fired": True,
        "first_batch_substantial": True,
        "audit_hard_required": False,
        "audit_includes_review": False,
        "debate_gate_fired": True,
        "debate_executed": True,
        "turn_token_budget_gate_fired": True,
        "validation_stopped_fps": [],
        "validation_thrash_latched": False,
    }
    assert all(isinstance(v, (bool, int, list)) for v in seed.values())

    restored = create_loop_controller(frozenset(), seed=seed)
    assert restored.export_seed() == seed
    assert restored.has_delegated is True
    assert restored.delegate_count == 1
    assert restored.audit_gate_fired is True
    assert restored.first_batch_substantial is True
    assert restored.debate_gate_fired is True
    assert restored.debate_executed is True
    assert restored.turn_token_budget_gate_fired is True
    assert restored.validation_thrash_latched is False


def test_note_delegate_batches_batch_shape_sets_first_substantial():
    controller = LoopController()
    args = (
        '{"tasks":[{"id":"a","role":"r","task":"t1"},'
        '{"id":"b","role":"r","task":"t2"},'
        '{"id":"c","role":"r","task":"t3"}]}'
    )
    tool_calls = [
        ToolCall(id="1", function=ToolCallFunction(name="delegate", arguments=args))
    ]
    attempts = [
        ToolAttempt(fingerprint="d", tool_name="delegate", success=True, meta={})
    ]
    note_delegate_batches(controller, tool_calls, attempts)
    assert controller.has_delegated is True
    assert controller.delegate_count == 1
    assert controller.first_batch_substantial is True


def test_note_delegate_batches_light_batch_not_substantial():
    controller = LoopController()
    args = (
        '{"tasks":[{"id":"a","role":"r","task":"t1"},'
        '{"id":"b","role":"r","task":"t2"}]}'
    )
    tool_calls = [
        ToolCall(id="1", function=ToolCallFunction(name="delegate", arguments=args))
    ]
    attempts = [
        ToolAttempt(fingerprint="d", tool_name="delegate", success=True, meta={})
    ]
    note_delegate_batches(controller, tool_calls, attempts)
    assert controller.has_delegated is True
    assert controller.first_batch_substantial is False


def test_note_delegate_batches_debate_counts_as_post_delegate():
    controller = LoopController()
    tool_calls = [
        ToolCall(
            id="1",
            function=ToolCallFunction(
                name="debate",
                arguments='{"motion":"X","form":"debate","sides":[]}',
            ),
        )
    ]
    attempts = [
        ToolAttempt(fingerprint="db", tool_name="debate", success=True, meta={})
    ]
    note_delegate_batches(controller, tool_calls, attempts)
    assert controller.has_delegated is True
    assert controller.delegate_count == 1
    assert controller.first_batch_substantial is False
    assert controller.debate_executed is True


def test_note_delegate_batches_meta_shape_preferred():
    controller = LoopController()
    tool_calls = [
        ToolCall(id="1", function=ToolCallFunction(name="delegate", arguments="{}"))
    ]
    attempts = [
        ToolAttempt(
            fingerprint="d",
            tool_name="delegate",
            success=True,
            meta={"batch_nodes": 4, "batch_has_deps": False},
        )
    ]
    note_delegate_batches(controller, tool_calls, attempts)
    assert controller.first_batch_substantial is True


@pytest.mark.asyncio
async def test_worker_nested_loop_does_not_clobber_captain_mirror():
    """Worker with deliverable_only=True must NOT publish/overwrite captain mirror."""
    captain = LoopController()
    captain.mark_post_delegate(node_count=3, has_deps=False)
    token = current_captain_loop.set(
        CaptainLoopMirror(
            controller=captain,
            content_before_round="pre",
            final_content="captain-body",
        )
    )
    try:
        before = current_captain_loop.get()
        assert before is not None
        assert before.final_content == "captain-body"
        assert before.controller is captain

        provider = _ScriptedProvider([[LLMChunk(delta_content="worker says")]])
        await react_loop(
            messages=[LLMMessage(role="user", content="go")],
            llm=provider,
            tools=_registry(_StubTool()),
            sink=EventSink(),
            tool_context=_context(),
            profile=make_profile_params(max_rounds=2),
            turn_model="m",
            role="worker",
            deliverable_only=True,  # same flag as captain — must NOT gate the mirror
            on_reset=lambda _reason: None,
            approval_gate=None,
        )

        after = current_captain_loop.get()
        assert after is before
        assert after is not None
        assert after.final_content == "captain-body"
        assert after.content_before_round == "pre"
        assert after.controller is captain
        assert after.controller.has_delegated is True
    finally:
        current_captain_loop.reset(token)


@pytest.mark.asyncio
async def test_captain_loop_publishes_and_resets_mirror():
    seen: list[str | None] = []

    class _CapturingProvider(_ScriptedProvider):
        async def stream(self, request):  # noqa: ANN001
            mirror = current_captain_loop.get()
            seen.append(None if mirror is None else mirror.final_content)
            async for chunk in super().stream(request):
                yield chunk

    provider = _CapturingProvider([[LLMChunk(delta_content="hello captain")]])
    assert current_captain_loop.get() is None
    content, *_ = await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(_StubTool()),
        sink=EventSink(),
        tool_context=_context(),
        profile=make_profile_params(max_rounds=2),
        turn_model="m",
        role="captain",
        deliverable_only=True,
        approval_gate=None,
    )
    assert content == "hello captain"
    assert current_captain_loop.get() is None  # finally reset
    assert seen == [""]  # published by round start, before prose join


@pytest.mark.asyncio
async def test_captain_mirror_updates_after_prose_join():
    """Prose join must refresh mirror.final_content before a tool runs."""
    seen: list[str] = []

    class _MirrorSpyTool(_StubTool):
        async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
            mirror = current_captain_loop.get()
            assert mirror is not None
            seen.append(mirror.final_content)
            return await super().execute(arguments, context)

    tool = _MirrorSpyTool()
    provider = _ScriptedProvider(
        [
            [
                LLMChunk(delta_content="pre-tool prose"),
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0, id="c1", function_name="search", arguments_delta="{}"
                        )
                    ]
                ),
            ],
            [LLMChunk(delta_content="final")],
        ]
    )
    content, *_ = await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(tool),
        sink=EventSink(),
        tool_context=_context(),
        profile=make_profile_params(max_rounds=4),
        turn_model="m",
        role="captain",
        approval_gate=None,
    )
    assert "final" in content
    assert seen == ["pre-tool prose"]


@pytest.mark.asyncio
async def test_react_loop_controller_seed_skips_audit_gate():
    """Seeded has_delegated + audit latch → long captain answer is not rewritten."""
    long = "直答" + ("字" * 400)
    seed = {
        "post_delegate": True,
        "delegate_count": 1,
        "audit_gate_fired": True,
        "first_batch_substantial": True,
    }
    provider = _ScriptedProvider([[LLMChunk(delta_content=long)]])
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(_StubTool()),
        sink=EventSink(),
        tool_context=_context(),
        profile=make_profile_params(max_rounds=3),
        turn_model="m",
        role="captain",
        deliverable_only=True,
        controller_seed=seed,
        approval_gate=None,
    )
    assert content == long
    nudges = [
        m
        for m in messages
        if m.role == "user"
        and m.content
        and ("收尾前审计复核" in m.content)
    ]
    assert nudges == []
