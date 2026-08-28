"""Tests for the tool approval gate (CEO chat path).

Covers three layers:
  * ``InteractionRegistry`` — the in-process bridge: unknown / double / wrong-
    conversation resolves are refused; a matching resolve settles the Future.
  * ``ApprovalGate`` — per-turn suspension: approve, timeout→deny, deny-reuse
    (no second card), and "approve for the rest of the turn" skipping the second
    prompt; plus the required→resolved event pair.
  * ``react_loop`` integration — a GRANTABLE tool is gated (runs on approve,
    skipped with a denial tool-message on deny; denials do not trip the tool
    circuit breaker), while a non-GRANTABLE tool runs un-gated even when a gate
    is present.
"""

import asyncio
import time
from pathlib import Path

import pytest

from agentcore.config.approval import ApprovalSettings
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.approvals import (
    ApprovalDecision,
    ApprovalGate,
    tool_call_requires_approval,
)
from agentcore.runtime.engine import react_loop
from agentcore.runtime.events import EventSink, EventType, SSEEvent
from agentcore.runtime.interaction import InteractionKind, InteractionRegistry
from agentcore.tools.builtin import (
    build_builtin_registry,
    delegation_grantable_tool_names,
    per_call_tool_names,
)
from agentcore.tools.builtin.test_run import TestRunTool
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params

pytestmark = pytest.mark.anyio


# --- helpers ---------------------------------------------------------------


def _drain(sink: EventSink) -> list[SSEEvent]:
    """Pop every event currently queued on the sink (test inspection)."""
    events: list[SSEEvent] = []
    while not sink._queue.empty():  # noqa: SLF001 - test-only inspection
        events.append(sink._queue.get_nowait())
    return events


async def _resolve_when_ready(
    registry: InteractionRegistry,
    approval_id: str,
    decision: ApprovalDecision,
    conversation_id: str,
) -> None:
    """Resolve ``approval_id`` as soon as it appears pending (public API only).

    Retries via the public ``resolve`` (which is a no-op until the gate has
    registered the request), yielding the loop so the awaiting gate makes
    progress. Avoids reaching into registry internals to detect readiness.
    """
    for _ in range(2000):
        if registry.resolve(approval_id, decision, conversation_id=conversation_id):
            return
        await asyncio.sleep(0)
    raise AssertionError(f"approval {approval_id!r} never became pending")


def _gate(
    sink: EventSink,
    registry: InteractionRegistry,
    *,
    conversation_id: str = "conv-1",
    timeout_seconds: float = 5.0,
    timeout_overrides: dict[str, float] | None = None,
    delegation_grantable_tools: frozenset[str] | None = None,
) -> ApprovalGate:
    return ApprovalGate(
        sink=sink,
        conversation_id=conversation_id,
        registry=registry,
        timeout_seconds=timeout_seconds,
        timeout_overrides=timeout_overrides or {},
        delegation_grantable_tools=delegation_grantable_tools or delegation_grantable_tool_names(),
    )


# --- InteractionRegistry ------------------------------------------------------


async def test_registry_resolve_unknown_returns_false():
    reg = InteractionRegistry()
    assert reg.resolve("nope", ApprovalDecision.APPROVE, conversation_id="c") is False


async def test_registry_rejects_wrong_conversation():
    reg = InteractionRegistry()
    fut = reg.create("a1", "conv-A", kind=InteractionKind.APPROVAL)
    # A resolve claiming a different conversation must not settle the Future.
    assert reg.resolve("a1", ApprovalDecision.APPROVE, conversation_id="conv-B") is False
    assert not fut.done()
    # The owning conversation can.
    assert reg.resolve("a1", ApprovalDecision.APPROVE, conversation_id="conv-A") is True
    assert fut.result() is ApprovalDecision.APPROVE


async def test_registry_double_resolve_returns_false():
    reg = InteractionRegistry()
    reg.create("a1", "c", kind=InteractionKind.APPROVAL)
    assert reg.resolve("a1", ApprovalDecision.DENY, conversation_id="c") is True
    # Already settled → second resolve is rejected.
    assert reg.resolve("a1", ApprovalDecision.APPROVE, conversation_id="c") is False


async def test_registry_discard_forgets_request():
    reg = InteractionRegistry()
    reg.create("a1", "c", kind=InteractionKind.APPROVAL)
    reg.discard("a1")
    assert reg.resolve("a1", ApprovalDecision.APPROVE, conversation_id="c") is False


# --- ApprovalGate ----------------------------------------------------------


async def test_gate_authorize_approve_emits_event_pair():
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "call-1", ApprovalDecision.APPROVE, "conv-1")
    )
    decision = await gate.authorize(
        tool_name="file_write", tool_call_id="call-1", arguments={"path": "a.txt"}
    )
    await resolver

    assert decision is ApprovalDecision.APPROVE
    types = [e.type for e in _drain(sink)]
    assert types == [EventType.APPROVAL_REQUIRED, EventType.APPROVAL_RESOLVED]


async def test_gate_authorize_times_out_to_deny():
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg, timeout_seconds=0.01)

    # No resolver — the request is never answered and must auto-deny.
    decision = await gate.authorize(tool_name="code_execute", tool_call_id="x", arguments={})

    assert decision is ApprovalDecision.DENY
    resolved = [e for e in _drain(sink) if e.type is EventType.APPROVAL_RESOLVED]
    assert resolved and resolved[0].payload["decision"] == ApprovalDecision.DENY


async def test_gate_per_tool_timeout_override():
    """A tool in timeout_overrides waits longer than the gate default."""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(
        sink,
        reg,
        timeout_seconds=0.05,
        timeout_overrides={"file_write": 0.35},
    )

    started = time.monotonic()
    pending = asyncio.create_task(
        gate.authorize(tool_name="file_write", tool_call_id="fw-1", arguments={"path": "a.md"})
    )
    await asyncio.sleep(0.12)
    assert not pending.done()

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "fw-1", ApprovalDecision.APPROVE, "conv-1")
    )
    decision = await pending
    await resolver
    elapsed = time.monotonic() - started

    assert decision is ApprovalDecision.APPROVE
    assert elapsed >= 0.1

    # Other tools still use the short default.
    # Bound is loose under pytest-xdist load (machine contention); not a product SLA.
    t0 = time.monotonic()
    deny = await gate.authorize(tool_name="code_execute", tool_call_id="ce-1", arguments={})
    assert deny is ApprovalDecision.DENY
    assert time.monotonic() - t0 < 1.0


def test_approval_settings_default_infinite_wait():
    """提问确认交互统一 D2：默认无限等，overrides 清空。"""
    settings = ApprovalSettings()
    assert settings.approval_timeout_seconds is None
    assert settings.approval_timeout_overrides == {}
    assert settings.approval_timeout_for("file_write") is None
    assert settings.approval_timeout_for("code_execute") is None


async def test_gate_approve_always_skips_second_prompt():
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "id1", ApprovalDecision.APPROVE_ALWAYS, "conv-1")
    )
    first = await gate.authorize(tool_name="file_write", tool_call_id="id1", arguments={})
    await resolver
    assert first is ApprovalDecision.APPROVE_ALWAYS

    _drain(sink)  # clear the first pair
    # Second call to the SAME tool returns immediately, with no new prompt.
    second = await gate.authorize(tool_name="file_write", tool_call_id="id2", arguments={})
    assert second is ApprovalDecision.APPROVE
    assert _drain(sink) == []


async def test_approve_always_sweeps_pending_same_tool():
    """'本轮内都允许' on one file_write retroactively approves the OTHER file_writes
    already suspended on the shared gate (parallel workers in local mode), so one
    click clears every pending same-tool prompt; a different tool stays gated.

    Closes the race the client's optimistic sibling-approve can miss (a sibling's
    approval_required SSE not yet in the store at click time): the registry is the
    authoritative pending set, so the sweep here catches it regardless.
    """
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)

    # Two file_writes + one code_execute suspended in parallel on the SAME gate.
    a = asyncio.create_task(gate.authorize(tool_name="file_write", tool_call_id="a", arguments={}))
    b = asyncio.create_task(gate.authorize(tool_name="file_write", tool_call_id="b", arguments={}))
    c = asyncio.create_task(
        gate.authorize(tool_name="code_execute", tool_call_id="c", arguments={})
    )
    # Let all three register before the grant, so the sweep can see b and c.
    for _ in range(2000):
        if len(reg.list_pending("conv-1")) == 3:
            break
        await asyncio.sleep(0)
    assert len(reg.list_pending("conv-1")) == 3

    # Grant file_write for the turn on call "a".
    assert reg.resolve("a", ApprovalDecision.APPROVE_ALWAYS, conversation_id="conv-1")

    assert await a is ApprovalDecision.APPROVE_ALWAYS
    # b (same tool) was swept to APPROVE without ever getting its own resolve.
    assert await b is ApprovalDecision.APPROVE
    # c (different tool) is untouched — still pending until resolved on its own.
    assert reg.resolve("c", ApprovalDecision.DENY, conversation_id="conv-1")
    assert await c is ApprovalDecision.DENY


async def test_approve_always_files_grants_whole_class():
    """'本轮内允许所有文件改动' grants the file-mutation class for the turn (so a LATER
    write/edit/delete/move auto-approves) and sweeps every already-suspended file-op
    call — while code_execute, outside the class, stays separately gated.

    Uses always_ask so session file-trust does not short-circuit the file cards
    (that path is covered by test_session_file_trust_*).
    """
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    reg = InteractionRegistry()
    sink = EventSink()
    file_ops = frozenset(
        {
            "file_write",
            "file_append",
            "str_replace",
            "file_delete",
            "file_move",
            "file_copy",
            "mkdir",
            "file_batch",
        }
    )
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        file_op_tools=file_ops,
        permission_axes=recipe_to_axes(AutonomyPolicy.CAUTIOUS),
    )

    # A file_write (the clicked card), a parallel str_replace, and a code_execute.
    w = asyncio.create_task(gate.authorize(tool_name="file_write", tool_call_id="w", arguments={}))
    r = asyncio.create_task(gate.authorize(tool_name="str_replace", tool_call_id="r", arguments={}))
    x = asyncio.create_task(
        gate.authorize(tool_name="code_execute", tool_call_id="x", arguments={})
    )
    for _ in range(2000):
        if len(reg.list_pending("conv-1")) == 3:
            break
        await asyncio.sleep(0)
    assert len(reg.list_pending("conv-1")) == 3

    # Click "allow all file edits" on the file_write card.
    assert reg.resolve("w", ApprovalDecision.APPROVE_ALWAYS_FILES, conversation_id="conv-1")
    assert await w is ApprovalDecision.APPROVE_ALWAYS_FILES
    # str_replace (in the class) was swept to APPROVE without its own resolve.
    assert await r is ApprovalDecision.APPROVE
    # code_execute (NOT in the class) is untouched — still gated until resolved.
    assert reg.resolve("x", ApprovalDecision.DENY, conversation_id="conv-1")
    assert await x is ApprovalDecision.DENY

    # A LATER file_delete (also in the class) is now auto-approved, no new prompt.
    _drain(sink)
    later = await gate.authorize(tool_name="file_delete", tool_call_id="d", arguments={})
    assert later is ApprovalDecision.APPROVE
    assert _drain(sink) == []
    # A LATER code_execute after the earlier deny short-circuits — no second card.
    later_exec = await gate.authorize(tool_name="code_execute", tool_call_id="x2", arguments={})
    assert later_exec is ApprovalDecision.DENY
    assert _drain(sink) == []


async def test_code_execute_approve_always_grants_turn():
    """Cursor-aligned: code_execute may take「本轮内都允许」— APPROVE_ALWAYS writes a
    turn grant and the next code_execute skips the prompt (no longer downgraded)."""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        per_call_tools=frozenset(),  # production wiring: per_call_tool_names() is empty
    )

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "id1", ApprovalDecision.APPROVE_ALWAYS, "conv-1")
    )
    first = await gate.authorize(tool_name="code_execute", tool_call_id="id1", arguments={})
    await resolver
    assert first is ApprovalDecision.APPROVE_ALWAYS

    _drain(sink)
    second = await gate.authorize(tool_name="code_execute", tool_call_id="id2", arguments={})
    assert second is ApprovalDecision.APPROVE  # whitelisted for the turn
    assert _drain(sink) == []


async def test_per_call_tool_grant_downgraded_to_one_shot():
    """Defense in depth: when per_call_tools is non-empty, APPROVE_ALWAYS is still
    downgraded to one-shot and the next call re-prompts."""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        per_call_tools=frozenset({"code_execute"}),
    )

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "id1", ApprovalDecision.APPROVE_ALWAYS, "conv-1")
    )
    first = await gate.authorize(tool_name="code_execute", tool_call_id="id1", arguments={})
    await resolver
    assert first is ApprovalDecision.APPROVE  # downgraded from APPROVE_ALWAYS

    _drain(sink)
    resolver2 = asyncio.create_task(
        _resolve_when_ready(reg, "id2", ApprovalDecision.DENY, "conv-1")
    )
    second = await gate.authorize(tool_name="code_execute", tool_call_id="id2", arguments={})
    await resolver2
    assert second is ApprovalDecision.DENY
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


async def test_per_call_tool_does_not_affect_other_tools_turn_grant():
    """The per-call exemption is scoped to its tools: a file_write APPROVE_ALWAYS still
    whitelists file_write for the turn (the existing batch放行 path is unchanged)."""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        per_call_tools=frozenset({"code_execute"}),
    )
    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "w1", ApprovalDecision.APPROVE_ALWAYS, "conv-1")
    )
    first = await gate.authorize(tool_name="file_write", tool_call_id="w1", arguments={})
    await resolver
    assert first is ApprovalDecision.APPROVE_ALWAYS

    _drain(sink)
    second = await gate.authorize(tool_name="file_write", tool_call_id="w2", arguments={})
    assert second is ApprovalDecision.APPROVE  # whitelisted for the turn, no new prompt
    assert _drain(sink) == []


def test_per_call_tool_names_is_empty_cursor_aligned():
    """Execution tools are no longer forced per-call; turn grants are allowed
    (Cursor-aligned). The helper stays as the injection point for ApprovalGate."""
    names = per_call_tool_names()
    assert names == frozenset()
    assert "code_execute" not in names
    assert "test_run" not in names


def test_test_run_is_governed_by_the_approval_gate():
    """P0 invariant: test_run runs project code through the SAME sandbox chain as
    code_execute, so it must pass the approval gate — it must NOT be NEVER (which slipped
    the gate entirely). Pinned at the class level: its schema is GRANTABLE, so the same
    ``tool_call_requires_approval`` path that gates code_execute gates it.
    Turn grants are allowed (per_call_tool_names empty); file-class grant still excludes it."""
    schema = TestRunTool().schema
    assert schema.approval is ToolApproval.GRANTABLE
    assert schema.category is ToolCategory.EXECUTION
    assert tool_call_requires_approval("test_run", schema.approval, {}) is True
    assert (
        tool_call_requires_approval("code_execute", schema.approval, {}) is True
    )  # same path
    assert "test_run" not in per_call_tool_names()
    assert build_builtin_registry().get("test_run").schema.approval is ToolApproval.GRANTABLE


async def test_gate_truncates_large_argument_preview():
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)

    big = "x" * 5000
    resolver = asyncio.create_task(_resolve_when_ready(reg, "id1", ApprovalDecision.DENY, "conv-1"))
    await gate.authorize(tool_name="file_write", tool_call_id="id1", arguments={"content": big})
    await resolver

    required = next(e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED)
    preview = required.payload["arguments"]["content"]
    assert len(preview) < len(big)
    assert preview.endswith("[truncated]")


async def test_gate_code_execute_code_preview_allows_20k():
    from agentcore.runtime.approvals import _PREVIEW_CODE_EXECUTE_CODE_MAX, _TRUNCATION_SUFFIX

    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)

    code = "c" * (_PREVIEW_CODE_EXECUTE_CODE_MAX + 500)
    purpose = "p" * 800
    resolver = asyncio.create_task(_resolve_when_ready(reg, "id1", ApprovalDecision.DENY, "conv-1"))
    await gate.authorize(
        tool_name="code_execute",
        tool_call_id="id1",
        arguments={"code": code, "purpose": purpose},
    )
    await resolver

    required = next(e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED)
    args = required.payload["arguments"]
    assert args["code"].endswith(_TRUNCATION_SUFFIX)
    assert len(args["code"]) == _PREVIEW_CODE_EXECUTE_CODE_MAX + len(_TRUNCATION_SUFFIX)
    assert args["purpose"].endswith(_TRUNCATION_SUFFIX)
    assert len(args["purpose"]) < len(purpose)


async def test_gate_code_execute_env_values_are_redacted():
    from agentcore.core.secrets import REDACTED

    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)

    resolver = asyncio.create_task(_resolve_when_ready(reg, "id1", ApprovalDecision.DENY, "conv-1"))
    await gate.authorize(
        tool_name="code_execute",
        tool_call_id="id1",
        arguments={
            "code": "print(1)",
            "purpose": "call api",
            "env": {"AGNES_API_KEY": "opaque-secret-value-here"},
        },
    )
    await resolver

    required = next(e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED)
    env = required.payload["arguments"]["env"]
    assert env == {"AGNES_API_KEY": REDACTED}


# --- react_loop integration ------------------------------------------------


def _tool_chunk(name: str, args: str, *, call_id: str = "c") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


class _ScriptedProvider:
    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _GrantableTool:
    """A GRANTABLE stub that records whether it actually executed."""

    def __init__(self, name: str = "file_write") -> None:
        self._name = name
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub grantable",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(tool_call_id="", success=True, output="wrote")


class _NeverGatedTool:
    """A non-GRANTABLE (SEARCH) stub — must run without any approval prompt."""

    def __init__(self, name: str = "search") -> None:
        self._name = name
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub search",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(tool_call_id="", success=True, output="result")


def _registry(tool) -> ToolRegistry:  # noqa: ANN001
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


def _profile():
    return make_profile_params(max_rounds=20)


async def test_engine_gates_grantable_tool_runs_on_approve():
    provider = _ScriptedProvider(
        [[_tool_chunk("file_write", '{"path": "a.txt"}')], [_content_chunk("done")]]
    )
    tool = _GrantableTool()
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "c", ApprovalDecision.APPROVE, "conv-1")
    )
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=_context(),
        profile=_profile(),
        turn_model="m",
        approval_gate=gate,
    )
    await resolver

    assert content == "done"
    assert tool.calls == 1  # approved → executed


async def test_engine_gates_grantable_tool_skips_on_deny():
    provider = _ScriptedProvider(
        [[_tool_chunk("file_write", '{"path": "a.txt"}')], [_content_chunk("ok")]]
    )
    tool = _GrantableTool()
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]

    resolver = asyncio.create_task(_resolve_when_ready(reg, "c", ApprovalDecision.DENY, "conv-1"))
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=_context(),
        profile=_profile(),
        turn_model="m",
        approval_gate=gate,
    )
    await resolver

    assert content == "ok"
    assert tool.calls == 0  # denied → never executed
    # The model was told, via a tool message, that the call was not authorized.
    denial = [m for m in messages if m.role == "tool" and "未获用户授权" in (m.content or "")]
    assert len(denial) == 1


async def test_denied_tool_skips_reprompt_on_later_call():
    """After an explicit deny, a later call to the same tool must not re-open a card."""
    provider = _ScriptedProvider(
        [
            [_tool_chunk("file_write", '{"path": "a.txt"}', call_id="c1")],
            [_tool_chunk("file_write", '{"path": "b.txt"}', call_id="c2")],
            [_content_chunk("done")],
        ]
    )
    tool = _GrantableTool()
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "c1", ApprovalDecision.DENY, "conv-1")
    )
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=_context(),
        profile=_profile(),
        turn_model="m",
        approval_gate=gate,
    )
    await resolver

    assert content == "done"
    assert tool.calls == 0
    events = _drain(sink)
    required = [e for e in events if e.type is EventType.APPROVAL_REQUIRED]
    assert len(required) == 1  # second call short-circuited — no new card
    denials = [m for m in messages if m.role == "tool" and "未获用户授权" in (m.content or "")]
    assert len(denials) == 2


async def test_approval_denials_do_not_trip_circuit_breaker():
    """User/timeout denials are policy failures — tool stays offered after ≥3 refuses."""

    class _RecordingProvider:
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

    provider = _RecordingProvider(
        [
            [_content_chunk("t0"), _tool_chunk("file_write", '{"path": "a"}', call_id="c1")],
            [_content_chunk("t1"), _tool_chunk("file_write", '{"path": "b"}', call_id="c2")],
            [_content_chunk("t2"), _tool_chunk("file_write", '{"path": "c"}', call_id="c3")],
            [_content_chunk("done")],
        ]
    )
    tool = _GrantableTool()
    reg = InteractionRegistry()
    sink = EventSink()
    # First call denied by user; later calls short-circuit via _denied (no more cards).
    gate = _gate(sink, reg)
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "c1", ApprovalDecision.DENY, "conv-1")
    )
    await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=_context(),
        profile=make_profile_params(max_rounds=20),
        turn_model="m",
        approval_gate=gate,
    )
    await resolver

    assert tool.calls == 0
    # Circuit breaker must NOT remove file_write — denials are governance, not exec fails.
    assert all("file_write" in offered for offered in provider.offered)
    steers = [m.content or "" for m in messages if m.role == "user"]
    assert not any("停用" in s for s in steers)


async def test_engine_does_not_gate_non_grantable_tool():
    provider = _ScriptedProvider([[_tool_chunk("search", '{"q": "x"}')], [_content_chunk("done")]])
    tool = _NeverGatedTool()
    sink = EventSink()
    # Gate present but the SEARCH tool is not GRANTABLE → must run un-gated.
    # A tiny timeout proves we never awaited an approval (would otherwise deny).
    gate = _gate(sink, InteractionRegistry(), timeout_seconds=0.01)
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]

    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=_context(),
        profile=_profile(),
        turn_model="m",
        approval_gate=gate,
    )

    assert content == "done"
    assert tool.calls == 1
    assert not any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


async def test_kickoff_grant_via_gate_api():
    """开工卡 grant is recorded on the gate (hot-path request_delegation_authorization retired)."""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)

    gate.grant_delegation("exec-1")
    assert gate.has_delegation_grant("exec-1")
    assert "exec-1" in gate._delegation_grants  # noqa: SLF001

    decision = await gate.authorize(
        tool_name="code_execute",
        tool_call_id="ce-1",
        arguments={"code": "print(1)"},
        execution_id="exec-1",
    )
    assert decision is ApprovalDecision.APPROVE
    assert _drain(sink) == []

    decision2 = await gate.authorize(
        tool_name="test_run",
        tool_call_id="tr-1",
        arguments={},
        execution_id="exec-1",
    )
    assert decision2 is ApprovalDecision.APPROVE


async def test_kickoff_grant_covers_terminal_start():
    """B · 开工已授执行类后，同 execution 的 terminal start 静默（不逐次弹门）。"""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)
    gate.grant_delegation("exec-1")

    first = await gate.authorize(
        tool_name="terminal",
        tool_call_id="term-1",
        arguments={"subcommand": "start", "command": "pnpm dev"},
        execution_id="exec-1",
    )
    second = await gate.authorize(
        tool_name="terminal",
        tool_call_id="term-2",
        arguments={"subcommand": "start", "command": "pnpm build"},
        execution_id="exec-1",
    )
    assert first is ApprovalDecision.APPROVE
    assert second is ApprovalDecision.APPROVE
    assert _drain(sink) == []


async def test_approve_once_still_reprompts_terminal():
    """用户主动「允许一次」后，同工具仍可再次出卡（能力保留）。"""
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg, timeout_seconds=5.0)

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "term-1", ApprovalDecision.APPROVE, "conv-1")
    )
    first = await gate.authorize(
        tool_name="terminal",
        tool_call_id="term-1",
        arguments={"subcommand": "start", "command": "a"},
        execution_id="exec-1",
    )
    await resolver
    assert first is ApprovalDecision.APPROVE
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))

    resolver2 = asyncio.create_task(
        _resolve_when_ready(reg, "term-2", ApprovalDecision.APPROVE, "conv-1")
    )
    second = await gate.authorize(
        tool_name="terminal",
        tool_call_id="term-2",
        arguments={"subcommand": "start", "command": "b"},
        execution_id="exec-1",
    )
    await resolver2
    assert second is ApprovalDecision.APPROVE
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


async def test_delegation_grant_skips_code_execute_approval():
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg)
    from agentcore.runtime.approvals import DelegationGrant

    gate._delegation_grants["exec-1"] = DelegationGrant(execution_id="exec-1")  # noqa: SLF001

    decision = await gate.authorize(
        tool_name="code_execute",
        tool_call_id="ce-1",
        arguments={"code": "print(1)"},
        execution_id="exec-1",
    )
    assert decision is ApprovalDecision.APPROVE
    assert _drain(sink) == []


async def test_always_ask_policy_ignores_kickoff_grant():
    """autonomy=always_ask（安全权限与治理 §三）：开工卡授权不短路——每个可授权调用仍出卡。"""
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=recipe_to_axes(AutonomyPolicy.CAUTIOUS),
    )
    gate.grant_delegation("exec-1")
    assert gate.has_delegation_grant("exec-1")  # the grant exists…

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "ce-1", ApprovalDecision.APPROVE, "conv-1")
    )
    decision = await gate.authorize(
        tool_name="code_execute",
        tool_call_id="ce-1",
        arguments={"code": "print(1)"},
        execution_id="exec-1",
    )
    await resolver

    assert decision is ApprovalDecision.APPROVE
    # …but always_ask still surfaced the card (no silent short-circuit).
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


async def test_delegation_grant_revoked_restores_per_call():
    reg = InteractionRegistry()
    sink = EventSink()
    gate = _gate(sink, reg, timeout_seconds=0.01)
    from agentcore.runtime.approvals import DelegationGrant

    gate._delegation_grants["exec-1"] = DelegationGrant(execution_id="exec-1")  # noqa: SLF001
    gate.revoke_delegation("exec-1")

    decision = await gate.authorize(
        tool_name="code_execute",
        tool_call_id="ce-1",
        arguments={},
        execution_id="exec-1",
    )
    assert decision is ApprovalDecision.DENY


def test_delegation_grantable_tool_names_includes_execution_and_file_ops():
    names = delegation_grantable_tool_names()
    assert "code_execute" in names
    assert "test_run" in names
    assert "terminal" in names
    assert "git" in names
    assert "file_write" in names


async def test_session_file_trust_skips_mkdir_under_first_grant():
    """开工授权：文件改动类会话信任，不必等开工卡（对齐 Composer 心智）。"""
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes
    from agentcore.tools.builtin import approval_class_tool_names

    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT),
    )

    decision = await gate.authorize(
        tool_name="mkdir",
        tool_call_id="mk-1",
        arguments={"path": "AgentCore/文档/research/设计"},
    )
    assert decision is ApprovalDecision.APPROVE
    assert _drain(sink) == []


async def test_session_file_trust_still_prompts_permanent_delete():
    """永久删除不在会话文件信任内——仍出审批卡。"""
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes
    from agentcore.tools.builtin import approval_class_tool_names

    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT),
    )

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "del-1", ApprovalDecision.APPROVE, "conv-1")
    )
    decision = await gate.authorize(
        tool_name="file_delete",
        tool_call_id="del-1",
        arguments={"path": "tmp.txt", "permanent": True},
    )
    await resolver
    assert decision is ApprovalDecision.APPROVE
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


async def test_session_file_trust_still_prompts_git_push():
    """Structured git push is remote publish — not covered by file_write=session."""
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes
    from agentcore.tools.builtin import approval_class_tool_names

    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT),
    )

    assert gate.will_prompt(
        tool_name="git", arguments={"subcommand": "push", "remote": "origin"}
    )
    # Local writes still session-trusted under LESS_INTERRUPT.
    assert not gate.will_prompt(
        tool_name="git", arguments={"subcommand": "commit", "message": "x"}
    )

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "push-1", ApprovalDecision.APPROVE, "conv-1")
    )
    decision = await gate.authorize(
        tool_name="git",
        tool_call_id="push-1",
        arguments={"subcommand": "push", "remote": "origin"},
    )
    await resolver
    assert decision is ApprovalDecision.APPROVE
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


async def test_delegation_grant_does_not_cover_git_push():
    """Kickoff/delegation grant covers git writes except push / create_pr."""
    from agentcore.core.types import (
        CommandAxis,
        FileWriteAxis,
        HostAxis,
        PermissionAxes,
    )
    from agentcore.tools.builtin import approval_class_tool_names

    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=PermissionAxes(
            file_write=FileWriteAxis.SESSION,
            command=CommandAxis.AUTO,
            host=HostAxis.ASK,
        ),
    )
    gate.grant_delegation("exec-1")
    assert not gate.will_prompt(
        tool_name="git",
        arguments={"subcommand": "add", "paths": ["a"]},
        execution_id="exec-1",
    )
    assert gate.will_prompt(
        tool_name="git",
        arguments={"subcommand": "push"},
        execution_id="exec-1",
    )
    assert gate.will_prompt(
        tool_name="git",
        arguments={"subcommand": "create_pr", "title": "x"},
        execution_id="exec-1",
    )


async def test_session_host_trust_still_prompts_package_install():
    """host(action=install_package) is always-confirm — not covered by host=session."""
    from agentcore.core.types import (
        CommandAxis,
        FileWriteAxis,
        HostAxis,
        PermissionAxes,
    )
    from agentcore.tools.registration import host_class_tool_names

    reg = InteractionRegistry()
    sink = EventSink()
    host_tools = host_class_tool_names()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        host_class_tools=host_tools,
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=PermissionAxes(
            file_write=FileWriteAxis.SESSION,
            command=CommandAxis.AUTO,
            host=HostAxis.SESSION,
        ),
    )

    # Ordinary Host GRANTABLE action is session-trusted under host=session.
    assert "host" in host_tools
    assert not gate.will_prompt(
        tool_name="host", arguments={"action": "open_settings", "panel": "sound"}
    )
    # Package install still prompts (恒确认).
    assert gate.will_prompt(
        tool_name="host",
        arguments={
            "action": "install_package",
            "manager": "winget",
            "package_id": "Microsoft.VisualStudioCode",
        },
    )

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "pkg-1", ApprovalDecision.APPROVE, "conv-1")
    )
    decision = await gate.authorize(
        tool_name="host",
        tool_call_id="pkg-1",
        arguments={
            "action": "install_package",
            "manager": "winget",
            "package_id": "Microsoft.VisualStudioCode",
        },
    )
    await resolver
    assert decision is ApprovalDecision.APPROVE
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))

    # Turn grant from APPROVE_ALWAYS is refused — second call still prompts.
    gate2 = ApprovalGate(
        sink=EventSink(),
        conversation_id="conv-2",
        registry=InteractionRegistry(),
        timeout_seconds=5.0,
        host_class_tools=host_tools,
        permission_axes=PermissionAxes(
            file_write=FileWriteAxis.SESSION,
            command=CommandAxis.AUTO,
            host=HostAxis.SESSION,
        ),
    )
    reg2 = gate2.registry
    resolver2 = asyncio.create_task(
        _resolve_when_ready(reg2, "pkg-2", ApprovalDecision.APPROVE_ALWAYS, "conv-2")
    )
    d1 = await gate2.authorize(
        tool_name="host",
        tool_call_id="pkg-2",
        arguments={"action": "install_package", "manager": "brew", "package_id": "git"},
    )
    await resolver2
    assert d1 is ApprovalDecision.APPROVE
    assert "host" not in gate2._granted
    assert gate2.will_prompt(
        tool_name="host",
        arguments={"action": "install_package", "manager": "brew", "package_id": "wget"},
    )


async def test_session_file_trust_does_not_cover_code_execute():
    """执行类仍需开工卡 / 逐次审批，不被文件会话信任短路。"""
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes
    from agentcore.tools.builtin import approval_class_tool_names

    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT),
    )

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "ce-1", ApprovalDecision.APPROVE, "conv-1")
    )
    decision = await gate.authorize(
        tool_name="code_execute",
        tool_call_id="ce-1",
        arguments={"code": "print(1)"},
    )
    await resolver
    assert decision is ApprovalDecision.APPROVE
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


async def test_observe_policy_ignores_session_file_trust():
    """只观察：文件会话信任关闭，mkdir 仍出卡。"""
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes
    from agentcore.tools.builtin import approval_class_tool_names

    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=recipe_to_axes(AutonomyPolicy.CAUTIOUS),
    )

    resolver = asyncio.create_task(
        _resolve_when_ready(reg, "mk-1", ApprovalDecision.APPROVE, "conv-1")
    )
    decision = await gate.authorize(
        tool_name="mkdir",
        tool_call_id="mk-1",
        arguments={"path": "docs"},
    )
    await resolver
    assert decision is ApprovalDecision.APPROVE
    assert any(e.type is EventType.APPROVAL_REQUIRED for e in _drain(sink))


def test_host_actions_require_approval_like_git_writes():
    """host schema is NEVER; GRANTABLE actions elevate at runtime."""
    from agentcore.tools.builtin.host import HostTool

    schema = HostTool().schema
    assert schema.approval is ToolApproval.NEVER
    assert not tool_call_requires_approval("host", schema.approval, {"action": "status"})
    assert not tool_call_requires_approval("host", schema.approval, {"action": "os_log"})
    assert tool_call_requires_approval(
        "host", schema.approval, {"action": "shell", "command": "echo hi"}
    )
    assert tool_call_requires_approval(
        "host",
        schema.approval,
        {"action": "install_package", "manager": "winget", "package_id": "git"},
    )


def test_terminal_start_requires_approval_like_git_writes():
    """terminal schema is NEVER; only start is gated (git write subcommand posture)."""
    from agentcore.tools.builtin.terminal import TerminalTool

    schema = TerminalTool().schema
    assert schema.approval is ToolApproval.NEVER
    assert tool_call_requires_approval(
        "terminal", schema.approval, {"subcommand": "start", "command": "pnpm dev"}
    )
    assert not tool_call_requires_approval(
        "terminal", schema.approval, {"subcommand": "list"}
    )


# --- will_prompt peek (awaiting_approval telemetry) ---------------------------


def test_will_prompt_matrix_short_circuits_and_force():
    """will_prompt mirrors authorize opening short-circuits; force always prompts."""
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes
    from agentcore.runtime.approvals import DelegationGrant
    from agentcore.tools.builtin import approval_class_tool_names

    reg = InteractionRegistry()
    sink = EventSink()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-1",
        registry=reg,
        timeout_seconds=5.0,
        file_op_tools=approval_class_tool_names(),
        delegation_grantable_tools=delegation_grantable_tool_names(),
        permission_axes=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT),
    )

    # Baseline: GRANTABLE with no short-circuit → would prompt.
    assert gate.will_prompt(tool_name="code_execute", arguments={}) is True

    # Session file trust (LESS_INTERRUPT) covers reversible file ops.
    assert gate.will_prompt(tool_name="mkdir", arguments={"path": "docs/x"}) is False
    # Permanent delete still prompts under session file trust.
    assert (
        gate.will_prompt(
            tool_name="file_delete",
            arguments={"path": "tmp.txt", "permanent": True},
        )
        is True
    )

    # Kickoff / delegation grant covers medium-risk for that execution_id.
    gate._delegation_grants["exec-1"] = DelegationGrant(execution_id="exec-1")  # noqa: SLF001
    assert (
        gate.will_prompt(
            tool_name="code_execute",
            arguments={},
            execution_id="exec-1",
        )
        is False
    )
    # force bypasses kickoff / session short-circuits.
    assert (
        gate.will_prompt(
            tool_name="code_execute",
            arguments={},
            execution_id="exec-1",
            force=True,
        )
        is True
    )
    assert (
        gate.will_prompt(
            tool_name="mkdir",
            arguments={"path": "docs/x"},
            force=True,
        )
        is True
    )

    # Turn-wide grant / deny skip the card.
    gate._granted.add("code_execute")  # noqa: SLF001
    assert gate.will_prompt(tool_name="code_execute", arguments={}) is False
    gate._granted.clear()  # noqa: SLF001
    gate._denied.add("file_write")  # noqa: SLF001
    assert gate.will_prompt(tool_name="file_write", arguments={"path": "a"}) is False
