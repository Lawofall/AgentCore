"""Write-through turn journal persistence (append-on-emit)."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from contextvars import ContextVar
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.journal.pending_interactions import settlement_dedupe_key

logger = get_logger(__name__)

# Post-pause facts that belong to a background execution, not the frozen pause
# snapshot. ``seal()`` must keep the pause stream frozen (trailing ``*_required``
# / suspending ``tool_use_end`` already sit in the snapshot); these terminals
# still have to land or the collab graph freezes on ``running``.
SEAL_OVERFLOW_KINDS = frozenset(
    {
        "run_completed",
        "run_failed",
        "run_cancelled",
        "run_skipped",
        "execution_detached",
        "execution_completed",
    }
)


def is_seal_overflow_kind(kind: str) -> bool:
    """True when ``kind`` must survive ``seal()`` via the overflow writer."""
    return kind in SEAL_OVERFLOW_KINDS


# Bound for the duration of a turn (fresh or resumed). When set, every
# :func:`~agentcore.runtime.facts.record_turn_fact` schedules a durable append
# before the matching SSE event is delivered.
current_journal_writer: ContextVar[TurnJournalWriter | None] = ContextVar(
    "current_journal_writer", default=None
)

# Buffer item: (seq_hint | None, entry, future, critical)
# seq_hint is unused for live cloud (DB allocates); kept for outbox merge callers
# that still pass explicit seq via schedule_append_with_seq.
# Future resolves to the durable journal seq (or None on skip / best-effort failure).
_BufferItem = tuple[int | None, dict[str, Any], asyncio.Future[int | None], bool]


class TurnJournalWriter:
    """Append-on-emit journal writer for one turn.

    Facts are persisted through a SINGLE serial write-behind consumer: each scheduled
    fact is queued and drained one at a time over one DB connection, rather than fanning
    out a task-(and-connection-)per-fact. A wide parallel delegation — many workers each
    emitting facts concurrently — could otherwise storm the pool with a fact-per-connection
    burst; the prior fan-out model exhausted / leaked connections under that load (asyncpg
    ``connection_lost`` + non-checked-in-connection GC noise). Ordering, per-fact durability
    (its own commit), best-effort degradation, the post-append hook, and the per-fact Future
    the SSE barrier awaits are all preserved — only the concurrency is bounded to one in-flight
    write.

    **seq 双模式 (D7)**：live 写入传 ``seq=None``，由 DB 事务内 advisory lock + MAX+1 分配；
    merge（sidecar outbox 回写）仍可显式 seq。跨 writer 同 turn 无竞态。

    **settlement 预写 (D8)**：``schedule_settlement_append`` 插队 + 写失败传播到 Future；
    进程内 dedupe 集合跳过 awaiter 重复落库（多 worker 化之前须迁共享存储）。
    """

    def __init__(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        trace_id: str | None,
        initial_seq: int = 0,
        overflow_band: bool = False,
    ) -> None:
        self.turn_id = turn_id
        self.conversation_id = conversation_id
        self.trace_id = trace_id
        # Soft counter for resume seed / diagnostics; live DB seq is authoritative.
        self._next_seq = initial_seq
        # Post-seal overflow writer allocates in the overflow band so a later
        # prefix rewrite cannot occupy those keys.
        self._overflow_band = overflow_band
        self._degraded = False
        self._sealed = False
        # Same turn identity, never sealed by this pause — worker/execution
        # terminals that still hit the sealed ContextVar writer are forwarded here.
        self._overflow: TurnJournalWriter | None = None
        self._buffer: deque[_BufferItem] = deque()
        self._drain_task: asyncio.Task[None] | None = None
        # (turn_id, kind, id) — process-local settlement dedupe (D8).
        # NOTE: multi-worker 化之前须迁共享存储（与持久化契约方案 §六同一部署约束）。
        self._settlement_dedupe: set[tuple[str, str, str]] = set()

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def sealed(self) -> bool:
        """True after a durable pause save — pause-stream appends are refused.

        Execution terminals are forwarded to :meth:`writable` (unsealed overflow
        on the same ``turn_id``) so they cannot silently vanish.
        """
        return self._sealed

    def writable(self) -> TurnJournalWriter:
        """Writer that still accepts appends (self, or the post-seal overflow)."""
        if self._sealed:
            return self._ensure_overflow()
        return self

    @property
    def next_seq(self) -> int:
        """Next soft ``seq`` hint (resume seed); live DB seq is authoritative."""
        return self._next_seq

    def has_settlement_dedupe(self, key: tuple[str, str, str]) -> bool:
        return key in self._settlement_dedupe

    def would_dedupe_settlement(self, entry: dict[str, Any]) -> bool:
        """True when a non-critical append of ``entry`` would skip the durable write.

        Matches :meth:`_enqueue` settlement dedupe (awaiter / cold-resume re-emit).
        Callers that also maintain ``TurnFactLog`` must consult this *before*
        appending to the in-memory log — otherwise a skipped DB write still grows
        the log and drifts ``fact_log`` index vs journal ``seq`` (finalize
        enumerate then inserts duplicate trailing ``process_content``).
        """
        key = settlement_dedupe_key(
            self.turn_id, str(entry.get("kind") or ""), dict(entry.get("payload") or {})
        )
        return key is not None and key in self._settlement_dedupe

    def register_settlement_dedupe(self, entry: dict[str, Any]) -> tuple[str, str, str] | None:
        """Register a settlement key so a later duplicate journal write is skipped."""
        key = settlement_dedupe_key(
            self.turn_id, str(entry.get("kind") or ""), dict(entry.get("payload") or {})
        )
        if key is not None:
            self._settlement_dedupe.add(key)
        return key

    def schedule_append(self, entry: dict[str, Any]) -> asyncio.Future[int | None] | None:
        """Queue one fact for durable append; returns a Future completed with the seq.

        Live mode passes ``seq=None`` to the store (DB allocates). Settlement facts that
        hit the process-local dedupe set skip the journal write (SSE still emitted by
        the caller) and resolve the Future with ``None`` immediately.
        """
        return self._enqueue(entry, critical=False, front=False)

    def schedule_settlement_append(
        self, entry: dict[str, Any]
    ) -> asyncio.Future[int | None] | None:
        """Priority-enqueue a settlement fact; write failure propagates to the Future."""
        key = self.register_settlement_dedupe(entry)
        if key is not None and key in self._settlement_dedupe:
            # Already registered above; if it was ALREADY present before this call,
            # treat as idempotent success (duplicate prewrite / resolve).
            pass
        return self._enqueue(entry, critical=True, front=True)

    def _enqueue(
        self,
        entry: dict[str, Any],
        *,
        critical: bool,
        front: bool,
    ) -> asyncio.Future[int | None] | None:
        if self._sealed:
            return self._enqueue_sealed(entry, critical=critical, front=front)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._log_enqueue_refused(entry, reason="no_running_loop")
            return None

        # Dedupe: awaiter re-emit after prewrite → skip journal, resolve immediately.
        key = settlement_dedupe_key(
            self.turn_id, str(entry.get("kind") or ""), dict(entry.get("payload") or {})
        )
        if (
            not critical
            and key is not None
            and key in self._settlement_dedupe
        ):
            future: asyncio.Future[int | None] = loop.create_future()
            future.set_result(None)
            return future

        if critical and key is not None:
            self._settlement_dedupe.add(key)

        future = loop.create_future()
        item: _BufferItem = (None, entry, future, critical)
        if front:
            self._buffer.appendleft(item)
        else:
            self._buffer.append(item)
        self._next_seq += 1
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = loop.create_task(self._drain())
        return future

    async def _drain(self) -> None:
        """Serially write every queued fact, one connection at a time.

        Best-effort facts: write failure degrades the turn but still resolves the Future
        so the SSE barrier never hangs. Settlement (critical) facts: write failure
        ``set_exception`` so the resolve endpoint can return 5xx without settling the
        interaction Future. On success the Future carries the durable journal seq.
        """
        from agentcore.conversation.store import get_conversation_store

        store = get_conversation_store()
        while self._buffer:
            _seq_hint, entry, future, critical = self._buffer.popleft()
            # Re-check dedupe for non-critical (awaiter path after prewrite registered).
            key = settlement_dedupe_key(
                self.turn_id, str(entry.get("kind") or ""), dict(entry.get("payload") or {})
            )
            if (
                not critical
                and key is not None
                and key in self._settlement_dedupe
            ):
                if not future.done():
                    future.set_result(None)
                continue
            try:
                allocated = await store.append_journal(
                    turn_id=self.turn_id,
                    seq=None,
                    conversation_id=self.conversation_id,
                    trace_id=self.trace_id,
                    entry=entry,
                    overflow=self._overflow_band,
                )
                if key is not None:
                    self._settlement_dedupe.add(key)
            except Exception as e:  # noqa: BLE001
                self._degraded = True
                logger.warning(
                    "journal.append_failed",
                    turn_id=self.turn_id,
                    kind=entry.get("kind"),
                    critical=critical,
                    error=str(e),
                )
                if critical:
                    if not future.done():
                        future.set_exception(e)
                    continue
                allocated = None
            if not future.done():
                future.set_result(allocated)

    async def flush(self) -> None:
        """Wait for all queued appends to be written (turn-end / pre-pause drain)."""
        while self._drain_task is not None and not self._drain_task.done():
            with contextlib.suppress(Exception):
                await self._drain_task
        if self._buffer:
            await self._drain()
        overflow = self._overflow
        if overflow is not None and overflow is not self:
            await overflow.flush()

    async def seal(self) -> None:
        """Freeze the pause snapshot stream; keep an overflow writer for execution terminals.

        Pause snapshots must not grow trailing ``*_required`` / suspending
        ``tool_use_end`` after the durable record. Background ``run_*`` /
        ``execution_*`` terminals are not that stream — they route to an
        unsealed overflow writer on the same ``turn_id``.
        """
        if self._sealed:
            return
        await self.flush()
        self._sealed = True
        overflow = self._ensure_overflow()
        self._rebind_host_writers(overflow)
        logger.info(
            "journal.sealed_at_pause",
            turn_id=self.turn_id,
            next_seq=self._next_seq,
        )

    def _ensure_overflow(self) -> TurnJournalWriter:
        if self._overflow is None:
            self._overflow = TurnJournalWriter(
                turn_id=self.turn_id,
                conversation_id=self.conversation_id,
                trace_id=self.trace_id,
                initial_seq=self._next_seq,
                overflow_band=True,
            )
        return self._overflow

    def _enqueue_sealed(
        self,
        entry: dict[str, Any],
        *,
        critical: bool,
        front: bool,
    ) -> asyncio.Future[int | None] | None:
        kind = str(entry.get("kind") or "")
        if is_seal_overflow_kind(kind):
            overflow = self._ensure_overflow()
            logger.info(
                "journal.sealed_overflow",
                turn_id=self.turn_id,
                kind=kind,
            )
            return overflow._enqueue(entry, critical=critical, front=front)
        self._log_enqueue_refused(entry, reason="pause_snapshot")
        return None

    def _log_enqueue_refused(self, entry: dict[str, Any], *, reason: str) -> None:
        kind = str(entry.get("kind") or "")
        if is_seal_overflow_kind(kind):
            logger.error(
                "journal.sealed_drop",
                turn_id=self.turn_id,
                kind=kind,
                reason=reason,
            )
            return
        if self._sealed:
            logger.info(
                "journal.sealed_skip",
                turn_id=self.turn_id,
                kind=kind,
                reason=reason,
            )

    def _rebind_host_writers(self, overflow: TurnJournalWriter) -> None:
        """Point live coordination sessions at the unsealed overflow writer."""
        try:
            from agentcore.runtime.coordination.session import (
                rebind_host_journal_writer,
            )
        except ImportError:
            return
        rebind_host_journal_writer(self, overflow)
