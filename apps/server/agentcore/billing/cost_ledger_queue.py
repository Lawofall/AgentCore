"""At-least-once durable queue for ``cost_calls`` + ``cost_events`` ledger writes.

Shared by inference ``proxy_spend`` (per-call + materialize per-run), cloud
in-process call metering (also materializes per-run), main-turn finalize /
handoff sync write failure, etc. A background drain writes via the **telemetry**
pool so spend never contends with content-write connections (as-built: 成本配额 §三).

Medium (G5 mid-term): Postgres table ``cost_ledger_outbox``. Drain claims with
``FOR UPDATE SKIP LOCKED`` (locks held until sink write + ack commit) so every
API process can self-drain. Idempotency stays on the sink (``call_id`` /
``run_id`` UNIQUE).

Legacy disk files under ``{data_dir}/telemetry/cost_ledger_queue/`` and
``proxy_spend_queue/`` are still drained once (upgrade compat). Enqueue DB
failure falls back to that disk path so billing intent is never silent-dropped.

Deployment: shared DB outbox + ``RATE_LIMIT_BACKEND=redis`` unlocks multi-worker
API. Other subsystems (approval gate / IM / steer / journal settlement) may still
be single-process — see 部署拓扑 · API 进程约束.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agentcore.config import settings
from agentcore.core.log_context import log_context
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id

logger = get_logger(__name__)

_QUEUE_SUBDIR = Path("telemetry") / "cost_ledger_queue"
_LEGACY_PROXY_SUBDIR = Path("telemetry") / "proxy_spend_queue"
_DRAIN_IDLE_WAIT_S = 0.05
_DRAIN_RETRY_BACKOFF_S = 0.05
_DRAIN_BATCH_SIZE = 32

# Guardrail / docs: ledger queue is the shared Postgres outbox (not process-local).
USES_SHARED_DB_OUTBOX = True


def _queue_dir() -> Path:
    return Path(settings.data_dir) / _QUEUE_SUBDIR


def _legacy_proxy_dir() -> Path:
    return Path(settings.data_dir) / _LEGACY_PROXY_SUBDIR


def _record_path(record_id: str) -> Path:
    return _queue_dir() / f"{record_id}.json"


def _run_id_from_payload(runs: list, calls: list) -> str | None:
    if runs:
        return (runs[0] or {}).get("run_id")
    if calls:
        return (calls[0] or {}).get("run_id")
    return None


def _payload_from_orm(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "conversation_id": row.conversation_id,
        "message_id": row.message_id,
        "trace_id": row.trace_id,
        "source": row.source,
        "runs": list(row.runs or []),
        "calls": list(row.calls or []),
        "materialize_runs": bool(row.materialize_runs),
        "status": row.status,
    }


class OutboxBackend(Protocol):
    """Pluggable outbox storage (DB in prod; in-memory for unit tests)."""

    async def insert(self, payload: dict[str, Any]) -> None: ...

    async def drain_batch(
        self,
        *,
        limit: int,
        write_sink: Any,
    ) -> int:
        """Claim ≤limit pending rows, write_sink each, ack successes. Returns writes."""
        ...

    async def pending_count(self) -> int: ...


class MemoryOutboxBackend:
    """Process-shared in-memory outbox (unit tests / multi fake-worker).

    Claim marks rows in-flight under an asyncio lock so concurrent drainers
    cannot take the same row (SKIP LOCKED analogue without Postgres).
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._lock = asyncio.Lock()
        self._in_flight: set[str] = set()

    async def insert(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            rid = str(payload["id"])
            if rid not in self._rows:
                self._order.append(rid)
            self._rows[rid] = {
                **payload,
                "status": "pending",
                "enqueued_at": payload.get("enqueued_at")
                or datetime.now(UTC).isoformat(),
            }

    async def drain_batch(self, *, limit: int, write_sink: Any) -> int:
        async with self._lock:
            selected: list[dict[str, Any]] = []
            for rid in list(self._order):
                if len(selected) >= limit:
                    break
                row = self._rows.get(rid)
                if row is None or row.get("status") != "pending":
                    continue
                if rid in self._in_flight:
                    continue
                self._in_flight.add(rid)
                selected.append(dict(row))

        written = 0
        for payload in selected:
            rid = str(payload["id"])
            result = "retry"
            try:
                result = await write_sink(payload)
            except Exception:  # noqa: BLE001 — leave pending; release in_flight
                logger.exception("cost.ledger_drain_failed", record_id=rid, phase="memory_sink")
                result = "retry"
            finally:
                async with self._lock:
                    self._in_flight.discard(rid)

            async with self._lock:
                if result == "ok":
                    self._rows.pop(rid, None)
                    with contextlib.suppress(ValueError):
                        self._order.remove(rid)
                    written += 1
                elif result == "corrupt":
                    row = self._rows.get(rid)
                    if row is not None:
                        row["status"] = "corrupt"
                # "retry" — leave pending for next drain
        return written

    async def pending_count(self) -> int:
        async with self._lock:
            return sum(
                1
                for rid, row in self._rows.items()
                if row.get("status") == "pending" and rid not in self._in_flight
            )


class DbOutboxBackend:
    """Postgres ``cost_ledger_outbox`` via ``telemetry_session_factory``.

    Claim holds ``FOR UPDATE SKIP LOCKED`` until the claim session commits
    (after sink writes + deletes), so peer workers skip in-flight rows.
    """

    async def insert(self, payload: dict[str, Any]) -> None:
        from agentcore.db.base import telemetry_session_factory
        from agentcore.db.models import CostLedgerOutbox

        async with telemetry_session_factory() as session:
            session.add(
                CostLedgerOutbox(
                    id=payload["id"],
                    user_id=payload["user_id"],
                    conversation_id=payload.get("conversation_id") or None,
                    message_id=payload.get("message_id"),
                    trace_id=payload.get("trace_id"),
                    source=payload.get("source") or "unknown",
                    runs=payload.get("runs") or [],
                    calls=payload.get("calls") or [],
                    materialize_runs=bool(payload.get("materialize_runs")),
                    status="pending",
                )
            )
            await session.commit()

    async def drain_batch(self, *, limit: int, write_sink: Any) -> int:
        from sqlalchemy import select

        from agentcore.db.base import telemetry_session_factory
        from agentcore.db.models import CostLedgerOutbox

        written = 0
        async with telemetry_session_factory() as claim_session:
            result = await claim_session.execute(
                select(CostLedgerOutbox)
                .where(CostLedgerOutbox.status == "pending")
                .order_by(CostLedgerOutbox.created_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            rows = list(result.scalars().all())
            for row in rows:
                payload = _payload_from_orm(row)
                outcome = await write_sink(payload)
                if outcome == "ok":
                    await claim_session.delete(row)
                    written += 1
                elif outcome == "corrupt":
                    row.status = "corrupt"
                # "retry" — leave pending; unlock on commit
            await claim_session.commit()
        return written

    async def pending_count(self) -> int:
        from sqlalchemy import func, select

        from agentcore.db.base import telemetry_session_factory
        from agentcore.db.models import CostLedgerOutbox

        async with telemetry_session_factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(CostLedgerOutbox)
                .where(CostLedgerOutbox.status == "pending")
            )
            return int(result.scalar_one() or 0)


class CostLedgerQueue:
    """Shared DB outbox + drain consumer for ledger writes.

    Sync ``enqueue_*`` schedules a durable insert on the running loop (and
    ``drain_once`` awaits in-flight inserts first so finalize reconcile sees
    this process's pending). Disk is only a failure fallback + legacy drain.
    """

    def __init__(self, *, backend: OutboxBackend | None = None) -> None:
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._backend: OutboxBackend = backend or DbOutboxBackend()
        self._pending_enqueues: set[asyncio.Task[None]] = set()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def uses_shared_db_outbox(self) -> bool:
        return USES_SHARED_DB_OUTBOX

    def enqueue_runs(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        runs: list[dict[str, Any]],
        message_id: str | None = None,
        trace_id: str | None = None,
        source: str = "turn",
    ) -> str | None:
        """Persist priced run aggregates and wake the drain. Returns record id."""
        return self._enqueue(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            trace_id=trace_id,
            source=source,
            runs=runs,
            calls=None,
            materialize_runs=False,
        )

    def enqueue_calls(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        calls: list[dict[str, Any]],
        message_id: str | None = None,
        trace_id: str | None = None,
        source: str = "proxy_spend",
        materialize_runs: bool = True,
    ) -> str | None:
        """Persist priced call details (and optionally materialize per-run aggregates)."""
        return self._enqueue(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            trace_id=trace_id,
            source=source,
            runs=None,
            calls=calls,
            materialize_runs=materialize_runs,
        )

    async def enqueue_runs_async(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        runs: list[dict[str, Any]],
        message_id: str | None = None,
        trace_id: str | None = None,
        source: str = "turn",
    ) -> str | None:
        """Await durable outbox insert (preferred from async call sites)."""
        payload = self._build_payload(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            trace_id=trace_id,
            source=source,
            runs=runs,
            calls=None,
            materialize_runs=False,
        )
        if payload is None:
            return None
        await self._persist_payload(payload)
        return str(payload["id"])

    async def enqueue_calls_async(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        calls: list[dict[str, Any]],
        message_id: str | None = None,
        trace_id: str | None = None,
        source: str = "proxy_spend",
        materialize_runs: bool = True,
    ) -> str | None:
        payload = self._build_payload(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            trace_id=trace_id,
            source=source,
            runs=None,
            calls=calls,
            materialize_runs=materialize_runs,
        )
        if payload is None:
            return None
        await self._persist_payload(payload)
        return str(payload["id"])

    def _build_payload(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        message_id: str | None,
        trace_id: str | None,
        source: str,
        runs: list[dict[str, Any]] | None,
        calls: list[dict[str, Any]] | None,
        materialize_runs: bool,
    ) -> dict[str, Any] | None:
        """Assemble one outbox payload, or ``None`` when there is nothing to bill.

        ``user_id`` is the only mandatory envelope key: an account-level call
        (AI 改写) legitimately has no conversation, and the
        ledger now carries such rows rather than dropping real spend. Empty
        strings normalise to ``NULL`` so a caller passing ``""`` cannot fail the
        UUID insert downstream.
        """
        if not user_id:
            logger.error(
                "cost.ledger_enqueue_failed",
                source=source,
                error="no_user",
            )
            return None
        if not runs and not calls:
            return None
        record_id = new_id()
        return {
            "id": record_id,
            "user_id": user_id,
            "conversation_id": conversation_id or None,
            "message_id": message_id,
            "trace_id": trace_id,
            "source": source,
            "runs": runs or [],
            "calls": calls or [],
            "materialize_runs": materialize_runs,
            "enqueued_at": datetime.now(UTC).isoformat(),
        }

    def _enqueue(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        message_id: str | None,
        trace_id: str | None,
        source: str,
        runs: list[dict[str, Any]] | None,
        calls: list[dict[str, Any]] | None,
        materialize_runs: bool,
    ) -> str | None:
        payload = self._build_payload(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            trace_id=trace_id,
            source=source,
            runs=runs,
            calls=calls,
            materialize_runs=materialize_runs,
        )
        if payload is None:
            return None

        record_id = str(payload["id"])
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if self._write_disk_fallback(payload):
                self._log_enqueued(payload, medium="disk_fallback_no_loop")
                return record_id
            logger.error(
                "cost.ledger_enqueue_failed",
                user_id=user_id,
                conversation_id=conversation_id,
                source=source,
                error="no_event_loop",
            )
            return None

        task = loop.create_task(
            self._persist_payload(payload),
            name=f"cost_ledger_enqueue:{record_id}",
        )
        self._pending_enqueues.add(task)
        task.add_done_callback(self._pending_enqueues.discard)
        return record_id

    async def _persist_payload(self, payload: dict[str, Any]) -> None:
        try:
            await self._backend.insert(payload)
        except Exception as e:
            logger.error(
                "cost.ledger_enqueue_failed",
                user_id=payload.get("user_id"),
                conversation_id=payload.get("conversation_id"),
                source=payload.get("source"),
                record_id=payload.get("id"),
                error=str(e),
            )
            if self._write_disk_fallback(payload):
                self._log_enqueued(payload, medium="disk_fallback")
                self._wake.set()
                return
            return

        self._log_enqueued(payload, medium="db_outbox")
        self._wake.set()

    def _log_enqueued(self, payload: dict[str, Any], *, medium: str) -> None:
        runs = payload.get("runs") or []
        calls = payload.get("calls") or []
        # Per-call happy path: debug so jsonl rotation keeps llm.call. Ledger
        # truth is Postgres, not this enqueue ack.
        logger.debug(
            "cost.ledger_enqueued",
            record_id=payload.get("id"),
            run_id=_run_id_from_payload(runs, calls),
            conversation_id=payload.get("conversation_id"),
            source=payload.get("source"),
            run_count=len(runs),
            call_count=len(calls),
            medium=medium,
        )

    def _write_disk_fallback(self, payload: dict[str, Any]) -> bool:
        """Best-effort local disk write when DB outbox insert fails (or no loop)."""
        record_id = str(payload["id"])
        path = _record_path(record_id)
        tmp = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError as e:
            logger.error(
                "cost.ledger_enqueue_failed",
                user_id=payload.get("user_id"),
                conversation_id=payload.get("conversation_id"),
                source=payload.get("source"),
                record_id=record_id,
                error=f"disk_fallback:{e}",
            )
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                logger.error(
                    "cost.ledger_enqueue_tmp_cleanup_failed",
                    path=str(tmp),
                )
            return False

    def start(self) -> None:
        """Start the background drain (idempotent). Call from app lifespan."""
        if self.running:
            return
        self._stopping = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("cost.ledger_drainer_no_loop")
            return
        self._task = loop.create_task(self._drain_loop(), name="cost_ledger_drain")
        self._wake.set()
        logger.debug(
            "cost.ledger_drainer_started",
            medium="db_outbox",
            legacy_disk=str(_queue_dir()),
        )

    async def stop(self) -> None:
        """Cancel the loop first, then drain remaining once (app shutdown)."""
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.drain_once()
        logger.debug("cost.ledger_drainer_stopped")

    async def _await_pending_enqueues(self) -> None:
        pending = list(self._pending_enqueues)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _pending_disk_paths(self) -> list[Path]:
        paths: list[Path] = []
        for directory in (_queue_dir(), _legacy_proxy_dir()):
            if directory.exists():
                paths.extend(sorted(directory.glob("*.json")))
        return paths

    async def drain_once(self) -> int:
        """Drain legacy disk + shared DB outbox once. Returns successful write count.

        Awaits this process's in-flight enqueue tasks first so finalize reconcile
        sees locally scheduled pending. Cross-worker visibility is the shared DB.
        """
        await self._await_pending_enqueues()
        written = 0
        for path in self._pending_disk_paths():
            if await self._drain_disk_file(path):
                written += 1

        try:
            written += await self._backend.drain_batch(
                limit=_DRAIN_BATCH_SIZE,
                write_sink=self._write_sink_outcome,
            )
        except Exception:  # noqa: BLE001 — loop/reconcile must survive
            logger.exception("cost.ledger_drain_failed", phase="batch")
        return written

    async def _drain_loop(self) -> None:
        while not self._stopping:
            try:
                written = await self.drain_once()
            except Exception:  # noqa: BLE001 — loop must survive; next wake retries
                logger.exception("cost.ledger_drain_loop_error")
                written = 0
            if written == 0:
                self._wake.clear()
                try:
                    has_disk = bool(self._pending_disk_paths())
                    has_db = (await self._backend.pending_count()) > 0
                except Exception:  # noqa: BLE001
                    has_disk, has_db = True, True
                if has_disk or has_db or self._pending_enqueues:
                    continue
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=_DRAIN_IDLE_WAIT_S)
                except TimeoutError:
                    continue
            else:
                await asyncio.sleep(0)

    def _quarantine_disk(self, path: Path) -> None:
        poison = path.with_suffix(".corrupt")
        try:
            os.replace(path, poison)
        except OSError:
            logger.error(
                "cost.ledger_corrupt_quarantine_failed",
                path=str(path),
            )

    async def _drain_disk_file(self, path: Path) -> bool:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(
                "cost.ledger_record_read_failed",
                path=str(path),
                error=str(e),
            )
            await asyncio.sleep(_DRAIN_RETRY_BACKOFF_S)
            return False

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(
                "cost.ledger_record_corrupt",
                path=str(path),
                error=str(e),
            )
            self._quarantine_disk(path)
            return False

        outcome = await self._write_sink_outcome(payload)
        if outcome == "corrupt":
            self._quarantine_disk(path)
            return False
        if outcome == "retry":
            return False
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.error(
                "cost.ledger_ack_failed",
                path=str(path),
                record_id=payload.get("id"),
                error=str(e),
            )
            return True
        logger.debug(
            "cost.ledger_drained",
            record_id=payload.get("id"),
            run_id=_run_id_from_payload(payload.get("runs") or [], payload.get("calls") or []),
            conversation_id=payload.get("conversation_id"),
            source=payload.get("source") or "unknown",
            run_count=len(payload.get("runs") or []),
            call_count=len(payload.get("calls") or []),
            medium="legacy_disk",
        )
        return True

    async def _write_sink_outcome(self, payload: dict[str, Any]) -> str:
        """Write ledger sink for one outbox payload.

        Returns ``ok`` | ``retry`` | ``corrupt``.
        """
        user_id = payload.get("user_id")
        conversation_id = payload.get("conversation_id") or None
        runs = payload.get("runs") or []
        calls = payload.get("calls") or []
        record_id = payload.get("id")
        # ``conversation_id`` may legitimately be NULL (account-level spend); only a
        # missing owner or an empty batch makes a record unwritable.
        if not user_id or (not runs and not calls):
            logger.error(
                "cost.ledger_record_invalid",
                record_id=record_id,
            )
            return "corrupt"

        trace_id = payload.get("trace_id")
        message_id = payload.get("message_id")
        source = payload.get("source") or "unknown"
        materialize_runs = bool(payload.get("materialize_runs"))
        with log_context(trace_id=trace_id, conversation_id=conversation_id):
            try:
                from agentcore.db.base import telemetry_session_factory
                from agentcore.db.repositories import CostEventRepository

                async with telemetry_session_factory() as session:
                    repo = CostEventRepository(session)
                    if calls:
                        await repo.record_calls(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            message_id=message_id,
                            calls=calls,
                            trace_id=trace_id,
                            materialize_runs=materialize_runs,
                        )
                    if runs:
                        await repo.record_runs(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            message_id=message_id,
                            runs=runs,
                            trace_id=trace_id,
                        )
            except Exception as e:
                logger.warning(
                    "cost.ledger_drain_failed",
                    user_id=user_id,
                    conversation_id=conversation_id,
                    record_id=record_id,
                    run_id=_run_id_from_payload(runs, calls),
                    source=source,
                    error=str(e),
                )
                await asyncio.sleep(_DRAIN_RETRY_BACKOFF_S)
                return "retry"

        logger.debug(
            "cost.ledger_drained",
            record_id=record_id,
            run_id=_run_id_from_payload(runs, calls),
            conversation_id=conversation_id,
            source=source,
            run_count=len(runs),
            call_count=len(calls),
            medium="db_outbox",
        )
        return "ok"


_default_queue: CostLedgerQueue | None = None


def get_cost_ledger_queue() -> CostLedgerQueue:
    """Process-wide queue singleton (created lazily)."""
    global _default_queue
    if _default_queue is None:
        _default_queue = CostLedgerQueue()
    return _default_queue


def reset_cost_ledger_queue_for_tests(
    *,
    backend: OutboxBackend | None = None,
) -> CostLedgerQueue:
    """Replace the singleton (tests only). Defaults to in-memory outbox."""
    global _default_queue
    _default_queue = CostLedgerQueue(backend=backend or MemoryOutboxBackend())
    return _default_queue
