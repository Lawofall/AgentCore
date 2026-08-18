"""P3 precise resume: journal cursor replay + SSE id wiring."""

from __future__ import annotations

import asyncio

from agentcore.api import sse
from agentcore.core.log_context import log_context
from agentcore.runtime.events import (
    EventSink,
    content_delta,
    content_reset,
    message_start,
    tool_use_start,
)
from agentcore.runtime.events.attach_replay import (
    _incremental_verdict,
    _mark_block_replacements,
    _slice_after_cursor,
    _turn_end_close_event,
    build_cursor_replay,
    journal_rows_to_sse,
    mark_full_replay_segment,
    replay_open_event,
    synthesize_segment_deltas,
)
from agentcore.runtime.events.stream_checkpointer import (
    CHANNEL_CAPTAIN_CONTENT,
    CHANNEL_CAPTAIN_REASONING,
    run_output_channel,
)
from agentcore.runtime.events.types import EventType
from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer


def test_journal_rows_to_sse_only_after_cursor_shape():
    """Given rows (already filtered seq > last), emit durable SSE with seq; skip exec."""
    rows = [
        {
            "seq": 2,
            "kind": "tool_use_start",
            "payload": {"tool_call_id": "t1", "tool_name": "web_search", "arguments": {}},
            "ts": "2026-01-01T00:00:00Z",
        },
        {
            "seq": 3,
            "kind": "llm_call",  # EXECUTION_ONLY — skipped
            "payload": {"run_id": "r1"},
            "ts": "2026-01-01T00:00:01Z",
        },
        {
            "seq": 4,
            "kind": "tool_use_end",
            "payload": {
                "tool_call_id": "t1",
                "tool_name": "web_search",
                "status": "success",
                "result": "ok",
            },
            "ts": "2026-01-01T00:00:02Z",
        },
    ]
    events = journal_rows_to_sse(rows)
    assert [e.type for e in events] == [EventType.TOOL_USE_START, EventType.TOOL_USE_END]
    assert [e.seq for e in events] == [2, 4]
    assert events[0].payload["tool_call_id"] == "t1"


def test_journal_rows_splices_message_final_before_terminal():
    rows = [
        {
            "seq": 1,
            "kind": "run_started",
            "payload": {"run_id": "w1", "agent_id": "ag1", "kind": "agent"},
            "ts": "t0",
        },
        {
            "seq": 2,
            "kind": "message_final",
            "payload": {"run_id": "w1", "content": "DONE", "reasoning": "think"},
            "ts": "t1",
        },
        {
            "seq": 3,
            "kind": "run_completed",
            "payload": {"run_id": "w1", "agent_id": "ag1"},
            "ts": "t2",
        },
    ]
    events = journal_rows_to_sse(rows)
    types = [e.type for e in events]
    assert types == [
        EventType.RUN_STARTED,
        EventType.RUN_REASONING_DELTA,
        EventType.RUN_OUTPUT_DELTA,
        EventType.RUN_COMPLETED,
    ]
    assert events[1].seq is None and events[1].payload["delta"] == "think"
    assert events[2].seq is None and events[2].payload["delta"] == "DONE"
    assert events[3].seq == 3


def test_synthesize_segment_deltas_captain_and_worker():
    events = synthesize_segment_deltas(
        by_channel={
            CHANNEL_CAPTAIN_REASONING: "r",
            CHANNEL_CAPTAIN_CONTENT: "hello",
            run_output_channel("w1"): "partial",
        },
        agent_run_ids={"w1": "ag1"},
        covered_run_ids=set(),
    )
    assert [e.type for e in events] == [
        EventType.REASONING_DELTA,
        EventType.CONTENT_DELTA,
        EventType.RUN_OUTPUT_DELTA,
    ]
    assert all(e.seq is None for e in events)
    assert events[1].payload["delta"] == "hello"
    assert events[2].payload["run_id"] == "w1"


def test_synthesize_skips_covered_runs():
    events = synthesize_segment_deltas(
        by_channel={run_output_channel("w1"): "x"},
        agent_run_ids={"w1": "ag1"},
        covered_run_ids={"w1"},
    )
    assert events == []


async def test_durable_emit_stamps_sse_id_from_barrier(monkeypatch):
    """append-on-emit barrier resolves with seq → ``id:`` on the live frame."""
    allocated = {"n": 0}

    class Store:
        async def append_journal(self, **kwargs) -> int | None:
            allocated["n"] += 1
            return allocated["n"]

    store = Store()
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store", lambda: store
    )

    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id="t1")
    token = current_journal_writer.set(writer)
    sink = EventSink(message_id="m1")
    try:
        sink.emit(tool_use_start("c1", "web_search", {}))
        await writer.flush()
        sink.close()
        frames = [frame async for frame in sse._event_generator(sink, None)]
    finally:
        current_journal_writer.reset(token)

    durable = [f for f in frames if "tool_use_start" in f]
    assert len(durable) == 1
    # 回合身份就在 id 里：客户端原样存、原样回传，服务端下次才分得清是不是本回合的游标。
    assert "\nid: m1:1\n" in durable[0]


async def test_ephemeral_delta_has_no_id_line():
    sink = EventSink()
    sink.emit(content_delta("hi"))
    sink.close()
    frames = [frame async for frame in sse._event_generator(sink, None)]
    assert any("content_delta" in f for f in frames)
    assert all("\nid: " not in f for f in frames if "content_delta" in f)


def test_parse_last_event_id_shapes():
    """Header 原样回传：有无回合身份就是版本协商，不另按客户端版本号分叉。"""
    assert sse.parse_last_event_id(None) is None
    assert sse.parse_last_event_id("") is None
    assert sse.parse_last_event_id("  ") is None
    assert sse.parse_last_event_id("m1:4") == sse.ReplayCursor(seq=4, turn_id="m1")
    assert sse.parse_last_event_id("  m1:4  ") == sse.ReplayCursor(seq=4, turn_id="m1")
    assert sse.parse_last_event_id("4") == sse.ReplayCursor(seq=4, turn_id=None)
    assert sse.parse_last_event_id("0") == sse.ReplayCursor(seq=0, turn_id=None)
    assert sse.parse_last_event_id("m1:0") == sse.ReplayCursor(seq=0, turn_id="m1")
    assert sse.parse_last_event_id("turn:with:colons:5") == sse.ReplayCursor(
        seq=5, turn_id="turn:with:colons"
    )
    # 读不出 seq 仍是「有游标」（走 journal 全量），不是缺 header 的 sink 快照路。
    assert sse.parse_last_event_id("garbage") == sse.ReplayCursor(seq=0)
    assert sse.parse_last_event_id("m1:abc") == sse.ReplayCursor(seq=0)


async def test_attach_cursor_path_replays_full_journal_then_segments(monkeypatch):
    """Last-Event-ID path: full-turn journal (incl. process_*) ; no _history content.

    Structured turns do not stitch 旁白 from flat segments. Header value is
    observational — load_after is called with -1 (turn start).
    """
    sink = EventSink()
    sink._message_id = "m1"
    # History would have this on the no-cursor path — cursor replay must NOT.
    sink.emit(content_delta("FROM_HISTORY"))

    rows = [
        {
            "seq": 1,
            "kind": "process_content",
            "payload": {"kind": "content", "text": "FROM_PROCESS"},
            "ts": "t0",
        },
        {
            "seq": 2,
            "kind": "tool_use_start",
            "payload": {"tool_call_id": "t1", "tool_name": "web_search", "arguments": {}},
            "ts": "t0",
        },
        {
            "seq": 5,
            "kind": "tool_use_end",
            "payload": {
                "tool_call_id": "t1",
                "tool_name": "web_search",
                "status": "success",
                "result": "ok",
            },
            "ts": "t1",
        },
    ]
    loaded_after: list[int] = []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def load_after(self, turn_id: str, after_seq: int):
            assert turn_id == "m1"
            loaded_after.append(after_seq)
            # Full-turn load ignores the client's cursor (observational only).
            assert after_seq == -1
            return rows

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _Sess())
    monkeypatch.setattr(
        "agentcore.db.repositories.runs.TurnJournalRepository", Repo
    )

    # Flat segment must NOT become a content_delta on structured turns.
    sink.stream_memory_snapshot = (  # type: ignore[method-assign]
        lambda: {CHANNEL_CAPTAIN_CONTENT: "FROM_SEGMENT"}
    )

    gen = sse._attach_generator(sink, cursor=sse.ReplayCursor(seq=4, turn_id="m1"))
    frames: list[str] = []
    try:
        for _ in range(4):
            frames.append(await asyncio.wait_for(gen.__anext__(), timeout=2.0))
    finally:
        await gen.aclose()

    joined = "".join(frames)
    assert loaded_after == [-1]
    # The stamp opens the segment (before any durable fact) and carries no ``id:``.
    assert frames[0].startswith("event: message_start")
    assert '"message_id": "m1"' in frames[0]
    assert '"full_replay": true' in frames[0]
    assert "\nid: " not in frames[0]
    assert "FROM_HISTORY" not in joined
    assert "FROM_SEGMENT" not in joined
    assert "FROM_PROCESS" in joined
    # Pre-cursor structure must be present (full replay, not > cursor tail).
    assert "tool_use_start" in joined
    assert "tool_use_end" in joined
    assert "\nid: m1:1\n" in joined
    assert "\nid: m1:2\n" in joined
    assert "\nid: m1:5\n" in joined


# --- 收口事实回放：finished detached turn closes with a synthetic message_end ---
# message_end is DERIVED (never journaled) + a detached turn emits it while detached, so
# a client attaching inside the post-completion persist window would otherwise get no
# close frame and finalize only via the reconnect-banner error salvage. build_cursor_replay
# replays a synthetic message_end whenever the journal carries turn_end (turn finished).


def test_turn_end_close_event_finished_turn_emits_message_end():
    rows = [
        {"seq": 1, "kind": "process_content", "payload": {"kind": "content", "text": "CEO 总结"}},
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    ev = _turn_end_close_event(rows)
    assert ev is not None
    assert ev.type == EventType.MESSAGE_END
    assert ev.payload == {
        "finish_reason": "end_turn",
        "team_batch": {"kind": "no_batch"},
    }


def test_turn_end_close_event_copies_outcome():
    ev = _turn_end_close_event(
        [{"kind": "turn_end", "payload": {"finish_reason": "degraded", "outcome": "partial"}}]
    )
    assert ev is not None
    assert ev.payload == {
        "finish_reason": "degraded",
        "outcome": "partial",
        "team_batch": {"kind": "no_batch"},
    }


def test_turn_end_close_event_paused_preserves_reason():
    """paused must survive so the client routes to the durable resume card, not complete."""
    ev = _turn_end_close_event([{"kind": "turn_end", "payload": {"finish_reason": "paused"}}])
    assert ev is not None
    assert ev.payload["finish_reason"] == "paused"


def test_turn_end_close_event_running_turn_returns_none():
    """No turn_end yet (turn still running) → None so the live tail delivers the real end."""
    rows = [
        {"seq": 1, "kind": "process_content", "payload": {"kind": "content", "text": "x"}},
    ]
    assert _turn_end_close_event(rows) is None


def test_turn_end_close_event_unknown_reason_falls_back_to_end_turn():
    ev = _turn_end_close_event([{"kind": "turn_end", "payload": {"finish_reason": "??"}}])
    assert ev is not None
    assert ev.payload["finish_reason"] == "end_turn"


def _patch_journal_repo(monkeypatch, rows: list[dict]) -> None:
    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def load_after(self, turn_id: str, after_seq: int):
            return rows

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class Store:
        async def list_stream_segments(self, *, turn_id: str):
            return []

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _Sess())
    monkeypatch.setattr("agentcore.db.repositories.runs.TurnJournalRepository", Repo)
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store", lambda: Store()
    )


async def test_build_cursor_replay_appends_message_end_for_finished_turn(monkeypatch):
    """Finished detached turn: the CEO summary content_delta is followed by a message_end
    close so the attaching client finalizes the bubble instead of the reconnect salvage."""
    rows = [
        {"seq": 1, "kind": "process_content", "payload": {"kind": "content", "text": "CEO 总结"}, "ts": "t0"},
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    _patch_journal_repo(monkeypatch, rows)

    events = await build_cursor_replay(
        turn_id="m1", conversation_id="c1", after_seq=-1, memory_channels={}, memory_agent_ids={}
    )

    assert events[-1].type == EventType.MESSAGE_END
    assert events[-1].payload["finish_reason"] == "end_turn"
    # The summary is still replayed as content before the close frame.
    summary = [
        e for e in events
        if e.type == EventType.CONTENT_DELTA and "CEO 总结" in (e.payload.get("delta") or "")
    ]
    assert len(summary) == 1


async def test_build_cursor_replay_no_close_for_running_turn(monkeypatch):
    """Still-running turn (no turn_end): no synthetic close — the live tail owns the end."""
    rows = [
        {"seq": 1, "kind": "process_content", "payload": {"kind": "content", "text": "进行中"}, "ts": "t0"},
    ]
    _patch_journal_repo(monkeypatch, rows)

    events = await build_cursor_replay(
        turn_id="m1", conversation_id="c1", after_seq=-1, memory_channels={}, memory_agent_ids={}
    )

    assert all(e.type != EventType.MESSAGE_END for e in events)


# --- 开场事实回放：耐久卡必须落在盖过章的气泡上 -----------------------------------
# message_start is EPHEMERAL (never journaled) yet it is the only frame carrying the
# server assistant message_id — the key a resume submit uses. Desktop always sends
# Last-Event-ID, so without a synthetic stamp at the head of this segment the replayed
# ask_user / plan_review / team_preview card binds to a client-only (or previous-turn)
# id and the「继续」card cannot be painted / 404s on submit.


def test_replay_open_event_carries_the_turn_id_without_seq():
    # The ambient trace here is the ATTACH request's, not the turn's — stamping it would
    # point the bubble's log link at this GET, so the stamp must stay trace-free.
    with log_context(trace_id="attach-request-trace"):
        ev = replay_open_event(turn_id="m1", conversation_id="c1")
    assert ev.type == EventType.MESSAGE_START
    # full_replay is the segment's own「先重置本回合本地态再折」instruction — without it
    # the client had to guess from the id and a wrong guess folded the body twice.
    assert ev.payload == {"message_id": "m1", "conversation_id": "c1", "full_replay": True}
    # Synthetic frame — no journal seq, so it never rewrites the client's cursor.
    assert ev.seq is None


def test_live_message_start_is_not_flagged_as_replay():
    """Live 首帧不带 full_replay：只有回放段才下达重置指令。"""
    ev = message_start("m1", conversation_id="c1", trace_id="")
    assert "full_replay" not in ev.payload


async def test_build_cursor_replay_stamps_bubble_before_the_durable_card(monkeypatch):
    """Paused-at-plan_review turn: the stamp leads, so the card binds to this turn's id."""
    rows = [
        {
            "seq": 1,
            "kind": "process_content",
            "payload": {"kind": "content", "text": "阶段成果如下。"},
            "ts": "t0",
        },
        {
            "seq": 2,
            "kind": "plan_review_required",
            "payload": {"checkpoint_id": "cp1", "conversation_id": "c1", "steps": [], "pending": []},
            "ts": "t1",
        },
        {"kind": "turn_end", "payload": {"finish_reason": "paused"}, "ts": None},
    ]
    _patch_journal_repo(monkeypatch, rows)

    events = await build_cursor_replay(
        turn_id="m1", conversation_id="c1", after_seq=-1, memory_channels={}, memory_agent_ids={}
    )

    assert events[0].type == EventType.MESSAGE_START
    assert events[0].payload == {
        "message_id": "m1",
        "conversation_id": "c1",
        "full_replay": True,
    }
    types = [e.type for e in events]
    assert types.index(EventType.MESSAGE_START) < types.index(EventType.PLAN_REVIEW_REQUIRED)
    # paused survives to the close frame → the client routes to the durable resume card.
    assert events[-1].type == EventType.MESSAGE_END
    assert events[-1].payload["finish_reason"] == "paused"


async def test_build_cursor_replay_stamps_even_with_empty_journal(monkeypatch):
    """Bare attach (nothing journaled yet): the stamp alone opens + keys the bubble."""
    _patch_journal_repo(monkeypatch, [])

    events = await build_cursor_replay(
        turn_id="m1", conversation_id="c1", after_seq=-1, memory_channels={}, memory_agent_ids={}
    )

    assert [e.type for e in events] == [EventType.MESSAGE_START]


async def test_build_cursor_replay_stamp_is_identical_on_reattach(monkeypatch):
    """Re-attach replays the SAME message_id — folds treat it as 同回合重开, not a new bubble.

    Only the ``full_replay`` order differs by段 kind (full vs incremental); the id the
    durable cards bind to is the same one every time.
    """
    rows = [
        {"seq": 1, "kind": "process_content", "payload": {"kind": "content", "text": "进行中"}, "ts": "t0"},
    ]
    _patch_journal_repo(monkeypatch, rows)

    first, second = [
        await build_cursor_replay(
            turn_id="m1",
            conversation_id="c1",
            after_seq=cursor,
            cursor_turn_id=cursor_turn,
            memory_channels={},
            memory_agent_ids={},
        )
        for cursor, cursor_turn in ((-1, None), (1, "m1"))
    ]

    assert first[0].payload["message_id"] == second[0].payload["message_id"] == "m1"
    assert first[0].payload["conversation_id"] == second[0].payload["conversation_id"] == "c1"
    assert first[0].payload["full_replay"] is True
    assert "full_replay" not in second[0].payload


# --- 清空指令由段首下达：两条 attach 回放路径必须同令 -------------------------------
# 服务端从不声明「这段是全量重放」时，客户端只能拿段首 message_start 的 id 跟屏上气泡比对、
# 自己猜要不要清——猜错就把正文折两遍（已出过线上 bug）。现在 full_replay 是显式指令，
# 带 Last-Event-ID 的 journal 游标路径与不带 header 的 sink 内存历史路径都必须带上它。


def test_mark_full_replay_flags_the_history_head_without_touching_the_live_frame():
    """History entries share their payload dict with the live event — flag a COPY."""
    live = message_start("m1", conversation_id="c1", trace_id="tr1")
    segment = mark_full_replay_segment(
        [live, content_delta("已答一半")], turn_id="m1", conversation_id="c1"
    )

    assert segment[0].payload == {
        "message_id": "m1",
        "conversation_id": "c1",
        "trace_id": "tr1",
        "full_replay": True,
    }
    # 正在流的其它端不能被回溯打上「这是重放」——否则它们会莫名清空重折。
    assert "full_replay" not in live.payload
    assert segment[1] is not None and segment[1].payload["delta"] == "已答一半"


def test_mark_full_replay_synthesizes_a_head_when_history_has_none():
    """Attached before the turn opened its bubble: the段 still carries the reset order."""
    segment = mark_full_replay_segment(
        [content_delta("旁白")], turn_id="m1", conversation_id="c1"
    )

    assert segment[0].type == EventType.MESSAGE_START
    assert segment[0].payload == {
        "message_id": "m1",
        "conversation_id": "c1",
        "full_replay": True,
    }
    assert [e.type for e in segment[1:]] == [EventType.CONTENT_DELTA]


def test_mark_full_replay_flags_only_the_segment_head():
    """A second same-id stamp keeps meaning 同回合重开 — only the head orders a reset."""
    segment = mark_full_replay_segment(
        [
            message_start("m1", conversation_id="c1", trace_id=""),
            content_delta("一段"),
            message_start("m1", conversation_id="c1", trace_id=""),
        ],
        turn_id="m1",
        conversation_id="c1",
    )

    assert segment[0].payload.get("full_replay") is True
    assert "full_replay" not in segment[2].payload


def test_mark_full_replay_without_a_turn_id_stamps_nothing():
    """No bound message_id = no bubble to reset; never invent one out of thin air."""
    segment = mark_full_replay_segment([content_delta("x")], turn_id=None, conversation_id="c1")

    assert [e.type for e in segment] == [EventType.CONTENT_DELTA]


def test_an_empty_segment_orders_no_reset():
    """空段不下清空指令 —— 一句 reset 后面什么都不跟，等于把客户端的正文擦了不还。

    空 ``history_snapshot()`` ≠ 客户端手里为空。resume settled join 正是这样：用户第二次
    点「继续」，续跑的 sink 刚建、历史空着，而客户端握着暂停前的整轮正文；这条路又只走
    sink 历史、不查 journal，清掉就再也补不回来。没有帧要重放，就不存在「本段是全量重放」
    这回事——真 ``message_start`` 随后会从 live 尾巴上不带标记地到达（同回合重开）。
    """
    assert mark_full_replay_segment([], turn_id="m1", conversation_id="c1") == []
    # 有内容却缺段首仍要补（上一期的意图不变）：那是「气泡还没开就接上了」。
    assert (
        mark_full_replay_segment([content_delta("旁白")], turn_id="m1", conversation_id="c1")[
            0
        ].type
        == EventType.MESSAGE_START
    )


async def test_attach_with_an_empty_history_goes_straight_to_the_boundary():
    """端到端：无游标 + 空历史 → 第一帧就是追平边界，不是凭空的 full_replay 段首。"""
    sink = EventSink()
    sink._message_id = "m1"
    sink._conversation_id = "c1"

    gen = sse._attach_generator(sink, cursor=None)
    try:
        first = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    finally:
        await gen.aclose()

    assert first == ": attach-caught-up\n\n"


async def test_attach_without_cursor_leads_with_the_same_reset_instruction():
    """No-header path (sink history): client-observably the same段首 as the cursor path."""
    sink = EventSink()
    sink._message_id = "m1"
    sink._conversation_id = "c1"
    sink.emit(message_start("m1", conversation_id="c1", trace_id="tr1"))
    sink.emit(content_delta("已答一半"))

    gen = sse._attach_generator(sink, cursor=None)
    try:
        head = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    finally:
        await gen.aclose()

    assert head.startswith("event: message_start")
    assert '"full_replay": true' in head
    # 段首照旧盖章：耐久卡仍绑本回合 message_id，trace 链接也不丢。
    assert '"message_id": "m1"' in head
    assert '"trace_id": "tr1"' in head


# --- 真增量：判定照旧看全表，只把「发什么」收窄到游标之后 ---------------------------
# 真实多 Agent 回合 journal 已观测到 ≥605 行、派单行 15KB，手机回前台每次重连都在重传
# 一整场。判定仍必须扫全表（是否结构化 / 已覆盖 run 集 / agent_id 回填 / message_final
# 拼 worker 全文四处，只看增量会翻面），过滤发生在产出事件之后。


def _structured_multi_agent_rows() -> list[dict]:
    """一条典型结构化回合：四处全表判定的依据全在游标**之前**。"""
    return [
        {
            "seq": 0,
            "kind": "run_started",
            "payload": {"run_id": "w1", "agent_id": "ag1", "kind": "agent"},
            "ts": "t0",
        },
        # 覆盖 w1 的 run_process 文本（游标前）→ 合成段不得再补 w1 的扁平通道全文。
        {
            "seq": 1,
            "kind": "run_process_content",
            "payload": {"kind": "content", "text": "worker 旁白", "run_id": "w1"},
            "ts": "t0",
        },
        # 有 process_* → 结构化回合（游标前）→ 合成段不得拼 CEO 扁平旁白。
        {
            "seq": 2,
            "kind": "process_content",
            "payload": {"kind": "content", "text": "CEO 旁白"},
            "ts": "t0",
        },
        {
            "seq": 3,
            "kind": "tool_use_start",
            "payload": {"tool_call_id": "t1", "tool_name": "web_search", "arguments": {}},
            "ts": "t1",
        },
        {
            "seq": 4,
            "kind": "tool_use_end",
            "payload": {
                "tool_call_id": "t1",
                "tool_name": "web_search",
                "status": "success",
                "result": "ok",
            },
            "ts": "t1",
        },
        # payload 没有 agent_id：只能由游标前那条 run_started 回填。
        {
            "seq": 5,
            "kind": "run_process_content",
            "payload": {"kind": "content", "text": "worker 续写", "run_id": "w1"},
            "ts": "t2",
        },
        {
            "seq": 6,
            "kind": "message_final",
            "payload": {"run_id": "w1", "content": "worker 全文", "reasoning": "worker 思考"},
            "ts": "t2",
        },
        {"seq": 7, "kind": "run_completed", "payload": {"run_id": "w1"}, "ts": "t3"},
    ]


async def test_increment_ships_only_post_cursor_facts_but_judges_on_the_whole_turn(
    monkeypatch,
):
    """四处全表判定在过滤之后仍然成立 —— 这条是最容易回归的一条。

    游标停在 seq 3（``tool_use_start``）。段里只该有 seq>3 的事实，但：结构化判定、
    已覆盖 run 集、``agent_id`` 回填、``message_final`` 拼 worker 全文，依据全在游标之前，
    若改成只读增量就会分别退化成「拼扁平旁白 / 重复 worker 正文 / agent_id 空 / 正文整段丢」。
    """
    _patch_journal_repo(monkeypatch, _structured_multi_agent_rows())

    events = await build_cursor_replay(
        turn_id="m1",
        conversation_id="c1",
        after_seq=3,
        cursor_turn_id="m1",
        memory_channels={
            CHANNEL_CAPTAIN_CONTENT: "FROM_SEGMENT",
            run_output_channel("w1"): "FROM_SEGMENT_W1",
        },
        memory_agent_ids={},
    )

    assert [e.type for e in events] == [
        EventType.MESSAGE_START,
        EventType.TOOL_USE_END,
        EventType.RUN_OUTPUT_DELTA,  # seq 5 的 run_process 步
        EventType.RUN_REASONING_DELTA,  # message_final 拼的 worker 思考
        EventType.RUN_OUTPUT_DELTA,  # message_final 拼的 worker 全文
        EventType.RUN_COMPLETED,
    ]
    # 增量段：段首不下清空指令。
    assert "full_replay" not in events[0].payload
    # 游标前的结构不重发（工具起始 / CEO 旁白 / worker 前半段）。
    bodies = [e.payload.get("delta") for e in events]
    assert "CEO 旁白" not in bodies
    assert "worker 旁白" not in bodies
    assert not any(e.type == EventType.TOOL_USE_START for e in events)
    # ① 结构化判定：扁平 captain 通道仍被跳过（判定源 process_content 在游标前）。
    # ② 已覆盖 run 集：w1 的扁平通道仍被跳过（判定源 run_process_content 在游标前）。
    assert not any((e.payload.get("delta") or "").startswith("FROM_SEGMENT") for e in events)
    # ③ agent_id 回填：判定源 run_started 在游标前，仍要填上。
    assert events[2].payload == {
        "run_id": "w1",
        "agent_id": "ag1",
        "delta": "worker 续写",
        # ④ 跨游标那步 process 行是整步全文 → 整块替换，不往客户端半截后面追加。
        "replace": True,
    }
    # ⑤ message_final 拼出的 worker 全文照旧在终态帧之前落地。
    assert events[3].payload["delta"] == "worker 思考"
    assert events[4].payload["delta"] == "worker 全文"


async def test_increment_marks_only_the_first_frame_of_each_channel(monkeypatch):
    """一个通道只有段内第一帧是「整块换」；其后的文本步是真·新块，照常追加。"""
    _patch_journal_repo(monkeypatch, _structured_multi_agent_rows())

    events = await build_cursor_replay(
        turn_id="m1",
        conversation_id="c1",
        after_seq=3,
        cursor_turn_id="m1",
        memory_channels={},
        memory_agent_ids={},
    )

    w1_content = [
        e
        for e in events
        if e.type == EventType.RUN_OUTPUT_DELTA and e.payload.get("run_id") == "w1"
    ]
    assert [e.payload.get("replace") for e in w1_content] == [True, None]
    # 另一个通道（思考）独立计数：它自己的第一帧照样是整块换。
    reasoning = [e for e in events if e.type == EventType.RUN_REASONING_DELTA]
    assert [e.payload.get("replace") for e in reasoning] == [True]


async def test_full_segment_journal_rows_carry_no_replace(monkeypatch):
    """全量段客户端刚被清空，每条 process 行都是新块——不带 replace（语义留给整块帧）。"""
    _patch_journal_repo(monkeypatch, _structured_multi_agent_rows())

    events = await build_cursor_replay(
        turn_id="m1", conversation_id="c1", after_seq=-1, memory_channels={}, memory_agent_ids={}
    )

    assert events[0].payload["full_replay"] is True
    assert all("replace" not in e.payload for e in events)


def test_synthesized_open_blocks_always_declare_themselves_whole():
    """stream_state 合成的都是「该通道到此为止的全文」，不是增量——恒带 replace。"""
    events = synthesize_segment_deltas(
        by_channel={
            CHANNEL_CAPTAIN_REASONING: "思考中",
            CHANNEL_CAPTAIN_CONTENT: "答一半",
            run_output_channel("w1"): "worker 半截",
        },
        agent_run_ids={"w1": "ag1"},
        covered_run_ids=set(),
    )

    assert [e.payload.get("replace") for e in events] == [True, True, True]


# --- 保守条件：说不准就整段重发（全量那条路已被上一期证明正确）---------------------


def test_cursor_zero_is_the_no_cursor_sentinel_not_a_position():
    """两端在没有游标时都发 ``Last-Event-ID: 0``，不能读成「我有 seq 0 之前的一切」。"""
    rows = [{"seq": 0, "kind": "tool_use_start", "payload": {}, "ts": "t0"}]
    assert (
        _incremental_verdict(rows, after_seq=0, turn_id="m1", cursor_turn_id="m1") == "no_cursor"
    )
    assert (
        _incremental_verdict(rows, after_seq=-1, turn_id="m1", cursor_turn_id="m1") == "no_cursor"
    )


def test_settled_turn_falls_back_to_full_replay():
    """有 turn_end = 回合已收口/挂起：整回合重编号过（或即将），旧游标不可信。"""
    rows = [
        {"seq": 1, "kind": "tool_use_start", "payload": {}, "ts": "t0"},
        {"seq": 2, "kind": "turn_end", "payload": {"finish_reason": "paused"}, "ts": None},
    ]
    assert (
        _incremental_verdict(rows, after_seq=1, turn_id="m1", cursor_turn_id="m1") == "turn_settled"
    )


def test_cursor_that_names_no_stamped_fact_falls_back_to_full_replay():
    """游标必须指向本回合真的盖过 ``id:`` 的那条事实。

    对不上的来源：重编号后的陈旧值、压根没上过线的执行事实。跨回合外来值由 turn_id 匹配另拦。
    """
    rows = [
        {"seq": 1, "kind": "llm_call", "payload": {"run_id": "r1"}, "ts": "t0"},
        {"seq": 2, "kind": "message_final", "payload": {"run_id": "w1"}, "ts": "t0"},
        {"seq": 3, "kind": "process_tool", "payload": {"kind": "tool", "id": "t1"}, "ts": "t0"},
        {
            "seq": 4,
            "kind": "tool_use_start",
            "payload": {"tool_call_id": "t1", "tool_name": "x", "arguments": {}},
            "ts": "t0",
        },
    ]

    kw = dict(turn_id="m1", cursor_turn_id="m1")
    assert _incremental_verdict(rows, after_seq=1, **kw) == "cursor_unknown"  # 执行事实不上线
    assert _incremental_verdict(rows, after_seq=2, **kw) == "cursor_unknown"  # 只做拼接源
    assert _incremental_verdict(rows, after_seq=3, **kw) == "cursor_unknown"  # 结构镜像不产帧
    assert _incremental_verdict(rows, after_seq=9, **kw) == "cursor_unknown"  # 越界 / 外来
    assert _incremental_verdict(rows, after_seq=4, **kw) is None  # 真盖过 id 的耐久事实


def test_process_text_rows_are_stampable_cursors():
    """回放段会给 process 文本步打 ``id:``，所以下一次重连的游标可能正停在这种行上。"""
    rows = [
        {"seq": 1, "kind": "process_content", "payload": {"kind": "content", "text": "x"}},
    ]
    assert (
        _incremental_verdict(rows, after_seq=1, turn_id="m1", cursor_turn_id="m1") is None
    )


def test_foreign_turn_cursor_cannot_increment():
    """seq 按回合从 0 计；会话级游标带着上一回合的号会撞上本回合同号行。"""
    rows = [{"seq": 3, "kind": "tool_use_start", "payload": {}, "ts": "t0"}]
    assert (
        _incremental_verdict(rows, after_seq=3, turn_id="m1", cursor_turn_id="m0")
        == "cursor_foreign_turn"
    )
    assert _incremental_verdict(rows, after_seq=3, turn_id="m1", cursor_turn_id="m1") is None


def test_unversioned_cursor_cannot_increment():
    """裸 seq（旧客户端回传升级前的 ``id:``）没有回合身份，与外来游标同等不可信。"""
    rows = [{"seq": 3, "kind": "tool_use_start", "payload": {}, "ts": "t0"}]
    assert (
        _incremental_verdict(rows, after_seq=3, turn_id="m1", cursor_turn_id=None)
        == "cursor_unversioned"
    )


async def test_conservative_fallback_replays_the_whole_turn(monkeypatch):
    """退回全量 = 回到上一期那条路：段首带 full_replay，游标前的结构一条不少。"""
    _patch_journal_repo(
        monkeypatch,
        _structured_multi_agent_rows()
        + [{"seq": 8, "kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None}],
    )

    events = await build_cursor_replay(
        turn_id="m1",
        conversation_id="c1",
        after_seq=3,
        cursor_turn_id="m1",
        memory_channels={},
        memory_agent_ids={},
    )

    assert events[0].payload["full_replay"] is True
    assert any(e.type == EventType.TOOL_USE_START for e in events)
    assert any((e.payload.get("delta") or "") == "CEO 旁白" for e in events)
    assert events[-1].type == EventType.MESSAGE_END


# --- 过滤本身：拼接帧跟着它引导的那条终态帧走 --------------------------------------


def test_slice_keeps_message_final_splices_with_their_terminal():
    """``message_final`` 拼出的全文帧没有 seq，位置就是它引导的终态帧的位置。"""
    events = journal_rows_to_sse(
        [
            {
                "seq": 1,
                "kind": "run_started",
                "payload": {"run_id": "w1", "agent_id": "ag1", "kind": "agent"},
                "ts": "t0",
            },
            {
                "seq": 2,
                "kind": "message_final",
                "payload": {"run_id": "w1", "content": "全文", "reasoning": ""},
                "ts": "t0",
            },
            {"seq": 3, "kind": "run_completed", "payload": {"run_id": "w1"}, "ts": "t0"},
        ]
    )

    # 终态帧在游标之后 → 拼接帧跟着一起发。
    kept = _slice_after_cursor(events, after_seq=1)
    assert [e.type for e in kept] == [EventType.RUN_OUTPUT_DELTA, EventType.RUN_COMPLETED]
    # 终态帧在游标之前 → 拼接帧也不重发（客户端早已收过 worker 全文）。
    assert _slice_after_cursor(events, after_seq=3) == []


def test_replace_marking_restarts_after_a_channel_reset():
    """``*_reset`` 把通道清空了，之后那帧是干净的新块，不该再声称「整块换」。"""
    marked = _mark_block_replacements(
        [
            content_delta("第一块"),
            content_reset("finish_guard"),
            content_delta("重写版"),
        ]
    )

    assert marked[0].payload.get("replace") is True
    assert "replace" not in marked[2].payload


async def test_attach_stream_ships_an_incremental_segment_end_to_end(monkeypatch):
    """端到端：带可信游标的 attach 收到不带 full_replay 的段 + 只有游标后的帧。"""
    sink = EventSink()
    sink._message_id = "m1"
    sink.emit(content_delta("FROM_HISTORY"))
    _patch_journal_repo(monkeypatch, _structured_multi_agent_rows())

    gen = sse._attach_generator(sink, cursor=sse.ReplayCursor(seq=3, turn_id="m1"))
    frames: list[str] = []
    try:
        for _ in range(3):
            frames.append(await asyncio.wait_for(gen.__anext__(), timeout=2.0))
    finally:
        await gen.aclose()

    joined = "".join(frames)
    assert frames[0].startswith("event: message_start")
    assert "full_replay" not in frames[0]
    assert '"message_id": "m1"' in frames[0]
    assert "FROM_HISTORY" not in joined
    assert "CEO 旁白" not in joined
    assert "tool_use_end" in joined
    assert "\nid: m1:4\n" in joined


async def _attach_journal_catchup(sink: EventSink, monkeypatch, cursor: sse.ReplayCursor) -> str:
    """取几帧 catch-up 后关掉，避免活尾巴上的 wait 把用例卡住。"""
    _patch_journal_repo(monkeypatch, _structured_multi_agent_rows())
    gen = sse._attach_generator(sink, cursor=cursor)
    frames: list[str] = []
    try:
        for _ in range(6):
            frames.append(await asyncio.wait_for(gen.__anext__(), timeout=2.0))
    finally:
        await gen.aclose()
    return "".join(frames)


async def test_cross_turn_cursor_replays_the_whole_journal(monkeypatch):
    """跨回合 ``<other>:<seq>`` 即便撞上本回合同号行，也走 journal 全量，不是 sink 快照。"""
    sink = EventSink()
    sink._message_id = "m1"
    sink.emit(content_delta("FROM_HISTORY"))

    cursor = sse.parse_last_event_id("m0:3")
    assert cursor is not None
    joined = await _attach_journal_catchup(sink, monkeypatch, cursor)

    assert '"full_replay": true' in joined
    assert "FROM_HISTORY" not in joined
    assert "CEO 旁白" in joined
    assert "tool_use_start" in joined


async def test_bare_seq_cursor_replays_the_whole_journal(monkeypatch):
    """裸数字（旧 ``id: 3``）一律 journal 全量，不是 ``cursor is None`` 的 sink 内存快照。"""
    sink = EventSink()
    sink._message_id = "m1"
    sink.emit(content_delta("FROM_HISTORY"))

    cursor = sse.parse_last_event_id("3")
    assert cursor is not None
    joined = await _attach_journal_catchup(sink, monkeypatch, cursor)

    assert '"full_replay": true' in joined
    assert "FROM_HISTORY" not in joined
    assert "CEO 旁白" in joined
    assert "tool_use_start" in joined
