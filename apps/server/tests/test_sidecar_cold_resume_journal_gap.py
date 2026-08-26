"""Local sidecar cold resume durables the post-resume journal to Postgres.

Pause seals the outbox READY; resume unseals so live facts and complete
finalize can rewrite the same file. ``persist_turn_journal`` replaces the
live-band prefix via ``TurnJournalRepository.record`` so a complete snapshot
overwrites the pause occupancy ``[0, n)``. Higher seqs stay. Sidecar prewrite
stays outbox-only; a separate resume-boundary writeback lands settlement in PG
without blocking 开工.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from agentcore.conversation.store.outbox import (
    PHASE_OPEN,
    PHASE_READY,
    OutboxStore,
    journal_entries_from_map,
)
from agentcore.runtime.journal.fold import runs_from_entries
from agentcore.runtime.journal.pending_interactions import fold_interactions
from agentcore.runtime.journal.persist import (
    persist_sidecar_journal_best_effort,
    persist_turn_journal,
)
from agentcore.sidecar.settlement_prewrite import prewrite_sidecar_resume_settlement
from tests.test_sidecar_settlement_prewrite import _ask


def _kinds(entries: list[dict[str, Any]]) -> list[str]:
    return [str(e.get("kind") or "") for e in entries]


def _hang_frame() -> list[dict[str, Any]]:
    return [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "e1",
                "runs": [
                    {"id": "cap", "kind": "captain"},
                    {"id": "w1", "kind": "agent"},
                ],
            },
            "ts": "t0",
        },
        {
            "kind": "team_preview_required",
            "payload": {"checkpoint_id": "ck-tp"},
            "ts": "t1",
        },
    ]


def _complete_after_resume(hang: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return hang + [
        {
            "kind": "team_preview_resolved",
            "payload": {"checkpoint_id": "ck-tp", "decision": "continue"},
            "ts": "t2",
        },
        {"kind": "run_started", "payload": {"run_id": "w1", "kind": "agent"}, "ts": "t3"},
        {"kind": "run_completed", "payload": {"run_id": "w1"}, "ts": "t4"},
        {
            "kind": "turn_end",
            "payload": {"finish_reason": "end_turn"},
            "ts": "t5",
        },
    ]


async def _seal_pause(outbox: OutboxStore, hang: list[dict[str, Any]]) -> None:
    outbox.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="开工",
        message_id="m-tp",
        trace_id="a" * 32,
    )
    await outbox.begin_turn(
        conversation_id="c1", message_id="m-tp", trace_id="a" * 32
    )
    await outbox.seed_journal_entries_durable(
        turn_id="m-tp",
        conversation_id="c1",
        trace_id="a" * 32,
        entries=hang,
        user_message_id="u1",
    )
    await outbox.finalize(
        conversation_id="c1",
        user_message="开工",
        user_message_id="u1",
        assistant_content="预计 2 人开工",
        message_id="m-tp",
        trace_id="a" * 32,
        finish_reason="paused",
        journal_entries=hang,
    )


def test_resume_rewrite_explicitly_replaces_via_record() -> None:
    """Resume / outbox writeback must request wholesale ``record()``; salvage must not.

    Pins caller intent (the ``replace`` flag), not a whole-function ban on ``append``.
    """
    persist_src = inspect.getsource(persist_turn_journal)
    assert "replace: bool = False" in persist_src
    assert "repo.record(" in persist_src
    assert "repo.append(" in persist_src

    be_src = inspect.getsource(persist_sidecar_journal_best_effort)
    assert "replace=True" in be_src

    from agentcore.conversation.store.cloud import CloudStore

    local_src = inspect.getsource(CloudStore._finalize_local)
    assert "replace=True" in local_src
    salvage_src = inspect.getsource(CloudStore.salvage)
    assert "replace=True" not in salvage_src
    cloud_src = inspect.getsource(CloudStore._finalize_cloud)
    assert "replace=True" not in cloud_src
    pause_src = inspect.getsource(CloudStore._finalize_ceo_continue_pause)
    assert "replace=True" not in pause_src

    from agentcore.runtime.turn.interrupt import close_turn_interrupted

    interrupt_src = inspect.getsource(close_turn_interrupted)
    assert "persist_turn_journal" in interrupt_src
    assert "replace=True" not in interrupt_src
    assert "replace=False" in interrupt_src


@pytest.mark.asyncio
async def test_persist_turn_journal_replaces_pause_prefix(monkeypatch) -> None:
    """Complete snapshot evicts hang-frame rows that already occupy seq 0..n."""
    recorded: list[list[dict[str, Any]]] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def record(self, *, turn_id, conversation_id, trace_id, entries) -> None:
            del turn_id, conversation_id, trace_id
            recorded.append(list(entries))

        async def load(self, turn_id) -> list:
            del turn_id
            return []

    class Session:
        async def rollback(self) -> None:
            return None

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr(
        "agentcore.config.settings.observability_span_export_enabled", False
    )

    hang = _hang_frame()
    complete = _complete_after_resume(hang)
    await persist_turn_journal(
        Session(),  # type: ignore[arg-type]
        message_id="m-tp",
        conversation_id="c1",
        trace_id="a" * 32,
        entries=complete,
        replace=True,
    )
    assert recorded, "record() must be called"
    kinds = _kinds(recorded[0])
    assert "team_preview_required" in kinds
    assert "team_preview_resolved" in kinds
    assert "run_started" in kinds
    assert "run_completed" in kinds
    assert recorded[0][-1]["payload"]["finish_reason"] == "end_turn"
    cards = fold_interactions(recorded[0])
    assert not any(c.kind == "team_preview" for c in cards)


@pytest.mark.asyncio
async def test_merge_persist_does_not_wipe_denser_progressive_rows(monkeypatch) -> None:
    """Salvage / cloud live default is merge: sparse snapshot must not call record()."""
    appended: list[tuple[int, str]] = []
    recorded: list[object] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def record(self, **_kw) -> None:
            recorded.append(_kw)

        async def append(self, *, turn_id, seq, conversation_id, trace_id, entry) -> int | None:
            del turn_id, conversation_id, trace_id
            appended.append((seq, str(entry.get("kind") or "")))
            return None  # occupied seq → insert-if-absent no-op

    class Session:
        async def rollback(self) -> None:
            return None

    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", Repo)
    monkeypatch.setattr(
        "agentcore.config.settings.observability_span_export_enabled", False
    )

    sparse = [
        {"kind": "run_plan", "payload": {"execution_id": "e1"}},
        {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-tp"}},
    ]
    await persist_turn_journal(
        Session(),  # type: ignore[arg-type]
        message_id="m-tp",
        conversation_id="c1",
        trace_id="a" * 32,
        entries=sparse,
        replace=False,
    )
    assert recorded == []
    assert appended == [(0, "run_plan"), (1, "team_preview_required")]


def test_sparse_salvage_cannot_evict_denser_prefix_under_merge() -> None:
    """Mimic ``on_conflict_do_nothing(turn_id, seq)``: display salvage keeps denser PG rows."""
    denser = [
        {"kind": "run_plan", "payload": {"execution_id": "e1"}},
        {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-tp"}},
        {"kind": "run_started", "payload": {"run_id": "w1", "kind": "agent"}},
        {"kind": "run_completed", "payload": {"run_id": "w1"}},
    ]
    sparse = [
        {"kind": "run_plan", "payload": {"execution_id": "e1"}},
        {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-tp"}},
    ]
    by_seq: dict[int, dict[str, Any]] = {i: e for i, e in enumerate(denser)}
    for seq, entry in enumerate(sparse):
        if seq not in by_seq:
            by_seq[seq] = entry
    landed = [by_seq[i] for i in sorted(by_seq)]
    kinds = _kinds(landed)
    assert "run_started" in kinds
    assert "run_completed" in kinds
    assert len(landed) == len(denser)


@pytest.mark.asyncio
async def test_sidecar_prewrite_does_not_touch_pg_turn_journal(
    tmp_path, monkeypatch
) -> None:
    """Cloud prewrite hits ConversationStore.append_journal (PG). Sidecar must not."""
    calls: list[str] = []

    async def _forbid_persist(*_a, **_kw) -> None:
        calls.append("persist_turn_journal")

    async def _forbid_append(*_a, **_kw) -> None:
        calls.append("cloud_append_journal")

    monkeypatch.setattr(
        "agentcore.runtime.journal.persist.persist_turn_journal", _forbid_persist
    )
    monkeypatch.setattr(
        "agentcore.conversation.store.cloud.CloudStore.append_journal",
        _forbid_append,
    )

    outbox = OutboxStore(tmp_path / "outbox")
    outbox.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="开工",
        message_id="m-tp",
        trace_id="a" * 32,
    )
    await outbox.begin_turn(
        conversation_id="c1", message_id="m-tp", trace_id="a" * 32
    )
    susp = _ask(message_id="m-tp")
    entry = await prewrite_sidecar_resume_settlement(
        outbox,
        susp,
        decision="continue",
        selected=["是"],
        user_message_id="u1",
        trace_id="a" * 32,
    )
    assert entry["kind"] == "checkpoint_resolved"
    assert calls == []
    record = outbox.find_record_by_message_id("m-tp")
    assert record is not None
    kinds = _kinds(journal_entries_from_map(record.get("journal")) or [])
    assert "checkpoint_required" in kinds
    assert "checkpoint_resolved" in kinds
    assert any(
        isinstance((e.get("payload") or {}).get("resume_frame"), dict)
        for e in (journal_entries_from_map(record.get("journal")) or [])
        if e.get("kind") == "checkpoint_resolved"
    )


@pytest.mark.asyncio
async def test_pause_ready_resume_rewrites_journal_and_appends_live(tmp_path) -> None:
    """Unseal after pause READY lets live append + complete finalize replace the snapshot."""
    hang = _hang_frame()
    outbox = OutboxStore(tmp_path / "outbox")
    await _seal_pause(outbox, hang)
    sealed = outbox.find_record_by_message_id("m-tp")
    assert sealed is not None
    assert sealed["phase"] == PHASE_READY
    assert "finalize" in sealed["ops"]

    await outbox.reopen_for_resume(
        turn_id="m-tp",
        user_message_id="u1",
        conversation_id="c1",
        trace_id="a" * 32,
    )
    opened = outbox.find_record_by_message_id("m-tp")
    assert opened is not None
    assert opened["phase"] == PHASE_OPEN
    assert "finalize" not in opened["ops"]
    assert "reopen_for_resume" in opened["ops"]
    assert isinstance(opened.get("resume_after_seq"), int)

    allocated = await outbox.append_journal(
        turn_id="m-tp",
        seq=None,
        conversation_id="c1",
        trace_id="a" * 32,
        entry={"kind": "run_started", "payload": {"run_id": "w1", "kind": "agent"}},
    )
    assert allocated is not None

    complete = _complete_after_resume(hang)
    await outbox.finalize(
        conversation_id="c1",
        user_message="开工",
        user_message_id="u1",
        assistant_content="工人已收工",
        message_id="m-tp",
        trace_id="a" * 32,
        finish_reason="end_turn",
        journal_entries=complete,
    )
    after = outbox.find_record_by_message_id("m-tp")
    assert after is not None
    kinds = _kinds(journal_entries_from_map(after.get("journal")) or [])
    assert after["phase"] == PHASE_READY
    assert "team_preview_required" in kinds
    assert "team_preview_resolved" in kinds
    assert "run_started" in kinds
    assert "run_completed" in kinds
    assert after.get("finish_reason") == "end_turn"
    assert after.get("content") == "工人已收工"
    cards = fold_interactions(journal_entries_from_map(after.get("journal")) or [])
    assert not any(c.kind == "team_preview" for c in cards)
    runs = runs_from_entries(journal_entries_from_map(after.get("journal")) or [])
    types = [e.get("type") for e in ((runs or {}).get("events") or [])]
    assert "team_preview_resolved" not in types
    assert "run_started" in types


@pytest.mark.asyncio
async def test_reopen_for_resume_missing_file_is_noop(tmp_path) -> None:
    outbox = OutboxStore(tmp_path / "outbox")
    await outbox.reopen_for_resume(turn_id="m-tp", user_message_id="u-missing")
    assert outbox.find_record_by_message_id("m-tp") is None
    assert list((tmp_path / "outbox").glob("*.json")) == []


@pytest.mark.asyncio
async def test_sidecar_prewrite_on_ready_outbox_is_not_pg(
    tmp_path, monkeypatch
) -> None:
    """Durable prewrite onto a READY pause file still never calls persist_turn_journal."""
    persist_calls = 0

    async def _count_persist(*_a, **_kw) -> None:
        nonlocal persist_calls
        persist_calls += 1

    monkeypatch.setattr(
        "agentcore.runtime.journal.persist.persist_turn_journal", _count_persist
    )

    outbox = OutboxStore(tmp_path / "outbox")
    outbox.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="开工",
        message_id="m-tp",
        trace_id="a" * 32,
    )
    await outbox.begin_turn(
        conversation_id="c1", message_id="m-tp", trace_id="a" * 32
    )
    susp = _ask(message_id="m-tp")
    await outbox.finalize(
        conversation_id="c1",
        user_message="原始问题",
        user_message_id="u1",
        assistant_content="要继续吗？",
        message_id="m-tp",
        trace_id="a" * 32,
        finish_reason="paused",
        journal_entries=list(susp.journal_entries),
    )
    await prewrite_sidecar_resume_settlement(
        outbox,
        susp,
        decision="continue",
        selected=["是"],
        user_message_id="u1",
        trace_id="a" * 32,
    )
    assert persist_calls == 0
    kinds = _kinds(
        journal_entries_from_map(
            outbox.find_record_by_message_id("m-tp").get("journal")
        )
        or []
    )
    assert "checkpoint_resolved" in kinds


def test_pause_journal_without_resolved_folds_pending_and_no_worker_start() -> None:
    """Leftover hang-frame: retired kickoff pair does not fold a pending card."""
    hang = _hang_frame()
    cards = fold_interactions(hang)
    assert not any(c.kind == "team_preview" for c in cards)

    runs = runs_from_entries(hang)
    events = (runs or {}).get("events") or []
    types = [e.get("type") for e in events]
    assert "team_preview_required" not in types
    assert "team_preview_resolved" not in types
    assert "run_started" not in types


def test_persist_replace_lands_resolved_and_worker_facts() -> None:
    """Wholesale replace (record): complete snapshot is what GET messages folds."""
    hang = _hang_frame()
    complete = _complete_after_resume(hang)
    landed = list(complete)
    kinds = _kinds(landed)
    assert kinds[0] == "run_plan"
    assert "team_preview_required" in kinds
    assert "team_preview_resolved" in kinds
    assert "run_started" in kinds
    assert "run_completed" in kinds
    assert landed[-1]["payload"]["finish_reason"] == "end_turn"
    assert not any(c.kind == "team_preview" for c in fold_interactions(landed))


@pytest.mark.asyncio
async def test_resume_writeback_persists_outbox_journal(tmp_path, monkeypatch) -> None:
    """Resume-boundary hop persists current outbox journal; prewrite itself does not."""
    captured: list[dict[str, Any]] = []

    async def _capture_persist(_session, **kw) -> None:
        captured.append(kw)

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(
        "agentcore.runtime.journal.persist.persist_turn_journal", _capture_persist
    )
    monkeypatch.setattr("agentcore.db.base.async_session_factory", lambda: _CM())

    susp = _ask(message_id="m-tp")
    hang = list(susp.journal_entries)
    outbox = OutboxStore(tmp_path / "outbox")
    await _seal_pause(outbox, hang)
    await outbox.reopen_for_resume(turn_id="m-tp", user_message_id="u1")
    await prewrite_sidecar_resume_settlement(
        outbox,
        susp,
        decision="continue",
        selected=["是"],
        user_message_id="u1",
        trace_id="a" * 32,
    )
    assert captured == []

    record = outbox.find_record_by_message_id("m-tp")
    assert record is not None
    entries = journal_entries_from_map(record.get("journal"))
    await persist_sidecar_journal_best_effort(
        message_id="m-tp",
        conversation_id="c1",
        trace_id="a" * 32,
        entries=entries,
    )
    assert captured
    assert captured[0].get("replace") is True
    kinds = _kinds(list(captured[0].get("entries") or []))
    assert "checkpoint_resolved" in kinds
    assert fold_interactions(list(captured[0].get("entries") or []))[0].status == "resolved"


@pytest.mark.asyncio
async def test_resume_writeback_failure_does_not_raise(monkeypatch) -> None:
    def _boom() -> None:
        raise RuntimeError("pg down")

    monkeypatch.setattr("agentcore.db.base.async_session_factory", _boom)
    await persist_sidecar_journal_best_effort(
        message_id="m-tp",
        conversation_id="c1",
        trace_id="a" * 32,
        entries=[{"kind": "team_preview_resolved", "payload": {}}],
    )
