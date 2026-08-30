"""End-to-end pipeline tests: B2 governance finish_reason → message_end + persistence.

Drives the REAL chat pipeline (``run_chat_pipeline``) — the captain executor, the
``react_loop``, the ``LoopController`` governance, and the pipeline's finish-mapping —
with a scripted LLM provider and a controlled CEO toolset, proving the terminal
``FinishReason`` produced deep in the engine reaches BOTH ends the product reads:

* the ``message_end`` SSE event the client renders live, AND
* the persisted ``runs`` payload (``runs.finish_reason`` — the 唯一事实源 the turn
  journal stores and the bubble replays on reload).

The engine half (``react_loop`` → ``finish_override_sink``) is covered by
``test_engine_governance``; these lock the executor → ``captain_state.finish_override``
→ pipeline (``message_end`` / ``runs``) seam that carries it the rest of the way.

Only 无产出早停 (UNPRODUCTIVE) yields a non-default terminal reason **when the
turn actually ends there**; 工具失败熔断 / 反思注入 are mid-loop steers. A later
force-finalize ``ask_user`` pause supersedes UNPRODUCTIVE (captain takes the last
``finish_override`` stamp — see ``test_checkpoint_finalize_ask_user``). So here we
(1) prove UNPRODUCTIVE rides message_end + persistence when salvage does not pause,
and (2) prove a run that trips the circuit breaker but recovers still surfaces a
clean END_TURN — governance never mis-finishes a recovering turn.
"""

from pathlib import Path
from types import SimpleNamespace

from agentcore.core.types import ToolCategory
from agentcore.llm.provider.protocol import LLMChunk, ToolCallDelta
from agentcore.runtime import pipeline
from agentcore.runtime.events import EventSink, EventType, FinishReason, title_generated
from agentcore.tools.protocol import ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_turn_profiles


def _tool_chunk(name: str, args: str, *, call_id: str = "c") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


class _ScriptedProvider:
    """Yields a pre-scripted list of chunks on each ``stream`` call (one per round)."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk

    async def close(self) -> None:  # the pipeline awaits llm.close() in its finally
        return None


class _StubTool:
    """A CEO tool that reports a fixed success/failure (drives the governance path)."""

    def __init__(self, name: str = "flaky", *, success: bool = False) -> None:
        self._name = name
        self._success = success
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
        if not self._success:
            return ToolResult(tool_call_id="", success=False, output="", error="boom")
        return ToolResult(tool_call_id="", success=True, output="result")


def _patch_pipeline(monkeypatch, provider: _ScriptedProvider, registry: ToolRegistry):
    """Swap ONLY the LLM provider, the memory load, and the CEO toolset assembly for
    controlled doubles — everything that carries the finish_reason (captain executor,
    react_loop, LoopController governance, finish-mapping, message_end, runs payload)
    stays REAL.

    Patch each seam WHERE IT IS LOOKED UP (pytest convention): ``prepare_chat_turn``
    (via ``run_chat_pipeline``) reads ``build_turn_router`` off the package facade
    (``pipeline_pkg.build_turn_router``), but it imports ``default_memory_store``
    and ``_assemble_ceo_toolset`` by name into the ``pipeline.run`` submodule and calls
    them as locals — so those two must be patched on ``run``, not on the package
    (patching the facade would silently miss the local call, and the facade doesn't even
    re-export ``default_memory_store``)."""

    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(pipeline, "build_turn_router", _fake_build_turn_router)

    class _FakeStore:
        async def load(self, _user_id: str, _path: str, scope: str | None = None) -> str:
            return ""

        async def list(self, _user_id: str, scope: str | None = None) -> list:
            # No topic notes ⇒ no 按需目录 / consult on this single-agent path.
            return []

    monkeypatch.setattr("agentcore.runtime.pipeline.run.default_memory_store", lambda: _FakeStore())

    # delegate / debate are unused on this single-agent path, but the pipeline
    # tail folds their usage/ledger/citations — give them empty doubles. The delegate
    # double also needs ``dispose_open_supervised`` (受监督的波循环 P5 Edge): the tail
    # awaits it to release any dangling supervised plan before the usage fold, plus
    # ``collab`` (协作质量 §2.5): the tail spreads ``delegate_tool.collab`` into the turn
    # result, so the double mirrors the real tool's zeroed tally. ``continuation_count``
    # feeds turn_metrics.revises (续派次数).
    async def _noop_dispose() -> None:
        return None

    fake_delegate = SimpleNamespace(
        usage={},
        run_ledger=[],
        citations=[],
        dispose_open_supervised=_noop_dispose,
        collab={"boundary_yields": 0, "scope_signals": 0, "escalations": 0},
        continuation_count=0,
        user_continuation_count=0,
    )
    fake_debate = SimpleNamespace(usage={}, run_ledger=[], citations=[])

    def _fake_assemble(**_kwargs):
        return fake_delegate, fake_debate, registry

    monkeypatch.setattr("agentcore.runtime.pipeline.run._assemble_ceo_toolset", _fake_assemble)


async def _run_pipeline(monkeypatch, provider: _ScriptedProvider, registry: ToolRegistry):
    _patch_pipeline(monkeypatch, provider, registry)
    sink = EventSink()
    backend = ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox())
    result = await pipeline.run_chat_pipeline(
        conversation_id="conv-1",
        user_message="去做点事",
        history=[],
        sink=sink,
        user_id="user-1",
        backend=backend,
        approvals_enabled=False,  # no live client → skip the approval/checkpoint gate
        profile_set=make_turn_profiles(model="chat-model"),
    )
    # run_chat_pipeline no longer closes the sink (its owner does); this test is the
    # owner, so close it to drain the queue to the None sentinel and collect every event.
    sink.close()
    events = [e async for e in sink]
    return result, events


def _message_end(events):
    return next(e for e in events if e.type == EventType.MESSAGE_END)


async def test_pipeline_leaves_sink_open_for_post_turn_tail(monkeypatch):
    """The pipeline must NOT close the sink — its owner (the coordinator) does — so the
    post-turn tail (title_generated, emitted by persist_turn_result AFTER the pipeline
    returns) still reaches the client.

    Regression for dropped post-turn SSE: run_chat_pipeline used to close the sink in
    its finally, so the tail hit an already-closed sink and was silently dropped (emit is a
    no-op once closed). Title survived via its DB write; transport-only events vanished.
    """
    registry = ToolRegistry()
    registry.register(_StubTool(name="noop", success=True))  # unused: one clean content round
    provider = _ScriptedProvider([[_content_chunk("答复")]])
    _patch_pipeline(monkeypatch, provider, registry)

    sink = EventSink()
    backend = ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox())
    await pipeline.run_chat_pipeline(
        conversation_id="conv-1",
        user_message="hi",
        history=[],
        sink=sink,
        user_id="user-1",
        backend=backend,
        approvals_enabled=False,
        profile_set=make_turn_profiles(model="chat-model"),
    )

    # The invariant: the pipeline returned but left the sink OPEN for the owner's tail.
    assert sink._closed is False

    # The tail emit persist_turn_result does after the pipeline still lands on the sink.
    sink.emit(title_generated("帮我把结论整理成一页纪要", conversation_id="conv-1"))
    sink.close()  # the owner (here, the test) closes once the tail is emitted
    types = [e.type async for e in sink]

    assert EventType.TITLE_GENERATED in types
    # …and it arrives AFTER message_end (a genuine post-turn tail, not mid-stream).
    assert types.index(EventType.TITLE_GENERATED) > types.index(EventType.MESSAGE_END)


async def test_unproductive_early_stop_reaches_message_end_and_persisted_runs(
    monkeypatch,
):
    # Three rounds of a failing tool with no content → 无产出早停 → empty inventory
    # skips LLM salvage (content '') but still surfaces UNPRODUCTIVE. The reason must
    # ride BOTH the live message_end event AND the persisted runs payload.
    registry = ToolRegistry()
    registry.register(_StubTool(name="flaky", success=False))
    provider = _ScriptedProvider(
        [
            [_tool_chunk("flaky", '{"q": "a"}')],
            [_tool_chunk("flaky", '{"q": "b"}')],
            [_tool_chunk("flaky", '{"q": "c"}')],
        ]
    )
    result, events = await _run_pipeline(monkeypatch, provider, registry)

    assert result["content"] == ""
    assert result["finish_reason"] == FinishReason.UNPRODUCTIVE
    # 1) the SSE message_end the client reads
    assert _message_end(events).payload["finish_reason"] == FinishReason.UNPRODUCTIVE
    # 2) the persisted journal (journal_entries → turn_end.finish_reason)
    entries = result["journal_entries"]
    assert entries is not None
    assert entries[-1]["payload"]["finish_reason"] == FinishReason.UNPRODUCTIVE.value


async def test_circuit_breaker_run_that_recovers_finishes_end_turn(
    monkeypatch,
):
    # The tool fails 3× with varied args (circuit breaker warns@2 / disables@3) but the
    # model writes content each round (never unproductive) and then answers tool-free →
    # a clean END_TURN. Proves governance steers in a recovering run do NOT corrupt the
    # terminal reason, and the normal finish-mapping rides message_end + persistence.
    registry = ToolRegistry()
    registry.register(_StubTool(name="flaky", success=False))
    provider = _ScriptedProvider(
        [
            [_content_chunk("t0"), _tool_chunk("flaky", '{"q": "a"}')],
            [_content_chunk("t1"), _tool_chunk("flaky", '{"q": "b"}')],
            [_content_chunk("t2"), _tool_chunk("flaky", '{"q": "c"}')],
            [_content_chunk("最终答复")],
        ]
    )
    result, events = await _run_pipeline(monkeypatch, provider, registry)

    assert "最终答复" in result["content"]
    assert result["finish_reason"] == FinishReason.END_TURN
    assert _message_end(events).payload["finish_reason"] == FinishReason.END_TURN
    entries = result["journal_entries"]
    assert entries is not None
    assert entries[-1]["payload"]["finish_reason"] == FinishReason.END_TURN.value
