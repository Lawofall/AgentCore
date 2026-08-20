"""挂起即收口 (②): the ask_user 收口 backend slice.

Pins the finalize-at-pause behavior that collapses the live/durable dual-state. A
blocking ``ask_user`` PERSISTS its durable frame and then ENDS the turn
(``FinishReason.PAUSED``) instead of parking on the in-memory interaction Future — so
EVERY resolution (even in-session) flows through the one cold ``POST .../resume`` path.
A pause whose frame could NOT be saved falls back to the in-memory blocking suspend
(§六-1 narrow fallback), asserted here as the negative case.

Three layers:
- the tool: returns SUSPEND only when the flag is ON AND a resumable frame ACTUALLY
  saved; otherwise it falls through to the in-memory wait (never finalize a turn it
  could not later resume).
- the engine: a SUSPEND terminal ends the loop on PAUSED, leaving the call PENDING (no
  tool message, no §8.3 tool_call fact) so ``window_from_journal`` folds back to a
  transcript ending at the assistant — the exact resume-window source the blocking
  pause produced.
- the persist tail: PAUSED writes a best-effort assistant snapshot (``paused: True``)
  so a refresh replays text / journal; cost / metrics wait until resume completes.
"""

import json
from pathlib import Path

from agentcore.core.types import ToolCategory, ToolEffect
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink, EventType, FinishReason, SSEEvent
from agentcore.runtime.facts import FactKind, TurnFactLog, TurnStartedFact, current_fact_log
from agentcore.runtime.journal import runs_from_entries, window_from_journal
from agentcore.runtime.runs.executor.shared import resolve_finish_override
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


class _ScriptedProvider:
    """Yields one pre-scripted chunk list per ``stream`` call (one call per round)."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="cap",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
    )


def _ask_tool(saver, deleter, sink: EventSink) -> AskUserTool:
    """A fully-wired live-CEO ask_user (the only construction that can persist a frame)."""
    return AskUserTool(
        sink=sink,
        conversation_id="c1",
        timeout_seconds=1.0,
        captain_run_id="cap",
        base_system_prompt="你是 CEO。",
        user_message="A 还是 B?",
        message_id="m1",
        suspension_saver=saver,
        suspension_deleter=deleter,
    )


def _drain(sink: EventSink) -> list[SSEEvent]:
    out: list[SSEEvent] = []
    while not sink._queue.empty():  # noqa: SLF001 - test-only inspection
        out.append(sink._queue.get_nowait())
    return out


def _tool_chunk(name: str, args: str, *, call_id: str) -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(
                index=0,
                id=call_id,
                function_name=name,
                arguments_delta=args,
            )
        ]
    )


class _FailOrOkTool:
    """Scripted CEO tool: success once, then failures (drives unproductive streak)."""

    def __init__(self, name: str, *, succeed_first: int = 1) -> None:
        self._name = name
        self._succeed_first = succeed_first
        self.calls = 0
        self._schema = ToolSchema(
            name=name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        if self.calls <= self._succeed_first:
            return ToolResult(tool_call_id="", success=True, output="ok")
        return ToolResult(tool_call_id="", success=False, output="", error="boom")


# --- the tool: finalize only when ON and a frame actually saved --------------------


async def test_finalize_returns_suspend_and_skips_the_wait():
    # A resumable frame saved (transcript published) ⇒ the tool ends the turn in place: a
    # SUSPEND result, the durable frame persisted, the card surfaced — no in-memory Future.
    frames: list = []

    async def saver(frame) -> None:  # noqa: ANN001 - TurnSuspension
        frames.append(frame)

    async def deleter(_message_id: str) -> None:
        return None

    sink = EventSink()
    tool = _ask_tool(saver, deleter, sink)
    token = captain_transcript.set([LLMMessage(role="user", content="A 还是 B?")])
    try:
        res = await tool.execute(
            {
                "message": "A 还是 B?",
                "assumptions": [{"label": "默认", "value": "A"}],
            },
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)

    assert res.effect is ToolEffect.SUSPEND
    assert res.is_terminal is True
    assert res.final_text is None  # no answer produced — the turn awaits /resume
    assert len(frames) == 1  # the durable resume frame was saved
    # The card surfaced so the client can render the (single) resume prompt.
    assert any(e.type is EventType.CHECKPOINT_REQUIRED for e in _drain(sink))


async def test_ask_user_browser_login_wire_flag():
    """CEO ask_user(browser_login=true) → checkpoint_required.browser_login + frame flag."""
    frames: list = []

    async def saver(frame) -> None:  # noqa: ANN001
        frames.append(frame)

    async def deleter(_message_id: str) -> None:
        return None

    sink = EventSink()
    tool = _ask_tool(saver, deleter, sink)
    token = captain_transcript.set([LLMMessage(role="user", content="请登录")])
    try:
        res = await tool.execute(
            {
                "message": "请在右坞浏览器完成登录后继续",
                "browser_login": True,
            },
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)

    assert res.effect is ToolEffect.SUSPEND
    assert len(frames) == 1
    assert frames[0].browser_login is True
    events = _drain(sink)
    cp = next(e for e in events if e.type is EventType.CHECKPOINT_REQUIRED)
    assert cp.payload.get("browser_login") is True
    # Schema advertises the field; escalate stays off CEO toolset (catalog test elsewhere).
    assert "browser_login" in tool.schema.parameters["properties"]


async def test_finalize_fails_explicitly_when_frame_not_saved():
    # D11：无 transcript ⇒ 无法落盘 ⇒ 显式失败（不再窄兜底假等待）。
    frames: list = []

    async def saver(frame) -> None:  # noqa: ANN001
        frames.append(frame)

    async def deleter(_message_id: str) -> None:
        return None

    tool = _ask_tool(saver, deleter, EventSink())
    res = await tool.execute(
        {
            "message": "A 还是 B?",
            "assumptions": [{"label": "默认", "value": "A"}],
        },
        _ctx(),
    )

    assert frames == []
    assert res.effect is not ToolEffect.SUSPEND
    assert res.success is False


# --- the engine: a SUSPEND terminal ends the loop on PAUSED, call left pending ------


async def test_loop_finalizes_ask_user_to_paused():
    # Drive the REAL captain loop with the REAL AskUserTool. The loop must end on
    # FinishReason.PAUSED with the suspended call PENDING — and the journal the face persisted
    # must fold back to the transcript ending at the assistant (the resume window source),
    # byte-for-byte the same shape the blocking pause produces.
    system_prompt = "你是 CEO。"
    user_message = "A 还是 B?"
    captured: dict[str, object] = {}

    async def saver(frame) -> None:  # noqa: ANN001 - TurnSuspension
        captured["transcript"] = list(frame.transcript)
        captured["journal_entries"] = list(frame.journal_entries)

    async def deleter(_message_id: str) -> None:
        captured["deleted"] = True

    sink = EventSink()
    ask_tool = AskUserTool(
        sink=sink,
        conversation_id="c1",
        timeout_seconds=1.0,
        captain_run_id="cap",
        base_system_prompt=system_prompt,
        user_message=user_message,
        message_id="m1",
        suspension_saver=saver,
        suspension_deleter=deleter,
    )
    reg = ToolRegistry()
    reg.register(ask_tool)

    provider = _ScriptedProvider(
        [
            [
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="call_ask",
                            function_name="ask_user",
                            arguments_delta=(
                                f'{{"message": "{user_message}", '
                                f'"assumptions": [{{"label": "默认", "value": "A"}}]}}'
                            ),
                        )
                    ]
                )
            ]
        ]
    )

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_message),
    ]
    profile = make_profile_params(max_rounds=5)
    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(
            system_prompt=system_prompt, user_message=user_message, model_profile="m"
        ).to_fact()
    )
    finish_override: list[FinishReason] = []
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(messages)
    try:
        content, _reasoning, _usage, _rounds = await react_loop(
            messages=messages,
            llm=provider,
            tools=reg,
            sink=sink,
            tool_context=_ctx(),
            profile=profile,
            turn_model="m",
            out=ReactLoopOut(finish_override=finish_override),
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    # The loop ended ON PAUSED — the single signal the pipeline maps to a paused
    # message_end + a parked persist tail. Prose folded into the card; bubble may
    # be empty (方案 2：不再引擎注入 wait-confirm 文案).
    assert finish_override == [FinishReason.PAUSED]
    assert content == ""

    # The suspended call is pending: the live transcript ends at the assistant issuing
    # ask_user, with NO tool result message (the bridge was never touched).
    assert [m.role for m in messages] == ["system", "user", "assistant"]
    assert messages[-1].tool_calls[0].function.name == "ask_user"
    assert all(m.role != "tool" for m in messages)

    # No §8.3 tool_call fact for the suspended call (recording one would inject a phantom
    # result into the resumed window).
    assert all(f["kind"] != FactKind.TOOL_CALL for f in log.entries())

    # THE GOLDEN: the window folded from the PERSISTED journal == the snapshotted transcript
    # == the live transcript — so a cold resume rebuilds the exact pre-pause window.
    persisted = captured["journal_entries"]  # type: ignore[assignment]
    assert window_from_journal(persisted) == captured["transcript"]
    assert window_from_journal(persisted) == messages

    # DISPLAY whole: the richer execution stream still surfaces the checkpoint card.
    runs = runs_from_entries(persisted)
    assert runs is not None
    assert any(e["type"] == "checkpoint_required" for e in runs["events"])
    cp = next(e for e in runs["events"] if e["type"] == "checkpoint_required")
    assert cp["payload"]["intent"] == "decision"
    assert "context" not in cp["payload"]


# --- the persist tail: PAUSED writes a snapshot assistant row -----------------------


async def test_persist_tail_writes_pause_snapshot(monkeypatch):
    # A PAUSED result writes a best-effort assistant snapshot (paused: True) so a refresh
    # replays CEO text / journal projection. Journal / cost / metrics are NOT written here.
    from types import SimpleNamespace

    from agentcore.conversation import turn_persistence
    from agentcore.conversation.store import cloud as cloud_mod

    upserted: dict = {}

    class FakeRepo:
        def __init__(self, _session):
            pass

        async def upsert_assistant(self, **kwargs):
            upserted.update(kwargs)

    class FakeSessionCM:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *_a):
            return False

    def _bomb_journal(*_args, **_kwargs):
        raise AssertionError("paused turn must not re-write journal")

    monkeypatch.setattr(cloud_mod, "MessageRepository", FakeRepo)
    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: FakeSessionCM())
    monkeypatch.setattr(cloud_mod, "persist_turn_journal", _bomb_journal)

    await turn_persistence.persist_turn_result(
        result={
            "message_id": "m1",
            "finish_reason": FinishReason.PAUSED,
            "content": "帮你分析一下选项",
            "reasoning_content": "先想清楚再提问",
        },
        conversation_id="c1",
        user_id="u1",
        folder_id=None,
        backend=object(),  # type: ignore[arg-type] - never touched on the parked path
        sink=EventSink(),
        user_message="A 还是 B?",
        llm_credentials=None,
        trace_id="t",
        turn_id="tn",
        duration_ms=1,
    )

    assert upserted["message_id"] == "m1"
    assert upserted["content"] == "帮你分析一下选项"
    assert upserted["reasoning_content"] == "先想清楚再提问"
    assert upserted["metadata"]["paused"] is True
    assert upserted["metadata"]["status"] == turn_persistence.MESSAGE_STATUS_RUNNING
    assert upserted["trace_id"] == "t"


async def test_loop_absorbs_content_into_blocking_ask_user():
    """Same-round prose + blocking ask_user: content folds into the card, not the bubble."""
    system_prompt = "你是 CEO。"
    user_message = "写一份调研报告"
    preamble = "帮你梳理一下起步方案："
    captured: dict[str, object] = {}

    async def saver(frame) -> None:  # noqa: ANN001
        captured["transcript"] = list(frame.transcript)

    async def deleter(_message_id: str) -> None:
        return None

    sink = EventSink()
    ask_tool = AskUserTool(
        sink=sink,
        conversation_id="c1",
        timeout_seconds=1.0,
        captain_run_id="cap",
        base_system_prompt=system_prompt,
        user_message=user_message,
        message_id="m1",
        suspension_saver=saver,
        suspension_deleter=deleter,
    )
    reg = ToolRegistry()
    reg.register(ask_tool)

    provider = _ScriptedProvider(
        [
            [
                LLMChunk(delta_content=preamble),
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="call_ask",
                            function_name="ask_user",
                            arguments_delta=(
                                '{"message": "", '
                                '"assumptions": [{"label": "篇幅", "value": "约3k字"}]}'
                            ),
                        )
                    ]
                ),
            ]
        ]
    )

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_message),
    ]
    profile = make_profile_params(max_rounds=5)
    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(
            system_prompt=system_prompt, user_message=user_message, model_profile="m"
        ).to_fact()
    )
    finish_override: list[FinishReason] = []
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(messages)
    try:
        content, _reasoning, _usage, _rounds = await react_loop(
            messages=messages,
            llm=provider,
            tools=reg,
            sink=sink,
            tool_context=_ctx(),
            profile=profile,
            turn_model="m",
            out=ReactLoopOut(finish_override=finish_override),
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert finish_override == [FinishReason.PAUSED]
    # Prose folds into the card; bubble rolls back (no engine wait-confirm fill).
    assert content == ""
    assert messages[-1].content is None
    args = json.loads(messages[-1].tool_calls[0].function.arguments)
    assert args["message"] == preamble
    events = _drain(sink)
    assert any(e.type is EventType.CONTENT_RESET for e in events)
    llm_facts = [f for f in log.entries() if f["kind"] == FactKind.LLM_CALL.value]
    assert llm_facts[-1]["payload"]["content"] == ""


# --- unproductive early-stop then force-finalize ask_user -----------------------


async def test_unproductive_then_finalize_ask_user_stamps_paused_last():
    """Regression: unproductive early-stop must not win over a later ask_user pause.

    Real accident (trace 807ee7b7…): consult/success inventory → 3× debate fail →
    ``UNPRODUCTIVE`` Finalize → force-finalize ``ask_user`` SUSPEND. The sink correctly
    appended ``[UNPRODUCTIVE, PAUSED]``, but captain used ``finish_override[0]`` and the
    client rendered the empty-failure banner instead of「需要你拍板」.
    """
    sink = EventSink()
    frames: list = []

    async def saver(frame) -> None:  # noqa: ANN001 - TurnSuspension
        frames.append(frame)

    async def deleter(_message_id: str) -> None:
        return None

    flaky = _FailOrOkTool("flaky", succeed_first=1)
    ask = _ask_tool(saver, deleter, sink)
    reg = ToolRegistry()
    reg.register(flaky)
    reg.register(ask)

    # Round 0 success (salvage inventory) → rounds 1–3 all-fail → unproductive →
    # force-finalize LLM round returns ask_user.
    provider = _ScriptedProvider(
        [
            [_tool_chunk("flaky", '{"q": "ok"}', call_id="c0")],
            [_tool_chunk("flaky", '{"q": "a"}', call_id="c1")],
            [_tool_chunk("flaky", '{"q": "b"}', call_id="c2")],
            [_tool_chunk("flaky", '{"q": "c"}', call_id="c3")],
            [
                LLMChunk(delta_content="先说清楚：这场辩论没能开起来。"),
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="call_ask",
                            function_name="ask_user",
                            arguments_delta=(
                                '{"message": "DeepSeek 不可用，怎么处理？", '
                                '"assumptions": [{"label": "换模型", "value": "auto"}]}'
                            ),
                        )
                    ]
                ),
            ],
        ]
    )

    messages = [
        LLMMessage(role="system", content="你是 CEO。"),
        LLMMessage(role="user", content="启动辩论"),
    ]
    profile = make_profile_params(max_rounds=20)
    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(
            system_prompt="你是 CEO。", user_message="启动辩论", model_profile="m"
        ).to_fact()
    )
    finish_override: list[FinishReason] = []
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(messages)
    try:
        _content, _reasoning, _usage, _rounds = await react_loop(
            messages=messages,
            llm=provider,
            tools=reg,
            sink=sink,
            tool_context=_ctx(),
            profile=profile,
            turn_model="m",
            out=ReactLoopOut(finish_override=finish_override),
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert finish_override == [FinishReason.UNPRODUCTIVE, FinishReason.PAUSED]
    assert resolve_finish_override(finish_override) is FinishReason.PAUSED
    assert len(frames) == 1
    assert any(e.type is EventType.CHECKPOINT_REQUIRED for e in _drain(sink))


def test_resolve_finish_override_latest_wins():
    assert resolve_finish_override([]) is None
    assert resolve_finish_override([FinishReason.UNPRODUCTIVE]) is FinishReason.UNPRODUCTIVE
    assert (
        resolve_finish_override([FinishReason.UNPRODUCTIVE, FinishReason.PAUSED])
        is FinishReason.PAUSED
    )
