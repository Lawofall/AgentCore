"""Best-effort journal persistence to Postgres."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.runtime.journal.entries import KIND_TURN_END, last_turn_end_finish

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

_CANCELLED = "cancelled"
_PAUSED = "paused"


def _cancelled_turn_end_entry() -> dict[str, Any]:
    return {
        "kind": KIND_TURN_END,
        "payload": {"finish_reason": _CANCELLED},
        "ts": None,
    }


def _last_turn_end_entry(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in reversed(entries):
        if (entry.get("kind") or "") == KIND_TURN_END:
            return entry
    return None


async def _load_turn_end_finish(repo: Any, message_id: str) -> str | None:
    """Best-effort last ``turn_end`` finish on disk. Missing ``load`` → ``None``."""
    load = getattr(repo, "load", None)
    if load is None:
        return None
    try:
        return last_turn_end_finish(await load(message_id))
    except Exception:  # noqa: BLE001 — persist must never break the turn
        return None


async def _load_live_max_seq(repo: Any, message_id: str) -> int | None:
    """Highest live-band seq, or ``None`` when empty / repo has no ``max_seq``."""
    max_seq = getattr(repo, "max_seq", None)
    if max_seq is None:
        return None
    try:
        return await max_seq(message_id)
    except Exception:  # noqa: BLE001 — persist must never break the turn
        return None


async def _merge_live_band(
    repo: Any,
    *,
    message_id: str,
    conversation_id: str,
    trace_id: str | None,
    entries: list[dict[str, Any]],
) -> None:
    """Insert-if-absent inside occupied live seqs; extend the band only for a closer.

    Append-on-emit already owns live seqs. Re-enumerating ``fact_log`` as ``0..n``
    after a pause snapshot that also carries overflow-band rows shifts the index
    past ``max_seq`` and inserts a duplicate trailing ``process_content``.
    """
    live_max = await _load_live_max_seq(repo, message_id)
    if live_max is None:
        for seq, entry in enumerate(entries):
            await repo.append(
                turn_id=message_id,
                seq=seq,
                conversation_id=conversation_id,
                trace_id=trace_id,
                entry=entry,
            )
        return
    for seq, entry in enumerate(entries):
        if seq > live_max:
            break
        await repo.append(
            turn_id=message_id,
            seq=seq,
            conversation_id=conversation_id,
            trace_id=trace_id,
            entry=entry,
        )
    closer = _last_turn_end_entry(entries)
    if closer is None:
        return
    incoming_finish = last_turn_end_finish([closer])
    landed = await _load_turn_end_finish(repo, message_id)
    if landed in (incoming_finish, _CANCELLED):
        return
    await repo.append(
        turn_id=message_id,
        seq=None,
        conversation_id=conversation_id,
        trace_id=trace_id,
        entry=closer,
    )


async def persist_turn_journal(
    session: AsyncSession,
    *,
    message_id: str | None,
    conversation_id: str,
    trace_id: str | None,
    entries: list[dict[str, Any]] | None,
    replace: bool = False,
) -> None:
    """Record a turn's replay payload to the journal (唯一事实源), best-effort.

    Called from the message-persistence tail right after the assistant row is
    written, on the SAME session, keyed by the assistant ``message_id``.
    Local sidecar pause snapshots also land here (cloud live pause writes the
    same table via ``save_paused_turn``); ``obs.turn_spans`` / ``team_batch``
    ride this choke point.

    Two write semantics — callers must pick explicitly; there is no length /
    count heuristic (a shorter snapshot is not "less complete"):

    * ``replace=True``: caller holds the authoritative *prefix* (sidecar resume
      rewrite / outbox writeback). :meth:`TurnJournalRepository.record` deletes
      only the live-band occupancy ``[0, n)`` this snapshot occupies so a resume
      reusing ``turn_id`` overwrites the pause prefix. Overflow-band rows stay;
      they are not copied back by kind.
    * ``replace=False`` (default): merge via seq ``append`` (insert-if-absent)
      **inside the live band already occupied**. Salvage / sparse display views
      may fill holes; occupied seqs stay because append-on-emit already owns
      them. The list index must not extend the live band — ``fact_log``
      inherited overflow / phantom rows are longer than ``max_seq+1`` and
      would insert a duplicate trailing ``process_content``. A missing (or
      more-final) ``turn_end`` is appended with ``seq=None`` (MAX+1). Extra
      overflow-band rows stay. Empty live band still writes ``0..n`` from
      the list (first persist / tests).

    User-stop ``turn_end(cancelled)`` is sticky: a later pause snapshot
    (``replace=True`` keep-tail, or merge that drops the closer) must not leave
    ``paused`` / missing as the last finish. Incoming ``end_turn`` / ``error``
    still replace it — this is not a success-path ``turn_end`` inventor.

    A failure must NEVER break the turn: it rolls back only this write and logs
    — the reply is already committed and the worst case is a turn that won't
    replay its graph.

    ``entries`` is the §8.3 fact-log stream (execution facts interleaved with
    forwarded display facts, plus process / ``turn_end`` tail). Callers that only
    hold a display ``runs`` payload must flatten via
    :func:`entries.journal_entries_from_display_runs` before calling.
    """
    if not message_id or not entries:
        return
    from agentcore.db.repositories import TurnJournalRepository

    repo = TurnJournalRepository(session)
    incoming_finish = last_turn_end_finish(entries)
    existing_cancelled = False
    if incoming_finish in (None, _PAUSED):
        existing_cancelled = await _load_turn_end_finish(repo, message_id) == _CANCELLED
    try:
        if replace:
            await repo.record(
                turn_id=message_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                entries=entries,
            )
        else:
            await _merge_live_band(
                repo,
                message_id=message_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                entries=entries,
            )
        # Keep-tail / merge-drop can leave a pause closer last, or drop the
        # cancelled fact onto an occupied seq. Re-append so refresh/follow
        # project cancelled — not still-running or interrupted.
        if incoming_finish == _CANCELLED or existing_cancelled:
            landed = await _load_turn_end_finish(repo, message_id)
            if landed != _CANCELLED:
                await repo.append(
                    turn_id=message_id,
                    seq=None,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    entry=_cancelled_turn_end_entry(),
                )
    except Exception as e:  # noqa: BLE001 — journal persistence must never break the turn
        await session.rollback()
        logger.warning(
            "journal.persist_failed",
            message_id=message_id,
            error=str(e),
        )
        return

    # D2 观测：把同一份耐久 entries 投影成执行 span 树并导出（off the user path、
    # best-effort）。这里是所有回合路径（首轮 / 重答 / handoff / resume / salvage）写
    # 耐久 journal 的唯一汇点，故 span 树天然覆盖全路径。导出自身吞异常、绝不影响回合。
    if settings.observability_span_export_enabled:
        from agentcore.runtime.spans import export_turn_spans

        export_turn_spans(
            entries,
            trace_id=trace_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )


async def persist_sidecar_journal_best_effort(
    *,
    message_id: str,
    conversation_id: str,
    trace_id: str | None,
    entries: list[dict[str, Any]] | None,
) -> None:
    """Resume-boundary PG writeback (symmetric with pause local-turns). Never raises.

    Sidecar prewrite stays outbox-only; this hop is a separate best-effort persist
    so a hard refresh after 开做 can read settlement before the complete READY
    drain. Failure must not block starting the resumed turn.
    """
    if not message_id or not entries:
        return
    try:
        from agentcore.db.base import async_session_factory

        async with async_session_factory() as session:
            await persist_turn_journal(
                session,
                message_id=message_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                entries=entries,
                replace=True,
            )
    except Exception as e:  # noqa: BLE001 — resume start must not wait on PG
        logger.warning(
            "journal.persist_failed",
            message_id=message_id,
            error=str(e),
        )
