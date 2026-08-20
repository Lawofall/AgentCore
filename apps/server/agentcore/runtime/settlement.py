"""Settlement 预写（提问确认交互统一 P1 · D8）.

热路 resolve：端点把 settlement 事实插队入 writer 队列并 await 落库 → 再 settle Future
→ awaiter emit ``*_resolved`` SSE（journal 侧命中 dedupe 跳过）。

冷路 resume：settlement 先直写落库 → 再 claim frame。

dedupe 键 ``(turn_id, kind, 自有id)``；进程内集合（多 worker 化之前须迁共享存储）。
预写与 awaiter emit 用同一 payload factory，完全同形。
"""

from __future__ import annotations

import time
from typing import Any

from agentcore.runtime.events.types import EventType, SSEEvent
from agentcore.runtime.interaction import INTERACTION_KIND_SPECS
from agentcore.runtime.journal.pending_interactions import settlement_dedupe_key
from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer

# Settlement event kinds that participate in prewrite + dedupe.
# Resolved events + reconnect-only required (stage_card is posted as a settlement
# fact on a closed host turn) + the cross-kind orphan fact. Derived from
# INTERACTION_KIND_SPECS so a new kind cannot silently miss the dedupe set.
SETTLEMENT_EVENT_KINDS: frozenset[str] = frozenset(
    {
        *(
            spec.resolved_event
            for spec in INTERACTION_KIND_SPECS.values()
            if spec.resolved_event is not None
        ),
        *(
            spec.required_event
            for spec in INTERACTION_KIND_SPECS.values()
            if spec.reconnect_answerable and not spec.hot
        ),
        EventType.INTERACTION_ORPHANED.value,
    }
)


def entry_from_sse(event: SSEEvent) -> dict[str, Any]:
    """Journal entry shape matching sink/fact-log (同形于 awaiter emit 落库)."""
    return {
        "kind": event.type.value,
        "payload": dict(event.payload),
        "ts": event.timestamp or time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }


async def prewrite_settlement(event: SSEEvent) -> bool:
    """Hot-path: enqueue settlement at writer head and await durable write.

    Returns True if written (or already deduped as written). Raises on write failure
    so the resolve endpoint can return 5xx without settling the interaction Future.
    Returns False when no writer is bound (tests / unbound) — caller may fall through.
    """
    writer = current_journal_writer.get()
    if writer is None:
        return False
    entry = entry_from_sse(event)
    future = writer.schedule_settlement_append(entry)
    if future is None:
        return False
    await future  # propagates write failure
    return True


async def prewrite_settlement_direct(
    *,
    turn_id: str,
    conversation_id: str,
    trace_id: str | None,
    event: SSEEvent,
) -> None:
    """Cold-path: write settlement directly via ConversationStore (before claim).

    Registers dedupe so a later resume-pipeline emit of the same fact is skipped.
    Idempotent: if the same ``(turn_id, kind, id)`` is already in journal, skip the write
    (still registers ambient-writer dedupe when present).
    """
    from agentcore.conversation.store import get_conversation_store

    entry = entry_from_sse(event)
    key = settlement_dedupe_key(turn_id, event.type.value, dict(event.payload))

    # Mark dedupe on ambient writer if present (same process resume).
    writer = current_journal_writer.get()
    if writer is not None:
        writer.register_settlement_dedupe(entry)

    if key is not None and await _journal_has_settlement(turn_id, key):
        return

    store = get_conversation_store()
    await store.append_journal(
        turn_id=turn_id,
        seq=None,
        conversation_id=conversation_id,
        trace_id=trace_id,
        entry=entry,
    )


async def _journal_has_settlement(turn_id: str, key: tuple[str, str, str]) -> bool:
    """True if journal already holds a settlement with the same dedupe key."""
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories import TurnJournalRepository

    try:
        async with async_session_factory() as db:
            entries = await TurnJournalRepository(db).load(turn_id)
    except Exception:  # noqa: BLE001 — treat probe failure as "not present"
        return False
    for e in entries or []:
        ek = settlement_dedupe_key(turn_id, str(e.get("kind") or ""), dict(e.get("payload") or {}))
        if ek == key:
            return True
    return False


def align_cold_resume_resolved_to_winner(
    entries: list[dict[str, Any]],
    *,
    turn_id: str,
    checkpoint_id: str,
    decision: str,
) -> list[dict[str, Any]] | None:
    """Rewrite cold-path ``*_resolved`` so journal matches the claim winner.

    Prewrite lands before ``claim_paused_turn`` and dedupes on
    ``(turn_id, kind, checkpoint_id)`` — not ``decision``. The first click to
    persist can therefore be the race loser, while ``paused_turn_outcomes``
    records whoever actually won the frame. After a successful claim, the
    winner's decision is the authority: same-checkpoint ``*_resolved`` rows
    whose decision differs are rewritten; same-key duplicates collapse to one
    winner row. Returns the aligned list, or ``None`` when already consistent
    (caller must not persist).
    """
    cid = (checkpoint_id or "").strip()
    if not cid:
        return None

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    changed = False
    for entry in entries:
        kind = str(entry.get("kind") or "")
        payload = dict(entry.get("payload") or {})
        key = settlement_dedupe_key(turn_id, kind, payload)
        if key is None or not kind.endswith("_resolved") or key[2] != cid:
            out.append(entry)
            continue
        if key in seen:
            changed = True
            continue
        seen.add(key)
        if str(payload.get("decision") or "") == decision:
            out.append(entry)
            continue
        out.append({**entry, "payload": {**payload, "decision": decision}})
        changed = True
    return out if changed else None


def already_settled_in_writer(event: SSEEvent) -> bool:
    """True if this settlement was already prewritten (idempotent resolve)."""
    writer = current_journal_writer.get()
    if writer is None:
        return False
    key = settlement_dedupe_key(writer.turn_id, event.type.value, dict(event.payload))
    return key is not None and writer.has_settlement_dedupe(key)


def seed_settlement_dedupe_from_entries(
    writer: TurnJournalWriter, entries: list[dict[str, Any]] | None
) -> None:
    """Register settlement keys already in journal so resume re-emit skips journal write.

    Cold-path D8: endpoint prewrites ``*_resolved`` before claim; claim re-hydrates
    those rows into ``suspension.journal_entries``; resume pipeline must seed the new
    writer so the recover-path emit does not duplicate the row.

    ``record_turn_fact`` also skips appending to ``TurnFactLog`` when the same key is
    already in the inherited prefix — otherwise a phantom log row drifts index vs DB
    ``seq`` and finalize enumerate can insert a duplicate trailing ``process_content``.
    """
    for entry in entries or []:
        kind = str(entry.get("kind") or "")
        if kind not in SETTLEMENT_EVENT_KINDS and not kind.endswith("_resolved"):
            continue
        writer.register_settlement_dedupe(entry)


def cold_resume_settlement_event(
    suspension: Any,
    *,
    decision: str,
    note: str = "",
    selected: list[str] | None = None,
    excluded_run_ids: list[str] | None = None,
    write_capability_overrides: list[dict[str, str]] | None = None,
    model_overrides: dict[str, dict[str, str]] | None = None,
) -> SSEEvent:
    """Build the same ``*_resolved`` SSE recover will emit (D8 同形)."""
    from agentcore.runtime.events import (
        checkpoint_resolved,
        plan_review_resolved,
        team_preview_resolved,
    )
    from agentcore.runtime.kickoff.team_veto import (
        should_apply_team_veto,
        veto_summary_for_resolved,
    )
    from agentcore.runtime.suspension import (
        AskUserSuspension,
        PlanReviewSuspension,
        TeamPreviewSuspension,
    )
    from agentcore.tools.builtin.ask_user.schema import option_label

    if isinstance(suspension, AskUserSuspension):
        allowed = {option_label(o) for q in suspension.questions for o in q.get("options", [])}
        picks = [s for s in (selected or []) if s in allowed]
        return checkpoint_resolved(
            checkpoint_id=suspension.checkpoint_id,
            decision=decision,
            note=note,
            selected=picks,
        )
    if isinstance(suspension, PlanReviewSuspension):
        return plan_review_resolved(
            checkpoint_id=suspension.checkpoint_id,
            decision=decision,
            note=note,
        )
    if isinstance(suspension, TeamPreviewSuspension):
        excl: list[str] | None = None
        overrides: list[dict[str, str]] | None = None
        models: dict[str, dict[str, str]] | None = None
        if should_apply_team_veto(suspension, decision):
            excl, overrides, models = veto_summary_for_resolved(
                excluded_run_ids=excluded_run_ids,
                write_capability_overrides=write_capability_overrides,
                model_overrides=model_overrides,
            )
            excl = excl or None
            overrides = overrides or None
            models = models or None
        return team_preview_resolved(
            checkpoint_id=suspension.checkpoint_id,
            decision=decision,
            note=note,
            excluded_run_ids=excl,
            write_capability_overrides=overrides,
            model_overrides=models,
        )
    raise ValueError(f"unknown suspension kind for cold settlement: {type(suspension)!r}")


async def prewrite_cold_resume_settlement(
    suspension: Any,
    *,
    decision: str,
    note: str = "",
    selected: list[str] | None = None,
    excluded_run_ids: list[str] | None = None,
    write_capability_overrides: list[dict[str, str]] | None = None,
    model_overrides: dict[str, dict[str, str]] | None = None,
) -> None:
    """Cold-path D8: durable-write ``*_resolved`` before ``claim_paused_turn``.

    Prefers ambient writer (rare on cold path); falls through to direct store write.
    Raises on write failure so the resume endpoint can 5xx without claiming the frame.
    """
    event = cold_resume_settlement_event(
        suspension,
        decision=decision,
        note=note,
        selected=selected,
        excluded_run_ids=excluded_run_ids,
        write_capability_overrides=write_capability_overrides,
        model_overrides=model_overrides,
    )
    written = await prewrite_settlement(event)
    if written:
        return
    await prewrite_settlement_direct(
        turn_id=suspension.message_id,
        conversation_id=suspension.conversation_id,
        trace_id=getattr(suspension, "trace_id", None),
        event=event,
    )
