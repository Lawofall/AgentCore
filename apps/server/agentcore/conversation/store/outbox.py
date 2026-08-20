"""Sidecar OutboxStore — progressive local turn persistence (as-built: 双模式工作区 §10.3).

Each method serializes into a per-turn outbox record under ``<dataDir>/outbox/``
(sibling of ``paused/``). The Electron main-process writebacker drains ``ready``
records via ``POST .../local-turns`` → ``CloudStore.finalize(mode="local")``.

Record lifecycle: ``open`` (begin + journal + stream_segments) → ``ready``
(finalize / salvage) → deleted after cloud ack. Resume unseals a leftover
``ready`` record (``reopen_for_resume``) so the same turn can rewrite its
journal. Idempotent: begin is create-once; journal appends dedupe on ``seq``;
finalize is once per seal (resume unseals first). Mid-turn prose durability is
``upsert_stream_segments`` → ``turn_stream_state`` (not ``messages.content``).
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from agentcore.conversation.store.merge import (
    MESSAGE_STATUS_COMPLETE,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_INCOMPLETE,
    MESSAGE_STATUS_RUNNING,
    pick_merged_content,
    pick_monotonic_content,
)
from agentcore.core.logging import get_logger
from agentcore.db.repositories.stream_state import resolve_stream_upsert

logger = get_logger(__name__)

SCHEMA_VERSION = 1
PHASE_OPEN = "open"
PHASE_READY = "ready"

# Mirror StreamCheckpointer channel ids (avoid store → runtime import).
_CHANNEL_CAPTAIN_CONTENT = "captain:content"
_CHANNEL_CAPTAIN_REASONING = "captain:reasoning"


def _is_safe_id(value: str) -> bool:
    if not value or ".." in value:
        return False
    return "/" not in value and "\\" not in value


def _is_transient_replace_error(exc: BaseException) -> bool:
    """Windows file-lock noise on ``os.replace`` (AV / writebacker holding ``.json``).

    WinError 5 (ACCESS_DENIED) and 32 (SHARING_VIOLATION) are the observed forms;
    POSIX cousins ``EACCES`` / ``EPERM`` / ``EBUSY`` get the same treatment.
    """
    if not isinstance(exc, OSError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror in (5, 32):
        return True
    if isinstance(exc, PermissionError):
        return True
    transient_errnos = {errno.EACCES, errno.EPERM}
    if hasattr(errno, "EBUSY"):
        transient_errnos.add(errno.EBUSY)
    return getattr(exc, "errno", None) in transient_errnos


# Backoff between replace attempts when the destination is briefly locked.
_REPLACE_RETRY_DELAYS_S = (0.0, 0.05, 0.15, 0.35, 0.75)

# Short retry when an existing outbox file cannot be read/parsed (AV / torn write).
_READ_RETRY_DELAYS_S = (0.0, 0.05, 0.15)


class OutboxReadError(OSError):
    """Outbox file exists but could not be read or parsed.

    Must not be collapsed into ``None`` (missing): treating corrupt/locked reads as
    missing caused ``_mutate_sync`` to invent an empty shell and silently wipe the
    on-disk record.
    """


def _replace_with_retry(tmp: Path, target: Path) -> None:
    """``os.replace`` with limited retry for transient Windows locks; re-raises if exhausted."""
    last: OSError | None = None
    for attempt, delay in enumerate(_REPLACE_RETRY_DELAYS_S):
        if delay:
            time.sleep(delay)
        try:
            os.replace(tmp, target)
            if attempt > 0:
                logger.warning(
                    "sidecar.outbox_replace_recovered",
                    target=str(target),
                    attempts=attempt + 1,
                )
            return
        except OSError as e:
            if not _is_transient_replace_error(e):
                raise
            last = e
            logger.warning(
                "sidecar.outbox_replace_retry",
                target=str(target),
                attempt=attempt + 1,
                max_attempts=len(_REPLACE_RETRY_DELAYS_S),
                error=str(e),
            )
    assert last is not None
    raise last


class OutboxStore:
    """ConversationStore that appends progressive outbox records to local disk.

    Does **not** talk to Postgres or the cloud API — durability is the file; the
    main-process writebacker is the sole cloud delivery path.
    """

    def __init__(self, base: Path) -> None:
        self._base = base
        # Per-turn contexts keyed by assistant ``message_id`` (turn id). Concurrent
        # local turns must not share a single slot — overwriting used to make
        # salvage fall back to message_id as the file key and seal empty
        # user_message dead letters that never write back.
        self._contexts: dict[str, dict[str, Any]] = {}
        # user_message_id → asyncio.Lock for serialized read-modify-write.
        self._locks: dict[str, asyncio.Lock] = {}

    def bind_turn(
        self,
        *,
        conversation_id: str,
        user_message_id: str,
        user_message: str,
        message_id: str,
        trace_id: str,
        origin: str | None = None,
        execution_id: str | None = None,
        harvest_kind: str | None = None,
        agent_mentions: list[dict] | None = None,
    ) -> None:
        """Pin one turn's idempotency keys before begin_turn / pipeline.

        Contexts are isolated by ``message_id`` so overlapping turns (pause →
        continue alongside another live turn, or concurrent stops) do not steal
        each other's ``user_message_id`` / ``user_message``.
        """
        ctx: dict[str, Any] = {
            "conversation_id": conversation_id,
            "user_message_id": user_message_id,
            "user_message": user_message,
            "message_id": message_id,
            "trace_id": trace_id,
        }
        for key, val in (
            ("origin", origin),
            ("execution_id", execution_id),
            ("harvest_kind", harvest_kind),
        ):
            if isinstance(val, str) and val.strip():
                ctx[key] = val.strip()
        if agent_mentions:
            from agentcore.conversation.mentions import to_stored_agent_mentions

            stored = to_stored_agent_mentions(agent_mentions)
            if stored:
                ctx["agent_mentions"] = stored
        self._contexts[message_id] = ctx

    def clear_turn(self, message_id: str | None = None) -> None:
        """Drop a bound turn context. ``message_id=None`` clears all (tests only)."""
        if message_id is None:
            self._contexts.clear()
            return
        self._contexts.pop(message_id, None)

    def _ctx_for(self, message_id: str | None) -> dict[str, Any]:
        mid = str(message_id or "")
        if not mid:
            return {}
        return self._contexts.get(mid) or {}

    def _resolve_user_message_id(self, message_id: str | None) -> str | None:
        """Map assistant turn id → outbox file key (``user_message_id``).

        Prefer the in-memory bind; fall back to an on-disk record that already
        carries this ``message_id``. Never treat the assistant id itself as the
        file key — that path produced empty-``user_message`` ready dead letters.
        """
        mid = str(message_id or "")
        if not mid:
            return None
        ctx = self._ctx_for(mid)
        umid = str(ctx.get("user_message_id") or "")
        if umid and _is_safe_id(umid):
            return umid
        existing = self._find_umid_record_for_message(mid)
        if existing is None:
            return None
        umid = str(existing.get("user_message_id") or "")
        if umid and _is_safe_id(umid) and umid != mid:
            return umid
        # Allow resume-{turn_id} and other intentional umids that equal mid only
        # when the record already has a non-empty user_message (not a dead letter).
        if umid and _is_safe_id(umid) and (existing.get("user_message") or "").strip():
            return umid
        return None

    def _find_umid_record_for_message(self, message_id: str) -> dict[str, Any] | None:
        """Prefer the real umid-keyed open/ready row over an assistant-id dead letter."""
        if not message_id:
            return None
        matches = [
            r
            for r in list_outbox_records(self._base)
            if str(r.get("message_id") or "") == message_id
        ]
        if not matches:
            return None

        def _rank(record: dict[str, Any]) -> tuple[int, int, str]:
            umid = str(record.get("user_message_id") or "")
            has_um = 1 if (record.get("user_message") or "").strip() else 0
            # Prefer umid ≠ assistant id (true optimistic bubble key).
            distinct = 1 if umid and umid != message_id else 0
            return (distinct, has_um, umid)

        return max(matches, key=_rank)

    def _lock_for(self, user_message_id: str) -> asyncio.Lock:
        lock = self._locks.get(user_message_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_message_id] = lock
        return lock

    def _path(self, user_message_id: str) -> Path:
        return self._base / f"{user_message_id}.json"

    def _empty_record(self, *, user_message_id: str, **fields: Any) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "user_message_id": user_message_id,
            "conversation_id": fields.get("conversation_id", ""),
            "message_id": fields.get("message_id"),
            "trace_id": fields.get("trace_id", ""),
            "user_message": fields.get("user_message", ""),
            "content": "",
            "reasoning_content": None,
            "citations": [],
            "evidence_ledger": [],
            "runs": None,
            "journal": {},  # seq(str) → entry — idempotent append
            # channel → {text, generation} — StreamCheckpointer mid-stream snapshots (D6)
            "stream_segments": {},
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "rounds": 0,
            "finish_reason": None,
            "phase": PHASE_OPEN,
            "updated_at": time.time(),
            "ops": [],
        }

    def _read_sync(self, user_message_id: str) -> dict[str, Any] | None:
        """Return the on-disk record, or ``None`` only when the file is missing.

        Raises :class:`OutboxReadError` when the file exists but read/parse fails
        (including non-object JSON) so callers never invent an empty shell over it.
        """
        path = self._path(user_message_id)
        if not path.is_file():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError) as e:
            raise OutboxReadError(
                f"outbox read failed for {user_message_id}: {e}"
            ) from e
        if not isinstance(data, dict):
            raise OutboxReadError(
                f"outbox record is not a JSON object for {user_message_id}"
            )
        return data

    def _write_sync(self, user_message_id: str, record: dict[str, Any]) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        target = self._path(user_message_id)
        tmp = target.with_suffix(".json.tmp")
        journal = record.get("journal")
        if isinstance(journal, dict):
            from agentcore.runtime.journal.seq_space import stamp_missing_ords

            stamp_missing_ords(journal)
        record["updated_at"] = time.time()
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        # Electron writebacker / AV may briefly hold the destination on Windows
        # (WinError 5) — retry, then let ``_mutate`` log ``outbox_write_failed``.
        _replace_with_retry(tmp, target)

    async def _mutate(
        self,
        user_message_id: str,
        mutator: Any,
    ) -> None:
        if not _is_safe_id(user_message_id):
            return
        async with self._lock_for(user_message_id):
            try:
                await asyncio.to_thread(self._mutate_sync, user_message_id, mutator)
            except Exception as e:  # noqa: BLE001 — outbox must never break the turn
                logger.error(
                    "sidecar.outbox_write_failed",
                    user_message_id=user_message_id,
                    error=str(e),
                )

    def _mutate_sync(self, user_message_id: str, mutator: Any) -> None:
        """Read-modify-write. Empty shell only when the file is missing.

        On read/parse failure: short retry, then abort without writing (never wipe).
        """
        last_err: OutboxReadError | None = None
        for attempt, delay in enumerate(_READ_RETRY_DELAYS_S):
            if delay:
                time.sleep(delay)
            try:
                record = self._read_sync(user_message_id)
            except OutboxReadError as e:
                last_err = e
                logger.warning(
                    "sidecar.outbox_read_retry",
                    user_message_id=user_message_id,
                    attempt=attempt + 1,
                    max_attempts=len(_READ_RETRY_DELAYS_S),
                    error=str(e),
                )
                continue
            if record is None:
                record = self._empty_record(user_message_id=user_message_id)
            mutator(record)
            self._write_sync(user_message_id, record)
            if attempt > 0:
                logger.warning(
                    "sidecar.outbox_read_recovered",
                    user_message_id=user_message_id,
                    attempts=attempt + 1,
                )
            return
        assert last_err is not None
        logger.error(
            "sidecar.outbox_read_failed",
            user_message_id=user_message_id,
            attempts=len(_READ_RETRY_DELAYS_S),
            error=str(last_err),
        )
        raise last_err

    async def begin_turn(
        self,
        *,
        conversation_id: str,
        message_id: str,
        trace_id: str,
    ) -> None:
        ctx = self._ctx_for(message_id)
        user_message_id = str(ctx.get("user_message_id") or "")
        if not user_message_id or not _is_safe_id(user_message_id):
            logger.warning(
                "sidecar.outbox_begin_missing_user_message_id",
                message_id=message_id,
            )
            return
        user_message = str(ctx.get("user_message") or "")

        def mutate(record: dict[str, Any]) -> None:
            if "begin_turn" in record.get("ops", []):
                return  # idempotent
            record["conversation_id"] = conversation_id
            record["message_id"] = message_id
            record["trace_id"] = trace_id
            record["user_message_id"] = user_message_id
            if user_message:
                record["user_message"] = user_message
            mentions = ctx.get("agent_mentions")
            if isinstance(mentions, list) and mentions:
                record["agent_mentions"] = mentions
            record["phase"] = PHASE_OPEN
            record.setdefault("ops", []).append("begin_turn")

        await self._mutate(user_message_id, mutate)

    def _reopen_for_resume_sync(
        self,
        user_message_id: str,
        turn_id: str,
        conversation_id: str | None,
        trace_id: str | None,
    ) -> None:
        """Unseal READY in place. Missing file → no-op (never invent an empty shell)."""
        try:
            record = self._read_sync(user_message_id)
        except OutboxReadError:
            return
        if record is None:
            return
        if record.get("phase") != PHASE_READY:
            return
        existing_mid = str(record.get("message_id") or "")
        if existing_mid and existing_mid != turn_id:
            return
        record["phase"] = PHASE_OPEN
        if conversation_id:
            record["conversation_id"] = conversation_id
        if turn_id:
            record["message_id"] = turn_id
        if trace_id:
            record["trace_id"] = trace_id
        ops = [op for op in list(record.get("ops") or []) if op != "finalize"]
        if "reopen_for_resume" not in ops:
            ops.append("reopen_for_resume")
        record["ops"] = ops
        self._write_sync(user_message_id, record)

    async def reopen_for_resume(
        self,
        *,
        turn_id: str,
        user_message_id: str,
        conversation_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Unseal a pause-READY record so this turn can rewrite its fact stream.

        Pause ``finalize`` stamps ``PHASE_READY`` + ``ops.finalize``; ``append_journal``
        and a later complete ``finalize`` then no-op. Resume reuses the same
        ``message_id`` / umid file — drop the seal, keep the hang-frame journal.
        Missing file (writeback already deleted it) is a no-op; never creates a
        new record. Failure is logged and does not raise.
        """
        if not user_message_id or not _is_safe_id(user_message_id):
            return
        try:
            async with self._lock_for(user_message_id):
                await asyncio.to_thread(
                    self._reopen_for_resume_sync,
                    user_message_id,
                    turn_id,
                    conversation_id,
                    trace_id,
                )
        except Exception as e:  # noqa: BLE001 — outbox must never break the turn
            logger.error(
                "sidecar.outbox_write_failed",
                user_message_id=user_message_id,
                error=str(e),
            )

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
        user_message_id = self._resolve_user_message_id(turn_id)
        if not user_message_id:
            logger.warning(
                "sidecar.outbox_journal_missing_user_message_id",
                turn_id=turn_id,
            )
            return None
        allocated: list[int | None] = [None]

        def mutate(record: dict[str, Any]) -> None:
            from agentcore.runtime.journal.seq_space import (
                next_live_seq,
                next_overflow_seq,
            )
            from agentcore.runtime.journal.writer import is_seal_overflow_kind

            kind = str(entry.get("kind") or "")
            if record.get("phase") == PHASE_READY:
                # Pause finalize seals READY so a later complete finalize cannot
                # replace the snapshot. Execution terminals that arrive after
                # that seal must still append — same family as TurnJournalWriter
                # overflow; refusing them here would recreate the silent drop.
                if not overflow and not is_seal_overflow_kind(kind):
                    logger.info(
                        "sidecar.outbox_ready_skip",
                        turn_id=turn_id,
                        kind=kind,
                    )
                    return
                logger.info(
                    "sidecar.outbox_ready_overflow",
                    turn_id=turn_id,
                    kind=kind,
                )
            record["conversation_id"] = conversation_id or record.get("conversation_id")
            record["message_id"] = turn_id or record.get("message_id")
            if trace_id:
                record["trace_id"] = trace_id
            journal = record.setdefault("journal", {})
            existing_seqs = [
                int(k) for k in journal if str(k).lstrip("-").isdigit()
            ]
            # Live seq=None：live-band max+1；post-seal overflow 走 overflow band。
            if seq is None:
                if overflow or record.get("phase") == PHASE_READY:
                    key = str(next_overflow_seq(existing_seqs))
                else:
                    key = str(next_live_seq(existing_seqs))
            else:
                key = str(seq)
            if key not in journal:  # seq-idempotent
                journal[key] = entry
                allocated[0] = int(key)
            ops = record.setdefault("ops", [])
            if "journal_append" not in ops:
                ops.append("journal_append")

        await self._mutate(user_message_id, mutate)
        return allocated[0]

    async def seed_journal_entries_durable(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        trace_id: str | None,
        entries: list[dict[str, Any]],
        user_message_id: str,
    ) -> int:
        """Seed hang-frame facts at explicit seq ``0..n-1`` (idempotent). Raises on failure.

        Aligns local resume with cloud: pause frames are already durable before
        ``*_resolved`` continues from the next seq. Empty outbox + settlement-only
        prewrite would leave ``process_*`` out of the durable journal forever.
        """
        if not _is_safe_id(user_message_id):
            raise ValueError(f"unsafe outbox user_message_id: {user_message_id!r}")
        seeded = list(entries or [])
        if not seeded:
            return 0

        def mutate(record: dict[str, Any]) -> None:
            record["conversation_id"] = conversation_id or record.get("conversation_id")
            record["message_id"] = turn_id or record.get("message_id")
            if trace_id:
                record["trace_id"] = trace_id
            if not record.get("user_message_id"):
                record["user_message_id"] = user_message_id
            journal = record.setdefault("journal", {})
            for i, entry in enumerate(seeded):
                if not isinstance(entry, dict):
                    continue
                key = str(i)
                if key not in journal:  # seq-idempotent
                    journal[key] = entry
            ops = record.setdefault("ops", [])
            if "journal_append" not in ops:
                ops.append("journal_append")
            if "journal_seed" not in ops:
                ops.append("journal_seed")

        async with self._lock_for(user_message_id):
            await asyncio.to_thread(self._mutate_sync, user_message_id, mutate)
        return len(seeded)

    async def append_journal_durable(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        trace_id: str | None,
        entry: dict[str, Any],
        user_message_id: str,
    ) -> int:
        """Settlement prewrite: append one journal entry and **raise** on failure.

        Unlike :meth:`append_journal` (best-effort, never breaks the turn), D1
        settlement must be durable before the paused frame is consumed — a silent
        swallow would leave「已授权」without a journal fact.
        """
        if not _is_safe_id(user_message_id):
            raise ValueError(f"unsafe outbox user_message_id: {user_message_id!r}")
        allocated: list[int] = [-1]

        def mutate(record: dict[str, Any]) -> None:
            # Settlement may land while phase is still open; also allow a ready
            # record that is being continued (idempotent re-prewrite no-ops via seq).
            record["conversation_id"] = conversation_id or record.get("conversation_id")
            record["message_id"] = turn_id or record.get("message_id")
            if trace_id:
                record["trace_id"] = trace_id
            if not record.get("user_message_id"):
                record["user_message_id"] = user_message_id
            journal = record.setdefault("journal", {})
            from agentcore.runtime.journal.seq_space import next_live_seq, seqs_from_map

            existing = seqs_from_map(journal)
            next_seq = next_live_seq(existing)
            # Dedupe by settlement identity (kind + checkpoint_id) so retries don't fan out.
            kind = str(entry.get("kind") or "")
            payload = dict(entry.get("payload") or {})
            cid = str(payload.get("checkpoint_id") or "")
            if kind and cid:
                for existing_entry in journal.values():
                    if not isinstance(existing_entry, dict):
                        continue
                    if str(existing_entry.get("kind") or "") != kind:
                        continue
                    ep = dict(existing_entry.get("payload") or {})
                    if str(ep.get("checkpoint_id") or "") == cid:
                        allocated[0] = next(
                            (
                                int(k)
                                for k, v in journal.items()
                                if v is existing_entry and str(k).lstrip("-").isdigit()
                            ),
                            next_seq,
                        )
                        return
            key = str(next_seq)
            journal[key] = entry
            allocated[0] = next_seq
            ops = record.setdefault("ops", [])
            if "journal_append" not in ops:
                ops.append("journal_append")
            if "settlement_prewrite" not in ops:
                ops.append("settlement_prewrite")

        async with self._lock_for(user_message_id):
            await asyncio.to_thread(self._mutate_sync, user_message_id, mutate)
        if allocated[0] < 0:
            raise RuntimeError("settlement prewrite did not allocate a journal seq")
        return allocated[0]

    def find_record_by_message_id(self, message_id: str) -> dict[str, Any] | None:
        """Read-only lookup of an outbox record by assistant ``message_id``."""
        if not message_id:
            return None
        for record in list_outbox_records(self._base):
            if str(record.get("message_id") or "") == message_id:
                return record
        return None

    async def finalize(
        self,
        *,
        mode: Literal["cloud", "local"] = "local",
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        del mode  # outbox only ever stages local write-back
        user_message_id = str(kwargs.get("user_message_id") or "")
        message_id = kwargs.get("message_id")
        if not user_message_id:
            user_message_id = self._resolve_user_message_id(
                str(message_id) if message_id else None
            ) or ""
        if not user_message_id:
            logger.warning("sidecar.outbox_finalize_missing_user_message_id")
            return None

        def mutate(record: dict[str, Any]) -> None:
            if record.get("phase") == PHASE_READY and "finalize" in record.get("ops", []):
                return  # already sealed — idempotent
            record["conversation_id"] = kwargs.get("conversation_id") or record.get(
                "conversation_id"
            )
            # Three-state user_message: absent → keep; explicit None → keep;
            # explicit "" → clear (do not use ``or``, which swallows empty strings).
            if "user_message" in kwargs and kwargs["user_message"] is not None:
                record["user_message"] = kwargs["user_message"]
            record["user_message_id"] = user_message_id
            record["message_id"] = kwargs.get("message_id") or record.get("message_id")
            record["trace_id"] = kwargs.get("trace_id") or record.get("trace_id")
            content = kwargs.get("assistant_content")
            if content is not None:
                finish = kwargs.get("finish_reason")
                if finish == "cancelled":
                    status = MESSAGE_STATUS_INCOMPLETE
                elif finish == "error":
                    status = MESSAGE_STATUS_FAILED
                elif finish == "paused":
                    status = MESSAGE_STATUS_RUNNING
                else:
                    # Happy-path / missing finish_reason: treat as complete delivery.
                    status = MESSAGE_STATUS_COMPLETE
                record["content"] = pick_merged_content(
                    record.get("content"),
                    content,
                    incoming_status=status,
                )
            if "assistant_reasoning" in kwargs:
                record["reasoning_content"] = kwargs.get("assistant_reasoning")
            if kwargs.get("citations") is not None:
                record["citations"] = list(kwargs["citations"] or [])
            if kwargs.get("evidence_ledger") is not None:
                record["evidence_ledger"] = list(kwargs["evidence_ledger"] or [])
            if kwargs.get("runs") is not None:
                record["runs"] = kwargs["runs"]
            # Prefer complete result journal over progressive mid-run map: cloud
            # local-turns persists ``journal`` first, so an incomplete progressive
            # map would otherwise eclipse full ``runs`` (CEO process_* lost).
            journal_entries = kwargs.get("journal_entries")
            if isinstance(journal_entries, list):
                from agentcore.runtime.journal.seq_space import replace_prefix_map

                record["journal"] = replace_prefix_map(
                    journal_entries, record.get("journal")
                )
            for key in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "cache_hit_tokens",
                "cache_miss_tokens",
                "rounds",
            ):
                if key in kwargs and kwargs[key] is not None:
                    record[key] = int(kwargs[key] or 0)
            if kwargs.get("finish_reason") is not None:
                record["finish_reason"] = kwargs["finish_reason"]
            for key in ("origin", "execution_id", "harvest_kind"):
                val = kwargs.get(key)
                if isinstance(val, str) and val.strip():
                    record[key] = val.strip()
            mentions = kwargs.get("agent_mentions")
            if mentions is not None:
                from agentcore.conversation.mentions import to_stored_agent_mentions

                stored = to_stored_agent_mentions(
                    mentions if isinstance(mentions, list) else None
                )
                if stored:
                    record["agent_mentions"] = stored
            record["phase"] = PHASE_READY
            ops = record.setdefault("ops", [])
            if "finalize" not in ops:
                ops.append("finalize")

        await self._mutate(user_message_id, mutate)
        # Local ack shape (cloud ids arrive after main-process writeback).
        return {
            "user_message_id": user_message_id,
            "assistant_message_id": kwargs.get("message_id"),
            "title": None,
        }

    async def salvage(
        self,
        *,
        journal: list[dict[str, Any]],
        content: str,
        conversation_id: str,
        trace_id: str,
        message_id: str | None,
        origin: str | None = None,
        execution_id: str | None = None,
        harvest_kind: str | None = None,
        interrupt_reason: str | None = None,
    ) -> None:
        """Seal an open umid-keyed record as cancelled+ready (stop / crash).

        Merges onto the existing ``user_message_id`` file only. Refuses to create
        a new ready dead letter keyed by the assistant ``message_id`` with an
        empty ``user_message`` (that broke local→cloud journal write-back).
        """
        user_message_id = self._resolve_user_message_id(message_id)
        if not user_message_id:
            logger.warning(
                "sidecar.outbox_salvage_missing_user_message_id",
                message_id=message_id,
            )
            return

        ctx = self._ctx_for(message_id)
        bound_user_message = str(ctx.get("user_message") or "")
        try:
            existing = self._read_sync(user_message_id)
        except OutboxReadError as e:
            logger.error(
                "sidecar.outbox_read_failed",
                user_message_id=user_message_id,
                message_id=message_id,
                error=str(e),
            )
            return
        prior_um = (
            str(existing.get("user_message") or "").strip() if existing else ""
        )
        if existing is None and not bound_user_message:
            logger.warning(
                "sidecar.outbox_salvage_refused_empty_dead_letter",
                user_message_id=user_message_id,
                message_id=message_id,
            )
            return
        if (
            user_message_id == str(message_id or "")
            and not prior_um
            and not bound_user_message
        ):
            logger.warning(
                "sidecar.outbox_salvage_refused_empty_dead_letter",
                user_message_id=user_message_id,
                message_id=message_id,
            )
            return

        def mutate(record: dict[str, Any]) -> None:
            if record.get("phase") == PHASE_READY:
                return
            record["conversation_id"] = conversation_id or record.get("conversation_id")
            record["message_id"] = message_id or record.get("message_id")
            record["trace_id"] = trace_id or record.get("trace_id")
            record["user_message_id"] = user_message_id
            if bound_user_message:
                record["user_message"] = bound_user_message
            from agentcore.runtime.turn.interrupt import (
                compose_interrupt_body,
                normalize_interrupt_reason,
            )

            merged = pick_monotonic_content(record.get("content"), content)
            record["content"] = compose_interrupt_body(
                merged or "",
                reason=normalize_interrupt_reason(interrupt_reason or ""),
            )
            journal_map = record.setdefault("journal", {})
            for i, entry in enumerate(journal or []):
                # Salvage journal may lack seq — use enumerate offset past existing keys.
                key = str(entry.get("seq", i))
                if key not in journal_map:
                    journal_map[key] = entry
            # User stop / cancel = normal incomplete end: seal cancelled + READY so
            # writeback can project the produced journal. No frameless retain-open.
            record["finish_reason"] = "cancelled"
            record["phase"] = PHASE_READY
            for key, val in (
                ("origin", origin if origin is not None else ctx.get("origin")),
                (
                    "execution_id",
                    execution_id if execution_id is not None else ctx.get("execution_id"),
                ),
                (
                    "harvest_kind",
                    harvest_kind if harvest_kind is not None else ctx.get("harvest_kind"),
                ),
            ):
                if isinstance(val, str) and val.strip():
                    record[key] = val.strip()
            ops = record.setdefault("ops", [])
            if "salvage" not in ops:
                ops.append("salvage")

        await self._mutate(user_message_id, mutate)

    def _user_message_id_for_turn(self, turn_id: str) -> str | None:
        """Resolve outbox file key for a stream-segment turn_id (assistant message_id)."""
        return self._resolve_user_message_id(turn_id)

    async def upsert_stream_segments(
        self,
        *,
        turn_id: str,
        segments: Sequence[tuple[str, str, int]],
    ) -> None:
        """Persist StreamCheckpointer flushes into the open outbox record (D6).

        Write cadence matches the checkpointer (3s / 4KB / semantic) — callers must
        not invoke this per delta. Read-side overlay stays out of scope
        (``list_stream_segments`` remains empty).
        """
        if not segments:
            return
        user_message_id = self._user_message_id_for_turn(turn_id)
        if not user_message_id:
            return

        def mutate(record: dict[str, Any]) -> None:
            if record.get("phase") == PHASE_READY:
                return
            if turn_id and not record.get("message_id"):
                record["message_id"] = turn_id
            segs: dict[str, Any] = record.setdefault("stream_segments", {})
            if not isinstance(segs, dict):
                segs = {}
                record["stream_segments"] = segs
            for channel, text, generation in segments:
                if not channel:
                    continue
                existing = segs.get(channel) if isinstance(segs.get(channel), dict) else {}
                resolved = resolve_stream_upsert(
                    existing_text=existing.get("text") if existing else None,
                    existing_generation=(
                        int(existing["generation"])
                        if existing and existing.get("generation") is not None
                        else None
                    ),
                    incoming_text=text if isinstance(text, str) else str(text or ""),
                    incoming_generation=int(generation or 0),
                )
                if resolved is None:
                    continue
                new_text, new_gen = resolved
                segs[channel] = {"text": new_text, "generation": new_gen}
            ops = record.setdefault("ops", [])
            if "stream_segments" not in ops:
                ops.append("stream_segments")

        await self._mutate(user_message_id, mutate)

    async def list_stream_segments(
        self,
        *,
        turn_id: str,
    ) -> list[dict[str, Any]]:
        # Local mid-stream overlay is out of scope — desktop salvage reads the
        # outbox JSON directly; cloud overlay stays on CloudStore.
        del turn_id
        return []

    async def list_stream_segments_map(
        self,
        *,
        turn_ids: Sequence[str],
    ) -> dict[str, list[dict[str, Any]]]:
        del turn_ids
        return {}

    async def clear_stream_segments(
        self,
        *,
        turn_id: str,
    ) -> None:
        user_message_id = self._user_message_id_for_turn(turn_id)
        if not user_message_id:
            return

        def mutate(record: dict[str, Any]) -> None:
            record["stream_segments"] = {}

        await self._mutate(user_message_id, mutate)

    async def discard(self, user_message_id: str) -> None:
        """Drop the outbox file so write-back cannot resurrect this turn."""
        if not _is_safe_id(user_message_id):
            return
        async with self._lock_for(user_message_id):
            await asyncio.to_thread(delete_outbox_record, self._base, user_message_id)


def captain_text_from_stream_segments(
    stream_segments: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """Extract captain content / reasoning snapshots from an outbox ``stream_segments`` map."""
    if not stream_segments or not isinstance(stream_segments, dict):
        return "", None
    content = ""
    reasoning: str | None = None
    content_entry = stream_segments.get(_CHANNEL_CAPTAIN_CONTENT)
    if isinstance(content_entry, dict):
        content = str(content_entry.get("text") or "")
    reasoning_entry = stream_segments.get(_CHANNEL_CAPTAIN_REASONING)
    if isinstance(reasoning_entry, dict):
        text = str(reasoning_entry.get("text") or "")
        reasoning = text if text else None
    return content, reasoning


def journal_entries_from_map(journal: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """Sort an outbox ``journal`` map into a list in emission order."""
    if not journal:
        return None
    from agentcore.runtime.journal.seq_space import (
        map_values_in_emission_order,
        strip_entry_ord,
    )

    entries = [strip_entry_ord(item) for item in map_values_in_emission_order(journal)]
    return entries or None


def tool_failures_from_journal(
    entries: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Project failed tool facts into ``RecordTurnRequest.tool_failures``.

    Prefers execution ``tool_call`` facts (``success=false``). When none exist,
    falls back to display ``tool_use_end`` with ``status=error`` (legacy/crash
    salvage journals). Codes are coarse-mapped from the error text.
    """
    from agentcore.api.schemas.messages import (
        normalize_local_turn_tool_failure_code,
        truncate_tool_failure_message,
    )

    if not entries:
        return []

    def _row(tool: str, message: str, *, code: str | None = None) -> dict[str, str] | None:
        name = (tool or "").strip()
        if not name:
            return None
        msg = truncate_tool_failure_message(message)
        return {
            "tool": name[:128],
            "code": normalize_local_turn_tool_failure_code(msg, code=code),
            "message": msg,
        }

    from_facts: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if (entry.get("kind") or "") != "tool_call":
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("success", True):
            continue
        raw_code = payload.get("code")
        code = str(raw_code).strip() if isinstance(raw_code, str) else None
        row = _row(
            str(payload.get("name") or ""),
            str(payload.get("result") or ""),
            code=code or None,
        )
        if row:
            from_facts.append(row)
    if from_facts:
        return from_facts

    from_ends: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if (entry.get("kind") or "") != "tool_use_end":
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("status", "success") == "success":
            continue
        raw_code = payload.get("code")
        code = str(raw_code).strip() if isinstance(raw_code, str) else None
        row = _row(
            str(payload.get("tool_name") or ""),
            str(payload.get("result") or ""),
            code=code or None,
        )
        if row:
            from_ends.append(row)
    return from_ends


def list_outbox_records(base: Path) -> list[dict[str, Any]]:
    """Read all outbox JSON records (best-effort; skips torn files)."""
    if not base.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("user_message_id"):
            records.append(data)
    return records


def delete_outbox_record(base: Path, user_message_id: str) -> None:
    """Drop a synced outbox file (best-effort)."""
    if not _is_safe_id(user_message_id):
        return
    path = base / f"{user_message_id}.json"
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(
            "sidecar.outbox_delete_failed",
            user_message_id=user_message_id,
            error=str(e),
        )


def to_record_turn_body(record: dict[str, Any]) -> dict[str, Any]:
    """Project an outbox record into the ``RecordTurnRequest`` wire shape."""
    from agentcore.conversation.store.cloud import LOCAL_TURN_RECOVERY_PLACEHOLDER

    raw_um = str(record.get("user_message") or "")
    user_message = (
        "" if raw_um.strip() == LOCAL_TURN_RECOVERY_PLACEHOLDER else raw_um
    )
    body: dict[str, Any] = {
        "user_message": user_message,
        "user_message_id": record["user_message_id"],
        "content": record.get("content") or "",
        "reasoning_content": record.get("reasoning_content"),
        "citations": record.get("citations") or [],
        "evidence_ledger": record.get("evidence_ledger") or [],
        "runs": record.get("runs"),
        "message_id": record.get("message_id"),
        "input_tokens": int(record.get("input_tokens") or 0),
        "output_tokens": int(record.get("output_tokens") or 0),
        "reasoning_tokens": int(record.get("reasoning_tokens") or 0),
        "cache_hit_tokens": int(record.get("cache_hit_tokens") or 0),
        "cache_miss_tokens": int(record.get("cache_miss_tokens") or 0),
        "rounds": int(record.get("rounds") or 0),
        "trace_id": record.get("trace_id") or "",
        "finish_reason": record.get("finish_reason"),
    }
    for key in ("origin", "execution_id", "harvest_kind"):
        val = record.get(key)
        if isinstance(val, str) and val.strip():
            body[key] = val.strip()
    journal = journal_entries_from_map(record.get("journal"))
    if journal is not None:
        body["journal"] = journal
    failures = tool_failures_from_journal(journal)
    if failures:
        body["tool_failures"] = failures
    mentions = record.get("agent_mentions")
    if isinstance(mentions, list) and mentions:
        from agentcore.conversation.mentions import to_stored_agent_mentions

        stored = to_stored_agent_mentions(mentions)
        if stored:
            body["agent_mentions"] = stored
    return body
