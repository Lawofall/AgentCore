"""Settlement prewrite + journal writer dedupe / priority (提问确认交互统一 P1 · D8)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentcore.runtime.events import approval_resolved, checkpoint_resolved
from agentcore.runtime.events.interaction import interaction_orphaned, stage_card_resolved
from agentcore.runtime.journal.pending_interactions import settlement_dedupe_key
from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer
from agentcore.runtime.settlement import (
    align_cold_resume_resolved_to_winner,
    entry_from_sse,
    prewrite_cold_resume_settlement,
    prewrite_settlement,
    prewrite_settlement_direct,
    seed_settlement_dedupe_from_entries,
)
from agentcore.runtime.suspension import AskUserSuspension


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail_kinds: set[str] = set()
        self.lock = asyncio.Lock()

    async def append_journal(
        self,
        *,
        turn_id: str,
        seq: int | None,
        conversation_id: str,
        trace_id: str | None,
        entry: dict[str, Any],
        overflow: bool = False,
    ) -> int | None:
        del overflow
        async with self.lock:
            kind = str(entry.get("kind") or "")
            if kind in self.fail_kinds:
                raise RuntimeError(f"forced fail: {kind}")
            # Mirror CloudStore: live passes seq=None (DB allocates); record the
            # caller's seq arg for assertions, return a synthetic durable seq.
            allocated = len(self.rows) if seq is None else int(seq)
            self.rows.append(
                {
                    "turn_id": turn_id,
                    "seq": seq,
                    "conversation_id": conversation_id,
                    "entry": entry,
                }
            )
            return allocated


def _ask_frame() -> AskUserSuspension:
    return AskUserSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="ck1",
        tool_call_id="call_ask",
        base_system_prompt="sys",
        user_message="A 还是 B?",
        transcript=[],
        question="A 还是 B?",
        questions=[
            {
                "id": "q0",
                "prompt": "A 还是 B?",
                "kind": "choice",
                "options": ["A", "B"],
                "multiple": False,
                "default": "",
            }
        ],
    )


@pytest.mark.asyncio
async def test_settlement_prewrite_priority_and_dedupe(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore()
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store",
        lambda: store,
    )

    writer = TurnJournalWriter(turn_id="t1", conversation_id="c1", trace_id=None)
    token = current_journal_writer.set(writer)
    try:
        # Queue a normal fact first
        fut_normal = writer.schedule_append(
            {"kind": "run_started", "payload": {"run_id": "r"}, "ts": "t"}
        )
        event = approval_resolved(
            approval_id="a1", tool_call_id="a1", decision="approve"
        )
        await prewrite_settlement(event)
        # Awaiter re-emit should dedupe (no second journal row)
        fut_dup = writer.schedule_append(
            {
                "kind": "approval_resolved",
                "payload": {
                    "approval_id": "a1",
                    "tool_call_id": "a1",
                    "decision": "approve",
                },
                "ts": "t",
            }
        )
        await writer.flush()
        if fut_normal:
            await fut_normal
        if fut_dup:
            await fut_dup
    finally:
        current_journal_writer.reset(token)

    kinds = [r["entry"]["kind"] for r in store.rows]
    assert kinds.count("approval_resolved") == 1
    assert "run_started" in kinds


@pytest.mark.asyncio
async def test_settlement_prewrite_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore()
    store.fail_kinds.add("approval_resolved")
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store",
        lambda: store,
    )

    writer = TurnJournalWriter(turn_id="t1", conversation_id="c1", trace_id=None)
    token = current_journal_writer.set(writer)
    try:
        event = approval_resolved(
            approval_id="a1", tool_call_id="a1", decision="approve"
        )
        with pytest.raises(RuntimeError, match="forced fail"):
            await prewrite_settlement(event)
    finally:
        current_journal_writer.reset(token)


@pytest.mark.asyncio
async def test_concurrent_writers_use_db_seq_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two writers enqueue concurrently; both pass seq=None (DB allocates)."""
    store = _FakeStore()
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store",
        lambda: store,
    )

    w1 = TurnJournalWriter(turn_id="t1", conversation_id="c1", trace_id=None)
    w2 = TurnJournalWriter(turn_id="t1", conversation_id="c1", trace_id=None)

    async def burst(w: TurnJournalWriter, n: int) -> None:
        futs = []
        for i in range(n):
            futs.append(
                w.schedule_append(
                    {"kind": "run_progress", "payload": {"i": i}, "ts": "t"}
                )
            )
        await w.flush()
        for f in futs:
            if f:
                await f

    await asyncio.gather(burst(w1, 5), burst(w2, 5))
    assert len(store.rows) == 10
    assert all(r["seq"] is None for r in store.rows)


@pytest.mark.asyncio
async def test_cold_resume_settlement_fail_skips_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D8 冷路：settlement 落库失败 ⇒ 不 claim、paused frame 保留可重试."""
    store = _FakeStore()
    store.fail_kinds.add("checkpoint_resolved")
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store",
        lambda: store,
    )

    async def _no_existing(_turn_id: str, _key: tuple[str, str, str]) -> bool:
        return False

    monkeypatch.setattr(
        "agentcore.runtime.settlement._journal_has_settlement",
        _no_existing,
    )

    frame = _ask_frame()
    frames: dict[str, AskUserSuspension] = {frame.message_id: frame}
    claimed: list[str] = []

    async def fake_claim(message_id: str, *, conversation_id: str | None = None):
        claimed.append(message_id)
        return frames.pop(message_id, None)

    # Mirror resume_message: prewrite must succeed before claim.
    prewrite_error: Exception | None = None
    try:
        await prewrite_cold_resume_settlement(
            frame, decision="continue", note="", selected=["A"]
        )
    except Exception as e:  # noqa: BLE001 — route maps this to 5xx
        prewrite_error = e
    else:
        await fake_claim(frame.message_id, conversation_id=frame.conversation_id)

    assert prewrite_error is not None
    assert "forced fail" in str(prewrite_error)
    assert claimed == []
    assert frame.message_id in frames
    assert store.rows == []


@pytest.mark.asyncio
async def test_cold_resume_pipeline_emit_dedupes_prewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D8 冷路：预写后 resume writer 种子化 dedupe ⇒ pipeline 重放 emit 不重复落库."""
    store = _FakeStore()
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store",
        lambda: store,
    )

    async def _no_existing(_turn_id: str, _key: tuple[str, str, str]) -> bool:
        return False

    monkeypatch.setattr(
        "agentcore.runtime.settlement._journal_has_settlement",
        _no_existing,
    )

    event = checkpoint_resolved(
        checkpoint_id="ck1", decision="continue", note="", selected=["A"]
    )
    await prewrite_settlement_direct(
        turn_id="m1",
        conversation_id="c1",
        trace_id=None,
        event=event,
    )
    assert [r["entry"]["kind"] for r in store.rows] == ["checkpoint_resolved"]

    # Resume pipeline: new writer seeded from claim-rehydrated journal_entries.
    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id=None)
    seed_settlement_dedupe_from_entries(writer, [entry_from_sse(event)])
    token = current_journal_writer.set(writer)
    try:
        fut = writer.schedule_append(entry_from_sse(event))
        await writer.flush()
        if fut:
            await fut
    finally:
        current_journal_writer.reset(token)

    kinds = [r["entry"]["kind"] for r in store.rows]
    assert kinds.count("checkpoint_resolved") == 1


def test_align_rewrites_loser_decision_on_same_checkpoint() -> None:
    """抢帧：journal 先落下败方 decision 时，对齐后必须改成赢家的。"""
    entries = [
        {"kind": "checkpoint_required", "payload": {"checkpoint_id": "ck1"}, "ts": "t0"},
        {
            "kind": "checkpoint_resolved",
            "payload": {"checkpoint_id": "ck1", "decision": "stop", "note": "from-a"},
            "ts": "t1",
        },
    ]
    aligned = align_cold_resume_resolved_to_winner(
        entries, turn_id="m1", checkpoint_id="ck1", decision="continue"
    )
    assert aligned is not None
    assert [e["kind"] for e in aligned] == ["checkpoint_required", "checkpoint_resolved"]
    assert aligned[-1]["payload"]["decision"] == "continue"
    assert aligned[-1]["payload"]["note"] == "from-a"
    assert entries[-1]["payload"]["decision"] == "stop"


def test_align_is_noop_when_journal_already_matches_winner() -> None:
    entries = [
        {
            "kind": "plan_review_resolved",
            "payload": {"checkpoint_id": "ck1", "decision": "continue"},
            "ts": "t1",
        }
    ]
    assert (
        align_cold_resume_resolved_to_winner(
            entries, turn_id="m1", checkpoint_id="ck1", decision="continue"
        )
        is None
    )


def test_align_collapses_duplicate_resolved_rows_to_winner() -> None:
    """同键双写（两端都过了 has_settlement 检查）：只留一行赢家的。"""
    entries = [
        {"kind": "turn_started", "payload": {}, "ts": "t0"},
        {
            "kind": "plan_review_resolved",
            "payload": {"checkpoint_id": "ck1", "decision": "stop", "note": "a"},
            "ts": "t1",
        },
        {
            "kind": "plan_review_resolved",
            "payload": {"checkpoint_id": "ck1", "decision": "continue", "note": "b"},
            "ts": "t2",
        },
    ]
    aligned = align_cold_resume_resolved_to_winner(
        entries, turn_id="m1", checkpoint_id="ck1", decision="continue"
    )
    assert aligned is not None
    resolved = [e for e in aligned if e["kind"] == "plan_review_resolved"]
    assert len(resolved) == 1
    assert resolved[0]["payload"]["decision"] == "continue"
    assert resolved[0]["payload"]["note"] == "a"
    assert aligned[0]["kind"] == "turn_started"


def test_align_leaves_other_checkpoints_and_required_rows() -> None:
    entries = [
        {"kind": "checkpoint_required", "payload": {"checkpoint_id": "ck1"}, "ts": "t0"},
        {
            "kind": "checkpoint_resolved",
            "payload": {"checkpoint_id": "ck-other", "decision": "stop"},
            "ts": "t1",
        },
    ]
    assert (
        align_cold_resume_resolved_to_winner(
            entries, turn_id="m1", checkpoint_id="ck1", decision="continue"
        )
        is None
    )


# --- 生命周期撞键回归（LV 黄金场实跑抓到的丢事实 bug）---
# 教训：dedupe 键曾折叠成交互族（required/resolved/orphaned 同键），宿主回合里的
# stage_card_required 行把同卡 resolved / orphaned 的落库静默吞掉。


def _required_entry(card_id: str = "sc1") -> dict[str, Any]:
    return {
        "kind": "stage_card_required",
        "payload": {"stage_card_id": card_id, "motion": "M", "sides": []},
        "ts": "t",
    }


def test_settlement_dedupe_key_distinguishes_lifecycle() -> None:
    req = settlement_dedupe_key(
        "t1", "stage_card_required", {"stage_card_id": "sc1"}
    )
    res = settlement_dedupe_key(
        "t1", "stage_card_resolved", {"stage_card_id": "sc1"}
    )
    orph = settlement_dedupe_key(
        "t1", "interaction_orphaned", {"kind": "stage_card", "interaction_id": "sc1"}
    )
    assert req is not None and res is not None and orph is not None
    assert len({req, res, orph}) == 3, "同卡三种事实必须是三个键"
    # 同一事实两次 → 同键（幂等去重仍然成立）
    assert res == settlement_dedupe_key(
        "t1", "stage_card_resolved", {"stage_card_id": "sc1", "decision": "start_debate"}
    )


@pytest.mark.asyncio
async def test_stage_card_resolved_not_blocked_by_required_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """宿主回合已有 required 行（resume 种子化进 dedupe）⇒ resolved 落库不得被吞."""
    store = _FakeStore()
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store",
        lambda: store,
    )

    writer = TurnJournalWriter(turn_id="t_host", conversation_id="c1", trace_id=None)
    seed_settlement_dedupe_from_entries(writer, [_required_entry()])

    event = stage_card_resolved(stage_card_id="sc1", decision="start_debate", note="")
    fut = writer.schedule_append(entry_from_sse(event))
    await writer.flush()
    if fut:
        await fut

    kinds = [r["entry"]["kind"] for r in store.rows]
    assert kinds.count("stage_card_resolved") == 1


@pytest.mark.asyncio
async def test_stage_card_orphan_not_blocked_by_required_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """被取代的兄弟卡：required 行在场时 interaction_orphaned 落库不得被吞."""
    store = _FakeStore()
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store",
        lambda: store,
    )

    writer = TurnJournalWriter(turn_id="t_host", conversation_id="c1", trace_id=None)
    seed_settlement_dedupe_from_entries(writer, [_required_entry()])

    event = interaction_orphaned(
        interaction_id="sc1", kind="stage_card", reason="superseded"
    )
    fut = writer.schedule_append(entry_from_sse(event))
    await writer.flush()
    if fut:
        await fut

    kinds = [r["entry"]["kind"] for r in store.rows]
    assert kinds.count("interaction_orphaned") == 1


@pytest.mark.asyncio
async def test_stage_card_double_resolve_still_dedupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 resolved 事实双写（预写 + awaiter emit）仍只落一行."""
    store = _FakeStore()
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store",
        lambda: store,
    )

    writer = TurnJournalWriter(turn_id="t_host", conversation_id="c1", trace_id=None)
    token = current_journal_writer.set(writer)
    try:
        event = stage_card_resolved(
            stage_card_id="sc1", decision="start_debate", note=""
        )
        await prewrite_settlement(event)
        fut = writer.schedule_append(entry_from_sse(event))
        await writer.flush()
        if fut:
            await fut
    finally:
        current_journal_writer.reset(token)

    kinds = [r["entry"]["kind"] for r in store.rows]
    assert kinds.count("stage_card_resolved") == 1


@pytest.mark.asyncio
async def test_cold_resume_settlement_redemit_does_not_phantom_fact_log() -> None:
    """Cold resume: inherited ``*_resolved`` + dedupe re-emit must not grow fact_log.

    A phantom log row drifts index vs DB seq; finalize enumerate then inserts a
    duplicate trailing process_content (92f7fea8 / seq 286+287).
    """
    from agentcore.runtime.facts import Fact, TurnFactLog, current_fact_log, record_turn_fact

    entry = {
        "kind": "checkpoint_resolved",
        "payload": {"checkpoint_id": "ck1", "decision": "continue", "note": ""},
        "ts": "t1",
    }
    inherited = [
        {"kind": "turn_paused", "payload": {}, "ts": "t0"},
        entry,
    ]
    log = TurnFactLog(inherited_entries=inherited)
    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id=None)
    seed_settlement_dedupe_from_entries(writer, inherited)

    fl = current_fact_log.set(log)
    wt = current_journal_writer.set(writer)
    try:
        before = len(log.entries())
        assert writer.would_dedupe_settlement(entry)
        fut = record_turn_fact(
            Fact(kind="checkpoint_resolved", payload=dict(entry["payload"]), ts=entry["ts"])
        )
        if fut is not None:
            await fut
        assert len(log.entries()) == before
        assert sum(1 for e in log.entries() if e.get("kind") == "checkpoint_resolved") == 1
    finally:
        current_journal_writer.reset(wt)
        current_fact_log.reset(fl)


@pytest.mark.asyncio
async def test_hot_path_awaiter_still_records_fact_log_after_prewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hot-path prewrite bypasses fact_log; awaiter re-emit must record once (catch-up)."""
    from agentcore.runtime.facts import Fact, TurnFactLog, current_fact_log, record_turn_fact

    store = _FakeStore()
    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store",
        lambda: store,
    )

    event = checkpoint_resolved(
        checkpoint_id="ck1", decision="continue", note="", selected=["A"]
    )
    log = TurnFactLog()
    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id=None)
    fl = current_fact_log.set(log)
    wt = current_journal_writer.set(writer)
    try:
        await prewrite_settlement(event)
        assert len(log.entries()) == 0
        assert [r["entry"]["kind"] for r in store.rows] == ["checkpoint_resolved"]

        fut = record_turn_fact(
            Fact(kind=event.type.value, payload=dict(event.payload), ts=event.timestamp)
        )
        if fut is not None:
            await fut
        assert len(log.entries()) == 1
        assert log.entries()[0]["kind"] == "checkpoint_resolved"
        assert [r["entry"]["kind"] for r in store.rows] == ["checkpoint_resolved"]
    finally:
        current_journal_writer.reset(wt)
        current_fact_log.reset(fl)
