"""Best-effort journal persistence to Postgres."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.config import settings
from agentcore.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


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
    * ``replace=False`` (default): merge via seq ``append`` (insert-if-absent).
      Salvage / cloud live may hold a sparser display view; occupied seqs stay
      because progressive append-on-emit already owns denser rows. Extra
      overflow-band rows beyond ``len(entries)`` are left in place.

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
    try:
        if replace:
            await repo.record(
                turn_id=message_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                entries=entries,
            )
        else:
            for seq, entry in enumerate(entries):
                await repo.append(
                    turn_id=message_id,
                    seq=seq,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    entry=entry,
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
