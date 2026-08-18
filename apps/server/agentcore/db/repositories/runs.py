"""Execution-domain data access: handoff jobs, recoverable run sessions, paused
turns, the turn journal (唯一事实源) and per-turn metrics (观测看板)."""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, case, delete, distinct, func, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import new_id
from agentcore.db.models import (
    PAUSED_TURN_EXPIRED,
    PAUSED_TURN_SETTLED,
    Conversation,
    HandoffJob,
    Message,
    PausedTurnOutcomeRow,
    PausedTurnRow,
    RunSessionRow,
    TurnJournalRow,
    TurnMetricsRow,
    User,
)

from ._base import commit_or_flush, strip_nul


class HandoffJobRepository:
    """Local→云 handoff jobs (双模式工作区 P2e / e2): a dispatched cloud team run.

    Tracks one job's lifecycle (pending → running → succeeded/failed, then
    applied/discarded for cloud-replica reclaim) and the two snapshot ids that
    bracket it (the base it ran on, the result it produced). All reads are
    owner-scoped so a non-owner gets nothing (IDOR-safe), mirroring the
    conversation repo.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        user_id: str,
        source_conversation_id: str,
        job_conversation_id: str,
        base_snapshot_id: str,
        task: str,
        commit: bool = True,
    ) -> HandoffJob:
        job = HandoffJob(
            id=new_id(),
            user_id=user_id,
            source_conversation_id=source_conversation_id,
            job_conversation_id=job_conversation_id,
            base_snapshot_id=base_snapshot_id,
            task=task,
        )
        self._session.add(job)
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(job)
        return job

    async def get_by_id(self, job_id: str, *, user_id: str | None = None) -> HandoffJob | None:
        conditions = [HandoffJob.id == job_id]
        if user_id is not None:
            conditions.append(HandoffJob.user_id == user_id)
        result = await self._session.execute(select(HandoffJob).where(*conditions))
        return result.scalar_one_or_none()

    async def list_for_source(
        self, source_conversation_id: str, *, user_id: str
    ) -> Sequence[HandoffJob]:
        """A source conversation's handoff jobs, newest first (owner-scoped)."""
        result = await self._session.execute(
            select(HandoffJob)
            .where(
                HandoffJob.source_conversation_id == source_conversation_id,
                HandoffJob.user_id == user_id,
            )
            .order_by(HandoffJob.created_at.desc())
        )
        return result.scalars().all()

    async def list_open_past_retention(
        self, *, before: datetime, limit: int
    ) -> Sequence[HandoffJob]:
        """Finished but still-open jobs (succeeded/failed) past ``before``.

        Backs retention aging of unapplied/undiscarded cloud hosts: Diff must
        stay available until this cutoff — never soft-delete earlier on succeed.

        Only hosts that are not yet soft-deleted are returned, so a prior aging
        pass (or apply/discard reclaim) cannot starve the batch with no-ops.
        """
        result = await self._session.execute(
            select(HandoffJob)
            .join(Conversation, Conversation.id == HandoffJob.job_conversation_id)
            .where(
                HandoffJob.status.in_(("succeeded", "failed")),
                HandoffJob.finished_at.is_not(None),
                HandoffJob.finished_at <= before,
                Conversation.deleted_at.is_(None),
            )
            .order_by(HandoffJob.finished_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def mark_running(self, job_id: str) -> None:
        await self._session.execute(
            update(HandoffJob).where(HandoffJob.id == job_id).values(status="running")
        )
        await self._session.commit()

    async def mark_succeeded(self, job_id: str, *, result_snapshot_id: str) -> None:
        await self._session.execute(
            update(HandoffJob)
            .where(HandoffJob.id == job_id)
            .values(
                status="succeeded",
                result_snapshot_id=result_snapshot_id,
                finished_at=datetime.now(UTC),
            )
        )
        await self._session.commit()

    async def mark_failed(self, job_id: str, *, error: str) -> None:
        await self._session.execute(
            update(HandoffJob)
            .where(HandoffJob.id == job_id)
            .values(status="failed", error=error, finished_at=datetime.now(UTC))
        )
        await self._session.commit()

    async def mark_applied(self, job_id: str) -> bool:
        """Terminal: cloud result merged back. Only from ``succeeded``. Returns whether updated."""
        result = await self._session.execute(
            update(HandoffJob)
            .where(HandoffJob.id == job_id, HandoffJob.status == "succeeded")
            .values(status="applied")
        )
        await self._session.commit()
        return bool(result.rowcount)

    async def mark_discarded(self, job_id: str) -> bool:
        """Terminal: user abandoned the cloud replica. From ``succeeded`` or ``failed``."""
        result = await self._session.execute(
            update(HandoffJob)
            .where(
                HandoffJob.id == job_id,
                HandoffJob.status.in_(("succeeded", "failed")),
            )
            .values(status="discarded")
        )
        await self._session.commit()
        return bool(result.rowcount)


class RunSessionRepository:
    """Durable store for recoverable worker runs (留人 跨进程落盘, 乙 热修 P3).

    The write path is an upsert by ``run_id``: a freshly-delegated worker inserts;
    a later ``revise`` of the same run updates its transcript / content /
    recall_count and bumps ``updated_at`` (which the TTL sweep reads). The read path
    rehydrates a single run on an in-memory roster miss (restart / eviction).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        conversation_id: str,
        run_id: str,
        spec: dict,
        transcript: list,
        content: str,
        recall_count: int,
        trace_id: str | None = None,
    ) -> None:
        """Insert a recoverable session, or update it in place if its ``run_id``
        already exists (a re-revised run). Idempotent re-delegation re-writes the
        same content; a revision advances transcript / recall_count. ``trace_id``
        is set on first insert only (NOT in the update set) so it keeps pointing at
        the interaction that originally spawned the worker, not a later revise."""
        now = datetime.now()
        clean_spec = strip_nul(spec)
        clean_transcript = strip_nul(transcript)
        clean_content = strip_nul(content)
        stmt = (
            pg_insert(RunSessionRow)
            .values(
                run_id=run_id,
                conversation_id=conversation_id,
                spec=clean_spec,
                transcript=clean_transcript,
                content=clean_content,
                recall_count=recall_count,
                trace_id=trace_id,
            )
            .on_conflict_do_update(
                index_elements=["run_id"],
                set_={
                    "spec": clean_spec,
                    "transcript": clean_transcript,
                    "content": clean_content,
                    "recall_count": recall_count,
                    "updated_at": now,
                },
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def get(self, run_id: str) -> RunSessionRow | None:
        result = await self._session.execute(
            select(RunSessionRow).where(RunSessionRow.run_id == run_id)
        )
        return result.scalar_one_or_none()

    async def delete_stale(self, *, before: datetime, limit: int) -> int:
        """Delete up to ``limit`` sessions idle since before ``before`` (7-day TTL).
        Batched so a sweep never holds one huge transaction; returns rows removed."""
        stale = select(RunSessionRow.run_id).where(RunSessionRow.updated_at < before).limit(limit)
        result = await self._session.execute(
            delete(RunSessionRow).where(RunSessionRow.run_id.in_(stale))
        )
        await self._session.commit()
        return result.rowcount or 0

    async def delete_for_conversation(self, conversation_id: str) -> int:
        """Cascade-clear recoverable sessions when a conversation is soft/hard deleted.

        现场生命周期跟随对话：对话不在则现场不可唤回。Does not commit — caller owns the txn.
        """
        result = await self._session.execute(
            delete(RunSessionRow).where(RunSessionRow.conversation_id == conversation_id)
        )
        return int(result.rowcount or 0)


# 结算方 for the disposition nobody drove: the retention sweep, not a device.
_SWEEP_SETTLER = "retention_sweep"


def _outcome_upsert(
    *,
    message_id: str,
    conversation_id: str,
    frame: dict | None,
    outcome: str,
    decision: str,
    settled_by: str,
) -> PgInsert:
    """The stamp a frame's consumer writes in its own transaction.

    Card identity (``checkpoint_id`` / ``card_kind``) is read off the consumed frame,
    so it always describes the pause cycle that actually ended — never an earlier peek.
    Upsert, because a turn can pause → be settled → pause again → be settled again, and
    only the latest conclusion answers for the card as it stands now.
    """
    data = frame if isinstance(frame, dict) else {}
    values: dict[str, object] = {
        "message_id": message_id,
        "conversation_id": conversation_id,
        "outcome": outcome,
        "card_kind": str(data.get("kind") or ""),
        "checkpoint_id": str(data.get("checkpoint_id") or ""),
        "decision": decision,
        "settled_by": settled_by,
        "decided_at": datetime.now(UTC),
    }
    return (
        pg_insert(PausedTurnOutcomeRow)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["message_id"],
            set_={k: v for k, v in values.items() if k != "message_id"},
        )
    )


class PausedTurnRepository:
    """Durable store for turns suspended at a plan_review checkpoint (结构化挂起
    turn 级落盘) — plus the terminal outcome of every frame it hands out.

    Write is an upsert keyed by the turn's ``message_id`` (re-pausing the same turn
    after a resume-then-pause overwrites in place). The read path either claims one
    row for resume (``claim`` = read-then-delete in one transaction, so two racing
    ``/resume`` calls can't both continue the same turn) or lists a conversation's
    pending paused turns for reopen. ``trace_id`` is set on first insert only so it
    keeps pointing at the originating interaction.

    **Frame ⊕ outcome.** Winning the atomic claim is what「结了这张卡」means, so the
    winner stamps its conclusion (:class:`PausedTurnOutcomeRow`) in the very
    transaction that consumed the frame — decision, moment, ``checkpoint_id``, settler.
    The TTL sweep stamps ``expired`` the same way. A frame row and its outcome never
    coexist: ``upsert`` (save / claim rollback) clears the outcome because the card is
    pending again. Everyone who lost the race reads the winner's row instead of
    re-deriving「谁结的」from a journal whose last entry is usually their own prewrite.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        message_id: str,
        conversation_id: str,
        user_id: str,
        frame: dict,
        trace_id: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        stmt = (
            pg_insert(PausedTurnRow)
            .values(
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                frame=frame,
                trace_id=trace_id,
            )
            .on_conflict_do_update(
                index_elements=["message_id"],
                set_={"frame": frame, "updated_at": now},
            )
        )
        await self._session.execute(stmt)
        # A pending frame and a terminal outcome are mutually exclusive states of one
        # card: a re-pause (or a rolled-back claim) means nobody has decided it yet, so
        # a conclusion left by an earlier cycle must not survive to answer for this one.
        await self._session.execute(
            delete(PausedTurnOutcomeRow).where(PausedTurnOutcomeRow.message_id == message_id)
        )
        await self._session.commit()

    async def get(self, message_id: str) -> PausedTurnRow | None:
        result = await self._session.execute(
            select(PausedTurnRow).where(PausedTurnRow.message_id == message_id)
        )
        return result.scalar_one_or_none()

    async def get_outcome(
        self, message_id: str, *, conversation_id: str
    ) -> PausedTurnOutcomeRow | None:
        """How this turn's paused card ended, or ``None`` if nothing ended it.

        Conversation-scoped (IDOR-safe): an id from another conversation reads as a
        card that never existed here.
        """
        result = await self._session.execute(
            select(PausedTurnOutcomeRow).where(
                PausedTurnOutcomeRow.message_id == message_id,
                PausedTurnOutcomeRow.conversation_id == conversation_id,
            )
        )
        return result.scalar_one_or_none()

    async def claim(
        self,
        message_id: str,
        *,
        conversation_id: str | None = None,
        decision: str,
        settled_by: str = "",
    ) -> PausedTurnRow | None:
        """Atomically read-and-delete one paused turn for resume, stamping the outcome.

        DELETE ... RETURNING means only ONE caller wins the row (a second concurrent
        ``/resume`` finds it gone), so a paused turn is never resumed twice. Scoped to
        ``conversation_id`` when given so a frame is only ever claimed within the
        conversation the caller has already proven it owns (IDOR-safe — a guessed
        ``message_id`` from another conversation won't match, so it is neither returned
        nor deleted). Returns the row (detached values) or ``None``.

        ``decision`` is what the winner is about to apply; it lands in the same
        transaction as the delete, so there is no instant where the frame is gone and
        the conclusion is not yet readable. ``settled_by`` records 结算方 (the origin
        device) for the same reason — a loser must be able to tell「另一端决定的」from
        「我自己刚提交过」without consulting a journal it has already written to.

        The frame's own ``checkpoint_id`` / ``kind`` are the authority for the stamped
        card identity (not the caller's earlier peek, which may have read a different
        pause cycle). A frame carrying no ``checkpoint_id`` cannot be settled: the CHECK
        rejects it, the transaction rolls back, and the frame survives for a retry.
        """
        stmt = delete(PausedTurnRow).where(PausedTurnRow.message_id == message_id)
        if conversation_id is not None:
            stmt = stmt.where(PausedTurnRow.conversation_id == conversation_id)
        result = await self._session.execute(stmt.returning(PausedTurnRow))
        row = result.scalar_one_or_none()
        if row is not None:
            await self._session.execute(
                _outcome_upsert(
                    message_id=row.message_id,
                    conversation_id=row.conversation_id,
                    frame=row.frame,
                    outcome=PAUSED_TURN_SETTLED,
                    decision=decision,
                    settled_by=settled_by,
                )
            )
        await self._session.commit()
        return row

    async def stamp_settled(
        self,
        *,
        message_id: str,
        conversation_id: str,
        frame: dict,
        decision: str,
        settled_by: str = "",
    ) -> None:
        """Stamp a settled conclusion without requiring a ``paused_turns`` row.

        Cloud :meth:`claim` writes this row in the same transaction that deletes the
        frame. Sidecar never inserts ``paused_turns`` (local JSON is its frame), so a
        later cloud ``POST .../resume`` would otherwise see ``outcome is None`` and
        report the card as regenerated. Any leftover frame is dropped here too
        (frame ⊕ outcome never coexist).
        """
        await self._session.execute(
            delete(PausedTurnRow).where(
                PausedTurnRow.message_id == message_id,
                PausedTurnRow.conversation_id == conversation_id,
            )
        )
        await self._session.execute(
            _outcome_upsert(
                message_id=message_id,
                conversation_id=conversation_id,
                frame=frame,
                outcome=PAUSED_TURN_SETTLED,
                decision=decision,
                settled_by=settled_by,
            )
        )
        await self._session.commit()

    async def list_pending(self, conversation_id: str) -> Sequence[PausedTurnRow]:
        """A conversation's paused turns (oldest first) for reopen-time rehydration."""
        result = await self._session.execute(
            select(PausedTurnRow)
            .where(PausedTurnRow.conversation_id == conversation_id)
            .order_by(PausedTurnRow.created_at.asc())
        )
        return result.scalars().all()

    async def list_pending_for_user(self, user_id: str) -> Sequence[PausedTurnRow]:
        """This user's paused turns on live conversations (oldest first).

        Soft-deleted (``deleted_at`` set) and already-gone conversations are
        excluded — an attention snapshot must not relight a chat the user deleted.
        """
        result = await self._session.execute(
            select(PausedTurnRow)
            .join(Conversation, Conversation.id == PausedTurnRow.conversation_id)
            .where(
                PausedTurnRow.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
            .order_by(PausedTurnRow.created_at.asc())
        )
        return result.scalars().all()

    async def exists_for_conversation(self, conversation_id: str) -> bool:
        """Whether the conversation holds ANY durably-paused turn (open-turn probe)."""
        result = await self._session.execute(
            select(PausedTurnRow.message_id)
            .where(PausedTurnRow.conversation_id == conversation_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def exists_for_message(self, message_id: str) -> bool:
        """Whether this assistant turn (``message_id``) is durably paused."""
        result = await self._session.execute(
            select(PausedTurnRow.message_id)
            .where(PausedTurnRow.message_id == message_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def delete(self, message_id: str) -> PausedTurnRow | None:
        """Drop a paused turn (live in-process resolve / timeout settled it instead).

        Returns the removed row (``None`` when there was nothing to remove) so the
        caller can tell「this really was a pending card」from「already gone」without a
        second query — the attention signal needs the frame's user / conversation to
        clear the badge, and only the winner of the delete may send it.

        Records NO outcome: a bare drop states nothing about how the card ended, so a
        later「继续」on it reads as a turn that is simply not there. Any caller that IS
        settling a decision must use :meth:`claim` (or stamp its own conclusion) —
        otherwise it consumes the frame while telling the other端 nothing.
        """
        result = await self._session.execute(
            delete(PausedTurnRow)
            .where(PausedTurnRow.message_id == message_id)
            .returning(PausedTurnRow)
        )
        row = result.scalar_one_or_none()
        await self._session.commit()
        return row

    async def claim_ceo_continue_lock(
        self,
        message_id: str,
        *,
        conversation_id: str,
        user_id: str,
        frame: dict,
    ) -> PausedTurnRow | None:
        """Atomically consume the CEO continue latch. Exactly one caller wins.

        Marks an existing unclaimed ``kind=ceo_continue`` row claimed (no delete —
        deleting would reopen the no-lock + ``usage.outcome=paused`` hole). When no
        row exists, inserts ``frame`` only if the assistant row is paused
        (``ON CONFLICT DO NOTHING`` so two inserters still have one winner). Does
        not stamp ``paused_turn_outcomes`` (this latch is not a checkpoint card).
        """
        claimed = await self._session.execute(
            update(PausedTurnRow)
            .where(
                PausedTurnRow.message_id == message_id,
                PausedTurnRow.conversation_id == conversation_id,
                PausedTurnRow.frame["kind"].astext == "ceo_continue",
                func.coalesce(PausedTurnRow.frame["claimed"].astext, "") != "true",
            )
            .values(
                frame=PausedTurnRow.frame.concat(type_coerce({"claimed": True}, JSONB)),
                updated_at=datetime.now(UTC),
            )
            .returning(PausedTurnRow)
        )
        row = claimed.scalar_one_or_none()
        if row is not None:
            await self._session.commit()
            return row

        paused = await self._session.execute(
            select(Message.id).where(
                Message.id == message_id,
                Message.conversation_id == conversation_id,
                Message.usage["outcome"].astext == "paused",
            )
        )
        if paused.scalar_one_or_none() is None:
            await self._session.commit()
            return None

        inserted = await self._session.execute(
            pg_insert(PausedTurnRow)
            .values(
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                frame=frame,
            )
            .on_conflict_do_nothing(index_elements=["message_id"])
            .returning(PausedTurnRow)
        )
        row = inserted.scalar_one_or_none()
        await self._session.commit()
        return row

    async def delete_claimed_ceo_continue_lock(
        self, message_id: str, *, conversation_id: str
    ) -> None:
        """Drop a consumed continue latch after a successful continue.

        Only a claimed ``ceo_continue`` row is removed, so a re-pause that already
        upserted a fresh unclaimed lock is left alone.
        """
        await self._session.execute(
            delete(PausedTurnRow).where(
                PausedTurnRow.message_id == message_id,
                PausedTurnRow.conversation_id == conversation_id,
                PausedTurnRow.frame["kind"].astext == "ceo_continue",
                PausedTurnRow.frame["claimed"].astext == "true",
            )
        )
        await self._session.commit()

    async def delete_stale(self, *, before: datetime, limit: int) -> list[tuple[str, str]]:
        """Delete up to ``limit`` paused turns idle since before ``before`` (TTL sweep).

        ``updated_at`` advances on re-pause (resume → pause again), so an actively
        re-paused turn stays alive while one abandoned past the window is pruned. Each
        pruned turn is stamped ``expired`` in the same transaction — that stamp is how a
        later「继续」learns the card was swept rather than settled or regenerated; it is
        the sweep's half of the frame ⊕ outcome pair. Its ``turn_journal`` rows go too:
        the journal-so-far is stored there (唯一事实源, §8.3) and nothing will ever
        continue this turn to consume it. Batched (one transaction) so a sweep never
        holds one huge lock.

        Returns the pruned ``(message_id, conversation_id)`` pairs, not just a count:
        the frame is only half of a pause — the assistant row still carries the
        ``usage.paused`` latch, and the sweep has to clear it or the client keeps
        painting a decision card whose frame is gone.
        """
        stale = (
            await self._session.execute(
                select(
                    PausedTurnRow.message_id,
                    PausedTurnRow.conversation_id,
                    PausedTurnRow.frame,
                )
                .where(PausedTurnRow.updated_at < before)
                .limit(limit)
            )
        ).all()
        if not stale:
            return []
        stale_ids = [row[0] for row in stale]
        for message_id, conversation_id, frame in stale:
            await self._session.execute(
                _outcome_upsert(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    frame=frame,
                    outcome=PAUSED_TURN_EXPIRED,
                    decision="",
                    settled_by=_SWEEP_SETTLER,
                )
            )
        await self._session.execute(
            delete(TurnJournalRow).where(TurnJournalRow.turn_id.in_(stale_ids))
        )
        await self._session.execute(
            delete(PausedTurnRow).where(PausedTurnRow.message_id.in_(stale_ids))
        )
        await self._session.commit()
        return [(str(row[0]), str(row[1])) for row in stale]


class TurnJournalRepository:
    """The §8.6 ``Journal`` port's Postgres impl — the唯一事实源 store (§8.3).

    A turn's execution facts are stored append-only, ordered by ``seq`` within a
    ``turn_id`` (== the assistant ``message_id``). :meth:`record` replaces the turn's
    rows wholesale (idempotent for a resume that reuses the id); :meth:`load_map`
    batch-loads several turns for the read-time projection (no N+1 when a history
    page renders). Entries are plain ``{kind, payload, ts}`` dicts — the
    ``runs``↔entries transform lives in ``runtime/journal.py`` (the engine domain),
    keeping this layer pure storage.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        trace_id: str | None,
        entries: Sequence[dict],
    ) -> None:
        """Replace a turn's journal with ``entries`` (delete-then-insert, one commit).

        Replace (not append) so a resume reusing the same ``turn_id`` re-persists the
        full, current fact stream without duplicating the pre-pause prefix. A no-op
        for empty ``entries`` after clearing any stale rows.
        """
        await self._session.execute(delete(TurnJournalRow).where(TurnJournalRow.turn_id == turn_id))
        if entries:
            self._session.add_all(
                [
                    TurnJournalRow(
                        turn_id=turn_id,
                        seq=seq,
                        kind=str(entry.get("kind") or ""),
                        payload=strip_nul(entry.get("payload") or {}),
                        ts=entry.get("ts"),
                        conversation_id=conversation_id,
                        trace_id=trace_id,
                    )
                    for seq, entry in enumerate(entries)
                ]
            )
        await self._session.commit()

    async def append(
        self,
        *,
        turn_id: str,
        seq: int | None,
        conversation_id: str,
        trace_id: str | None,
        entry: dict,
    ) -> int | None:
        """Append one journal fact (emit-on-write path, one commit).

        **seq 双模式 (D7)**：
        - ``seq is None`` (live)：事务内 ``pg_advisory_xact_lock(hash(turn_id))`` 后
          ``COALESCE(MAX(seq),-1)+1`` 原子分配——跨 writer 无竞态。禁止无锁 MAX+1。
        - ``seq is int`` (merge / outbox 回写)：显式 seq + ``(turn_id, seq)`` 幂等去重，
          禁止云端重排。

        Returns the durable ``seq`` on fresh insert, or ``None`` on merge-mode duplicate
        no-op (so the SSE barrier can stamp ``id:`` without a second read).
        """
        from sqlalchemy import text

        if seq is None:
            # Live: advisory lock serializes same-turn writers, then allocate.
            # hashtext is stable for a given turn_id within PG.
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:tid))"),
                {"tid": turn_id},
            )
            result = await self._session.execute(
                text(
                    "SELECT COALESCE(MAX(seq), -1) + 1 FROM turn_journal WHERE turn_id = :tid"
                ),
                {"tid": turn_id},
            )
            allocated = int(result.scalar_one())
            self._session.add(
                TurnJournalRow(
                    turn_id=turn_id,
                    seq=allocated,
                    kind=str(entry.get("kind") or ""),
                    payload=strip_nul(entry.get("payload") or {}),
                    ts=entry.get("ts"),
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                )
            )
            await self._session.commit()
            return allocated

        # Merge mode: explicit seq + idempotent conflict.
        stmt = (
            pg_insert(TurnJournalRow)
            .values(
                turn_id=turn_id,
                seq=seq,
                kind=str(entry.get("kind") or ""),
                payload=strip_nul(entry.get("payload") or {}),
                ts=entry.get("ts"),
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
            .on_conflict_do_nothing(index_elements=["turn_id", "seq"])
            .returning(TurnJournalRow.turn_id)
        )
        result = await self._session.execute(stmt)
        inserted = result.scalar_one_or_none() is not None
        await self._session.commit()
        return seq if inserted else None

    async def load(self, turn_id: str) -> list[dict]:
        """One turn's facts as ordered ``{kind, payload, ts}`` entries (``[]`` if none)."""
        result = await self._session.execute(
            select(TurnJournalRow)
            .where(TurnJournalRow.turn_id == turn_id)
            .order_by(TurnJournalRow.seq.asc())
        )
        return [{"kind": r.kind, "payload": r.payload, "ts": r.ts} for r in result.scalars().all()]

    async def list_recent_turn_ids(
        self, conversation_id: str, *, limit: int = 40, after: datetime | None = None
    ) -> list[str]:
        """Distinct ``turn_id``s for a conversation, newest-first by session time.

        Orders by ``max(created_at)`` per turn — **not** in-turn ``seq`` (which is a
        within-turn counter and would starve older turns' durable cards after a long
        journal). Shared by stage_card scan / recovery / interaction resolve.

        ``after`` keeps only turns whose journal ends strictly later than that instant.
        The memory consolidation pass passes its watermark so the action inventory it
        summarizes covers the same turns as its message window, not every recent turn.
        """
        cid = (conversation_id or "").strip()
        if not cid or limit <= 0:
            return []
        stmt = (
            select(TurnJournalRow.turn_id)
            .where(TurnJournalRow.conversation_id == cid)
            .group_by(TurnJournalRow.turn_id)
            .order_by(func.max(TurnJournalRow.created_at).desc())
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.having(func.max(TurnJournalRow.created_at) > after)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_question_posted_hosts(
        self,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        posted_before: datetime | None = None,
        posted_after: datetime | None = None,
        exclude_turn_id: str | None = None,
        limit: int | None = None,
    ) -> list[tuple[str, str]]:
        """Turns that journaled ``question_posted``, oldest first.

        Returns ``(conversation_id, turn_id)``. Pending vs resolved is fold's job —
        this is only the host scan (injection / 「在等你」snapshot / 7-day sweep).
        Soft-deleted conversations are omitted so a badge cannot relight a deleted chat.
        ``limit`` is a sweep batch bound, not a product cap on hanging questions.
        """
        posted_at = func.min(TurnJournalRow.created_at)
        stmt = (
            select(TurnJournalRow.conversation_id, TurnJournalRow.turn_id)
            .join(Conversation, Conversation.id == TurnJournalRow.conversation_id)
            .where(
                TurnJournalRow.kind == "question_posted",
                Conversation.deleted_at.is_(None),
            )
            .group_by(TurnJournalRow.conversation_id, TurnJournalRow.turn_id)
            .order_by(posted_at.asc())
        )
        cid = (conversation_id or "").strip()
        if cid:
            stmt = stmt.where(TurnJournalRow.conversation_id == cid)
        uid = (user_id or "").strip()
        if uid:
            stmt = stmt.where(Conversation.user_id == uid)
        excluded = (exclude_turn_id or "").strip()
        if excluded:
            stmt = stmt.where(TurnJournalRow.turn_id != excluded)
        if posted_before is not None:
            stmt = stmt.having(posted_at < posted_before)
        if posted_after is not None:
            stmt = stmt.having(posted_at >= posted_after)
        if limit is not None:
            if limit <= 0:
                return []
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [(str(cid_), str(tid)) for cid_, tid in result.all()]

    async def find_turn_id_for_question_posted(
        self, *, conversation_id: str, ask_id: str
    ) -> str | None:
        """Host turn of this conversation's ``question_posted`` with ``ask_id``."""
        from sqlalchemy import text

        cid = (conversation_id or "").strip()
        aid = (ask_id or "").strip()
        if not cid or not aid:
            return None
        result = await self._session.execute(
            text(
                """
                SELECT turn_id
                FROM turn_journal
                WHERE conversation_id = :cid
                  AND kind = 'question_posted'
                  AND payload->>'ask_id' = :ask_id
                ORDER BY created_at ASC
                LIMIT 1
                """
            ),
            {"cid": cid, "ask_id": aid},
        )
        row = result.first()
        return str(row[0]) if row and row[0] else None

    async def find_turn_id_for_execution(
        self, *, conversation_id: str, execution_id: str
    ) -> str | None:
        """Earliest assistant turn in ``conversation_id`` whose journal has
        ``run_plan`` for ``execution_id``.

        Used by跨回合同图追加 to re-home growth facts onto the host graph's ``turn_id``.
        """
        from sqlalchemy import text

        eid = (execution_id or "").strip()
        cid = (conversation_id or "").strip()
        if not eid or not cid:
            return None
        result = await self._session.execute(
            text(
                """
                SELECT turn_id
                FROM turn_journal
                WHERE conversation_id = :cid
                  AND kind = 'run_plan'
                  AND payload->>'execution_id' = :eid
                ORDER BY seq ASC
                LIMIT 1
                """
            ),
            {"cid": cid, "eid": eid},
        )
        row = result.first()
        return str(row[0]) if row and row[0] else None

    async def find_latest_multi_agent_execution(
        self,
        *,
        conversation_id: str,
        exclude_turn_id: str | None = None,
        prefer_turn_id: str | None = None,
        prefer_only: bool = False,
    ) -> str | None:
        """Newest team-graph ``execution_id`` in ``conversation_id`` (跨回合同图追加 latest 解析).

        Candidates are ``run_plan`` facts with ``plan_type='multi_agent'`` — debate graphs
        (``plan_type='debate'``) are not appendable and are excluded here. Cross-turn growth
        ``run_plan`` facts divert to the HOST turn's journal, so the newest matching row
        already points at the graph as last grown.

        ``prefer_turn_id``（本回合图优先）：若该 turn 上已有 multi_agent 图，返回其最新
        ``execution_id``。``prefer_only=True`` 时未命中本回合则返回 ``None``（不回落对话级）；
        默认未命中再回落对话级最新。``exclude_turn_id`` 仅用于 prompt 回显等「刻意不看本回合」
        场景——append ``latest`` 解析应传 ``prefer_turn_id``，勿排除本回合。
        """
        from sqlalchemy import text

        cid = (conversation_id or "").strip()
        if not cid:
            return None
        prefer = (prefer_turn_id or "").strip()
        if prefer:
            preferred = await self._session.execute(
                text(
                    """
                    SELECT payload->>'execution_id'
                    FROM turn_journal
                    WHERE conversation_id = :cid
                      AND turn_id = :pref
                      AND kind = 'run_plan'
                      AND payload->>'plan_type' = 'multi_agent'
                      AND COALESCE(payload->>'execution_id', '') != ''
                    ORDER BY created_at DESC, seq DESC
                    LIMIT 1
                    """
                ),
                {"cid": cid, "pref": prefer},
            )
            pref_row = preferred.first()
            if pref_row and pref_row[0]:
                return str(pref_row[0])
            if prefer_only:
                return None
        exclude = (exclude_turn_id or "").strip()
        exclusion = "AND turn_id != :ex" if exclude else ""
        result = await self._session.execute(
            text(
                f"""
                SELECT payload->>'execution_id'
                FROM turn_journal
                WHERE conversation_id = :cid
                  AND kind = 'run_plan'
                  AND payload->>'plan_type' = 'multi_agent'
                  AND COALESCE(payload->>'execution_id', '') != ''
                  {exclusion}
                ORDER BY created_at DESC, seq DESC
                LIMIT 1
                """
            ),
            {"cid": cid, "ex": exclude} if exclude else {"cid": cid},
        )
        row = result.first()
        return str(row[0]) if row and row[0] else None

    async def find_latest_website_style(
        self, *, conversation_id: str
    ) -> dict | None:
        """Newest ``website_style_confirmed`` payload for ``conversation_id`` (P1a style ledger).

        Cold rehydrate for ``build_website`` after process restart / new turn when the
        hot cache is empty. Returns the raw journal payload dict or ``None``.
        """
        from sqlalchemy import text

        cid = (conversation_id or "").strip()
        if not cid:
            return None
        result = await self._session.execute(
            text(
                """
                SELECT payload
                FROM turn_journal
                WHERE conversation_id = :cid
                  AND kind = 'website_style_confirmed'
                  AND COALESCE(payload->>'style_id', '') != ''
                ORDER BY created_at DESC, seq DESC
                LIMIT 1
                """
            ),
            {"cid": cid},
        )
        row = result.first()
        if not row or row[0] is None:
            return None
        payload = row[0]
        return dict(payload) if isinstance(payload, dict) else None

    async def find_latest_delivery_status(
        self, *, conversation_id: str, exclude_turn_id: str | None = None
    ) -> dict | None:
        """Newest ``delivery_status`` payload in ``conversation_id`` (可用性短问复用对账).

        Returns the raw journal payload dict or ``None``. ``exclude_turn_id`` drops the
        current turn so a short follow-up reuses the prior batch's reconciliation.
        """
        from sqlalchemy import text

        cid = (conversation_id or "").strip()
        if not cid:
            return None
        exclude = (exclude_turn_id or "").strip()
        exclusion = "AND turn_id != :ex" if exclude else ""
        result = await self._session.execute(
            text(
                f"""
                SELECT payload
                FROM turn_journal
                WHERE conversation_id = :cid
                  AND kind = 'delivery_status'
                  AND COALESCE(payload->>'execution_id', '') != ''
                  {exclusion}
                ORDER BY created_at DESC, seq DESC
                LIMIT 1
                """
            ),
            {"cid": cid, "ex": exclude} if exclude else {"cid": cid},
        )
        row = result.first()
        if not row or row[0] is None:
            return None
        payload = row[0]
        return dict(payload) if isinstance(payload, dict) else None

    async def find_latest_presentation_format(
        self, *, conversation_id: str
    ) -> dict | None:
        """Newest ``presentation_format_confirmed`` payload for ``conversation_id``.

        Cold rehydrate for presentation delivery after process restart / new turn when
        the hot cache is empty. Returns the raw journal payload dict or ``None``.
        """
        from sqlalchemy import text

        cid = (conversation_id or "").strip()
        if not cid:
            return None
        result = await self._session.execute(
            text(
                """
                SELECT payload
                FROM turn_journal
                WHERE conversation_id = :cid
                  AND kind = 'presentation_format_confirmed'
                  AND COALESCE(payload->>'format_id', '') != ''
                ORDER BY created_at DESC, seq DESC
                LIMIT 1
                """
            ),
            {"cid": cid},
        )
        row = result.first()
        if not row or row[0] is None:
            return None
        payload = row[0]
        return dict(payload) if isinstance(payload, dict) else None

    async def find_latest_automation_delivery(
        self, *, conversation_id: str
    ) -> dict | None:
        """Newest ``automation_delivery_confirmed`` payload for ``conversation_id``.

        Cold rehydrate for Agent/自动化 delivery after process restart / new turn when
        the hot cache is empty. Returns the raw journal payload dict or ``None``.
        """
        from sqlalchemy import text

        cid = (conversation_id or "").strip()
        if not cid:
            return None
        result = await self._session.execute(
            text(
                """
                SELECT payload
                FROM turn_journal
                WHERE conversation_id = :cid
                  AND kind = 'automation_delivery_confirmed'
                  AND COALESCE(payload->>'format_id', '') != ''
                ORDER BY created_at DESC, seq DESC
                LIMIT 1
                """
            ),
            {"cid": cid},
        )
        row = result.first()
        if not row or row[0] is None:
            return None
        payload = row[0]
        return dict(payload) if isinstance(payload, dict) else None

    async def find_latest_mlr_execution(self, *, conversation_id: str) -> str | None:
        """Newest MLR-shaped team graph: ``multi_agent`` ``run_plan`` containing a synthesizer run.

        批 A2 辩论进宿主图：playbook ``multi_lens_research`` 汇总员 raw id ``synthesizer``，
        经 DAG 铸造后为 ``{del_<uuid>|add_<uuid>}_synthesizer``——须同时匹配二者。
        Does **not** exclude the current turn (same-turn MLR → debate must resolve the host).
        """
        from sqlalchemy import text

        cid = (conversation_id or "").strip()
        if not cid:
            return None
        result = await self._session.execute(
            text(
                """
                SELECT payload->>'execution_id'
                FROM turn_journal
                WHERE conversation_id = :cid
                  AND kind = 'run_plan'
                  AND payload->>'plan_type' = 'multi_agent'
                  AND COALESCE(payload->>'execution_id', '') != ''
                  AND EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(COALESCE(payload->'runs', '[]'::jsonb)) AS r
                    WHERE COALESCE(r->>'id', '') = 'synthesizer'
                       OR COALESCE(r->>'agent_id', '') = 'synthesizer'
                       OR right(COALESCE(r->>'id', ''), 12) = '_synthesizer'
                       OR right(COALESCE(r->>'agent_id', ''), 12) = '_synthesizer'
                  )
                ORDER BY created_at DESC, seq DESC
                LIMIT 1
                """
            ),
            {"cid": cid},
        )
        row = result.first()
        return str(row[0]) if row and row[0] else None

    async def load_after(self, turn_id: str, after_seq: int) -> list[dict]:
        """Facts with ``seq > after_seq`` as ordered ``{seq, kind, payload, ts}`` (P3 cursor)."""
        result = await self._session.execute(
            select(TurnJournalRow)
            .where(
                TurnJournalRow.turn_id == turn_id,
                TurnJournalRow.seq > after_seq,
            )
            .order_by(TurnJournalRow.seq.asc())
        )
        return [
            {"seq": r.seq, "kind": r.kind, "payload": r.payload, "ts": r.ts}
            for r in result.scalars().all()
        ]

    async def max_seq(self, turn_id: str) -> int | None:
        """Highest journal ``seq`` for ``turn_id``, or ``None`` when the turn has no rows.

        Resume uses this to seed :class:`TurnJournalWriter` past any live append-on-emit
        facts that outran the pause snapshot (sidecar ``journal_entries`` can be shorter
        than the DB), avoiding UniqueViolation on the next append.
        """
        result = await self._session.execute(
            select(func.max(TurnJournalRow.seq)).where(TurnJournalRow.turn_id == turn_id)
        )
        return result.scalar_one_or_none()

    async def load_owned(self, turn_id: str, conversation_id: str) -> list[dict]:
        """One turn's facts, scoped to its conversation (IDOR-safe read).

        Same projection as :meth:`load` but filtered by ``conversation_id`` too, so a
        user who owns conversation A can't read conversation B's journal by passing a
        foreign ``turn_id`` — mirroring the conversation-scoped message delete. A
        cross-tenant pair simply matches no rows (``[]``).
        """
        result = await self._session.execute(
            select(TurnJournalRow)
            .where(
                TurnJournalRow.turn_id == turn_id,
                TurnJournalRow.conversation_id == conversation_id,
            )
            .order_by(TurnJournalRow.seq.asc())
        )
        return [{"kind": r.kind, "payload": r.payload, "ts": r.ts} for r in result.scalars().all()]

    async def load_map(self, turn_ids: Sequence[str]) -> dict[str, list[dict]]:
        """Several turns' facts keyed by ``turn_id`` (ordered entries), batched.

        One query over all ids (ordered by turn_id, seq) grouped in Python, so a
        history page projects every assistant message's replay payload without an
        N+1. Turns with no facts are simply absent from the map.
        """
        ids = list(dict.fromkeys(turn_ids))
        if not ids:
            return {}
        result = await self._session.execute(
            select(TurnJournalRow)
            .where(TurnJournalRow.turn_id.in_(ids))
            .order_by(TurnJournalRow.turn_id.asc(), TurnJournalRow.seq.asc())
        )
        grouped: dict[str, list[dict]] = {}
        for r in result.scalars().all():
            grouped.setdefault(r.turn_id, []).append(
                {"kind": r.kind, "payload": r.payload, "ts": r.ts}
            )
        return grouped


class TurnMetricsRepository:
    """Per-turn 运营观测 telemetry store — the admin 观测看板 data source.

    Writes one compact row per completed assistant turn (:meth:`record`, called
    best-effort at the turn's persistence tail) and serves the dashboard's
    platform-wide rollups: a window's health (:meth:`aggregate_health_for_window`
    — error rate / latency p95 / 委派率), the daily trend
    (:meth:`aggregate_daily_for_window`), and the recent-error feed
    (:meth:`list_recent_errors`). Aggregates are unscoped (every account) — admin
    is a cross-user surface; per-conversation drill-down (会话复盘, P2) joins these
    rows with messages + cost_events by trace_id.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def record(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        user_id: str,
        trace_id: str | None,
        agent_id: str | None,
        kind: str,
        status: str,
        finish_reason: str | None,
        error: str | None,
        rounds: int,
        duration_ms: int,
        delegated: bool,
        workers: int,
        input_tokens: int,
        output_tokens: int,
        boundary_yields: int = 0,
        scope_signals: int = 0,
        revises: int = 0,
        escalations: int = 0,
        audit_drops: int = 0,
        mode: str = "cloud",
    ) -> None:
        """Append one telemetry row for a completed turn (one commit).

        The caller (conversation service) supplies the already-computed turn
        outcome — this layer stays pure storage. A row id is minted here (Core
        bulk paths skip the ORM default, but this is a single ORM ``add``).
        ``mode`` is the ``finalize(mode=cloud|local)`` fork (engine location),
        default cloud so historical / seed writes stay correct. The 协作质量
        counters (boundary_yields / scope_signals / revises / escalations,
        学·度量 §2.5) default 0 so a plain single-agent turn writes zeros.
        """
        self._session.add(
            TurnMetricsRow(
                id=new_id(),
                turn_id=turn_id,
                conversation_id=conversation_id,
                user_id=user_id,
                trace_id=trace_id,
                agent_id=agent_id,
                kind=kind,
                mode=mode,
                status=status,
                finish_reason=finish_reason,
                error=error,
                rounds=rounds,
                duration_ms=duration_ms,
                delegated=delegated,
                workers=workers,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                boundary_yields=boundary_yields,
                scope_signals=scope_signals,
                revises=revises,
                escalations=escalations,
                audit_drops=audit_drops,
            )
        )
        await self._session.commit()

    async def aggregate_health_for_window(self, *, since: datetime) -> dict:
        """Platform-wide turn health since a cutoff (admin 观测看板 全站健康).

        One round-trip returns the rollup the dashboard needs: turn count, error
        count (status='error'), delegated count, average + p95 latency, average
        rounds, and token totals. Token sums count ``mode='cloud'`` only —
        local rows are sidecar / BYOK usage, not platform spend. Do not filter
        the window by ``input_tokens = 0``. The caller derives the rates
        (errors/turns, delegated/turns) so this layer returns only raw
        aggregates. p95 uses Postgres ``percentile_cont`` (NULL → 0 on an empty
        window). Filters on ``ix_turn_metrics_created``.
        """
        err = case((TurnMetricsRow.status == "error", 1), else_=0)
        dele = case((TurnMetricsRow.delegated.is_(True), 1), else_=0)
        # 协作质量 (学·度量 §2.5): 首计划存活 = delegated turns whose first plan ran without a
        # supervised boundary handing control back (boundary_yields == 0). The caller derives
        # 存活率 = survived / delegated; the raw counters back 返工/漂移/escalation rates.
        survived = case(
            (and_(TurnMetricsRow.delegated.is_(True), TurnMetricsRow.boundary_yields == 0), 1),
            else_=0,
        )
        stmt = select(
            func.count().label("turns"),
            func.coalesce(func.sum(err), 0).label("errors"),
            func.coalesce(func.sum(dele), 0).label("delegated"),
            func.coalesce(func.avg(TurnMetricsRow.duration_ms), 0).label("avg_duration"),
            func.percentile_cont(0.95)
            .within_group(TurnMetricsRow.duration_ms.asc())
            .label("p95_duration"),
            func.coalesce(func.avg(TurnMetricsRow.rounds), 0).label("avg_rounds"),
            func.coalesce(
                func.sum(
                    case((TurnMetricsRow.mode == "cloud", TurnMetricsRow.input_tokens), else_=0)
                ),
                0,
            ).label("input_tokens"),
            func.coalesce(
                func.sum(
                    case((TurnMetricsRow.mode == "cloud", TurnMetricsRow.output_tokens), else_=0)
                ),
                0,
            ).label("output_tokens"),
            func.coalesce(func.sum(survived), 0).label("first_plan_survived"),
            func.coalesce(func.sum(TurnMetricsRow.boundary_yields), 0).label("boundary_yields"),
            func.coalesce(func.sum(TurnMetricsRow.scope_signals), 0).label("scope_signals"),
            func.coalesce(func.sum(TurnMetricsRow.revises), 0).label("revises"),
            func.coalesce(func.sum(TurnMetricsRow.escalations), 0).label("escalations"),
        ).where(TurnMetricsRow.created_at >= since)
        row = (await self._session.execute(stmt)).one()
        return {
            "turns": int(row.turns or 0),
            "errors": int(row.errors or 0),
            "delegated": int(row.delegated or 0),
            "avg_duration_ms": int(row.avg_duration or 0),
            "p95_duration_ms": int(row.p95_duration or 0),
            "avg_rounds": float(row.avg_rounds or 0.0),
            "input_tokens": int(row.input_tokens or 0),
            "output_tokens": int(row.output_tokens or 0),
            # 协作质量 raw aggregates (caller derives rates over delegated/turns).
            "first_plan_survived": int(row.first_plan_survived or 0),
            "boundary_yields": int(row.boundary_yields or 0),
            "scope_signals": int(row.scope_signals or 0),
            "revises": int(row.revises or 0),
            "escalations": int(row.escalations or 0),
        }

    async def aggregate_daily_for_window(self, *, since: datetime) -> dict[str, dict]:
        """Daily turn/error counts (UTC days) since a cutoff — the dashboard trend.

        Groups the window into UTC calendar days (matching the cost trend's day
        boundaries) and returns an ``{iso_date: {turns, errors}}`` map (only days
        with rows); the caller zero-fills absent days for a fixed-length series.
        """
        day = func.date_trunc("day", func.timezone("UTC", TurnMetricsRow.created_at))
        err = case((TurnMetricsRow.status == "error", 1), else_=0)
        stmt = (
            select(
                day.label("day"),
                func.count().label("turns"),
                func.coalesce(func.sum(err), 0).label("errors"),
            )
            .where(TurnMetricsRow.created_at >= since)
            .group_by(day)
        )
        rows = (await self._session.execute(stmt)).all()
        return {
            row.day.date().isoformat(): {
                "turns": int(row.turns or 0),
                "errors": int(row.errors or 0),
            }
            for row in rows
        }

    async def list_recent_errors(self, *, limit: int = 20) -> Sequence[TurnMetricsRow]:
        """The most recent errored turns (status='error'), newest-first.

        The dashboard's「近期错误」feed and the entry point for 会话复盘 — each row
        carries the trace_id/conversation_id to drill from a failure into its full
        turn. Capped at ``limit`` (the long tail isn't actionable on a dashboard).
        """
        result = await self._session.execute(
            select(TurnMetricsRow)
            .where(TurnMetricsRow.status == "error")
            .order_by(TurnMetricsRow.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def list_for_conversation(self, conversation_id: str) -> Sequence[TurnMetricsRow]:
        """Every turn's telemetry for one conversation, oldest-first (会话复盘).

        The 复盘 timeline joins these to the conversation's messages by ``trace_id``;
        oldest-first matches the message thread's chronological order. Hits
        ``ix_turn_metrics_conversation_created``.
        """
        result = await self._session.execute(
            select(TurnMetricsRow)
            .where(TurnMetricsRow.conversation_id == conversation_id)
            .order_by(TurnMetricsRow.created_at.asc())
        )
        return result.scalars().all()

    async def latest_input_tokens(self, conversation_id: str) -> int | None:
        """Most recent turn's ``input_tokens`` for this conversation, or ``None``.

        Used by near-ceiling pre-turn compaction (定案⑦A) to decide whether the
        next send must await a fold before assembling history. Hits
        ``ix_turn_metrics_conversation_created``.
        """
        result = await self._session.execute(
            select(TurnMetricsRow.input_tokens)
            .where(TurnMetricsRow.conversation_id == conversation_id)
            .order_by(TurnMetricsRow.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return int(row)

    async def list_recent_for_user(
        self, user_id: str, *, limit: int = 20
    ) -> Sequence[TurnMetricsRow]:
        """The most recent turns for one account (用户详情下钻 最近活动), newest-first.

        The per-user counterpart of :meth:`list_recent_errors` — every turn (ok +
        error), capped at ``limit``, so the operator sees an account's latest
        activity and can drill any row into 会话复盘. Filters on ``user_id``; the
        ``ix_turn_metrics_created`` index serves the newest-first ordering (a
        bounded recent-N read, so a dedicated user index isn't warranted yet).
        """
        result = await self._session.execute(
            select(TurnMetricsRow)
            .where(TurnMetricsRow.user_id == user_id)
            .order_by(TurnMetricsRow.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def aggregate_stats_by_conversations(
        self, conversation_ids: Sequence[str]
    ) -> dict[str, dict[str, int]]:
        """Turn / error / multi-agent rollups per conversation (admin roster enrichment).

        One GROUP BY over the given ids. Ids with no telemetry are absent
        (callers default turns/errors/delegated_turns/workers to 0).
        """
        if not conversation_ids:
            return {}
        err = func.sum(case((TurnMetricsRow.status == "error", 1), else_=0))
        dele = func.sum(case((TurnMetricsRow.delegated.is_(True), 1), else_=0))
        stmt = (
            select(
                TurnMetricsRow.conversation_id.label("conversation_id"),
                func.count().label("turns"),
                err.label("errors"),
                dele.label("delegated_turns"),
                func.coalesce(func.max(TurnMetricsRow.workers), 0).label("workers"),
            )
            .where(TurnMetricsRow.conversation_id.in_(conversation_ids))
            .group_by(TurnMetricsRow.conversation_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {
            row.conversation_id: {
                "turns": int(row.turns),
                "errors": int(row.errors),
                "delegated_turns": int(row.delegated_turns or 0),
                "workers": int(row.workers or 0),
            }
            for row in rows
        }

    async def list_platform(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        user_id: str | None = None,
        conversation_id: str | None = None,
        status: str | None = None,
        delegated: bool | None = None,
        trace_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        include_deleted_conversations: bool = True,
    ) -> tuple[Sequence[tuple[TurnMetricsRow, Conversation, User | None]], int]:
        """Paginated cross-user turn feed for the admin 对话 page (newest-first).

        Joins conversation + owner for list context. Hidden handoff-host
        conversations are always excluded; ``include_deleted_conversations``
        controls soft-deleted chats. ``delegated`` filters multi-agent turns;
        ``trace_id`` resolves a turn for 复盘 deep-link.
        """
        base = (
            select(TurnMetricsRow, Conversation, User)
            .join(Conversation, Conversation.id == TurnMetricsRow.conversation_id)
            .outerjoin(User, User.user_id == TurnMetricsRow.user_id)
            .where(Conversation.mode != "handoff")
        )
        if not include_deleted_conversations:
            base = base.where(Conversation.deleted_at.is_(None))
        if user_id is not None:
            base = base.where(TurnMetricsRow.user_id == user_id)
        if conversation_id is not None:
            base = base.where(TurnMetricsRow.conversation_id == conversation_id)
        if status is not None:
            base = base.where(TurnMetricsRow.status == status)
        if delegated is True:
            base = base.where(TurnMetricsRow.delegated.is_(True))
        elif delegated is False:
            base = base.where(TurnMetricsRow.delegated.is_(False))
        if trace_id is not None:
            base = base.where(TurnMetricsRow.trace_id == trace_id)
        if since is not None:
            base = base.where(TurnMetricsRow.created_at >= since)
        if until is not None:
            base = base.where(TurnMetricsRow.created_at <= until)

        count_result = await self._session.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = count_result.scalar_one()

        offset = (page - 1) * page_size
        result = await self._session.execute(
            base.order_by(TurnMetricsRow.created_at.desc())
            .limit(page_size)
            .offset(offset)
        )
        return result.all(), total

    async def aggregate_audit_drops_for_window(self, *, since: datetime) -> int:
        """Sum of per-turn audit write degradations since a cutoff."""
        stmt = select(func.coalesce(func.sum(TurnMetricsRow.audit_drops), 0)).where(
            TurnMetricsRow.created_at >= since
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def count_distinct_users_for_window(self, *, since: datetime) -> int:
        """Distinct accounts that completed ≥1 turn since a cutoff (活跃用户).

        The 概览 dashboard's「今日活跃」metric — ``COUNT(DISTINCT user_id)`` over
        ``turn_metrics`` in the window (a user who took a turn is "active"). Filters
        on ``ix_turn_metrics_created``.
        """
        stmt = select(func.count(distinct(TurnMetricsRow.user_id))).where(
            TurnMetricsRow.created_at >= since
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)
