"""LocalPausedTurnStore — the Sidecar's on-disk home for durably-paused turns.

The cloud persists a paused turn's frame to the ``paused_turns`` table + its
journal-so-far to ``turn_journal`` (``runtime/suspension/persistence.py``), so a
``POST .../resume`` can rebuild the turn on a fresh process. The Sidecar has **no
local DB** for the pause *frame* (双模式工作区 §十): one JSON file per checkpoint.
Settlement still drops that file, and best-effort stamps cloud
``paused_turn_outcomes`` so a later cloud ``POST .../resume`` can classify the
card as settled (sidecar never writes ``paused_turns``).

This module is the §8.6 ``Journal`` / paused-turn port's **local implementation**:
one JSON file per paused turn under a desktop-provided data dir, carrying the same
:class:`~agentcore.runtime.suspension.TurnSuspension` frame the cloud stores PLUS the
journal-so-far inline (no local ``turn_journal`` table — the file is self-contained).
The Sidecar wires :meth:`save` / :meth:`delete` as the pipeline's
``suspension_saver`` / ``suspension_deleter`` (persist before the suspend wait; drop
after a live in-process resolve), and :meth:`claim` / :meth:`list_pending` back the
``resume`` / ``listPaused`` JSON-RPC methods.

Layout is FLAT — ``<base>/<message_id>.json`` — so :meth:`delete` (which the engine
calls with only a ``message_id``) is a direct unlink; ``conversation_id`` is stored
inside and filtered in :meth:`list_pending`. A user's pending set is tiny, so the
scan is cheap. Writes are atomic (temp + ``os.replace``); a claim renames-then-reads
so a turn is never resumed twice.

D11: :meth:`save` raises on persistence failure (same posture as cloud
``save_paused_turn``) so callers never treat a failed write as ``saved=True``.
Delete / claim / list remain best-effort where noted.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from agentcore.core.logging import get_logger
from agentcore.runtime.suspension import (
    TurnSuspension,
    suspension_from_json,
    suspension_paused_summary,
)

logger = get_logger(__name__)


def _is_pg_uuid(value: str) -> bool:
    """Postgres ``paused_turn_outcomes`` keys are UUID; unit-test ids like ``m1`` are not."""
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def stamp_sidecar_paused_outcome(
    *,
    message_id: str,
    conversation_id: str,
    frame: dict[str, Any],
    decision: str,
    settled_by: str = "",
) -> None:
    """Write the cloud-readable settled conclusion for a sidecar-consumed pause.

    Best-effort: local settlement (outbox + dropping the JSON frame) must not fail
    because Postgres was unreachable. A miss degrades to today's
    ``classify_resume_miss`` regenerated 404 on a later cloud POST resume.
    """
    data = frame if isinstance(frame, dict) else {}
    checkpoint_id = str(data.get("checkpoint_id") or "")
    if not decision or not checkpoint_id:
        return
    if not _is_pg_uuid(message_id) or not _is_pg_uuid(conversation_id):
        return
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import PausedTurnRepository
        from agentcore.fulfill.origin import current_origin_device

        settler = settled_by or (current_origin_device() or "")
        async with async_session_factory() as db:
            await PausedTurnRepository(db).stamp_settled(
                message_id=message_id,
                conversation_id=conversation_id,
                frame=data,
                decision=decision,
                settled_by=settler,
            )
    except Exception as e:  # noqa: BLE001 — must never break local resume
        logger.warning(
            "sidecar.paused_outcome_stamp_failed",
            message_id=message_id,
            conversation_id=conversation_id,
            error=str(e),
        )


def _display_runs_for_pause(
    journal_entries: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Project pause-time journal into client ``runs`` for desktop reopen.

    Pin display runs on the local frame so desktop hydrate can rebuild the collab
    graph before (or without) ``local-turns`` journal writeback. Same
    ``runs_from_entries`` fold GET messages uses once the snapshot is durable.
    """
    from agentcore.runtime.journal import runs_from_entries

    runs = runs_from_entries(list(journal_entries or []))
    if not runs:
        return None
    if not runs.get("finish_reason"):
        return {**runs, "finish_reason": "paused"}
    return runs


def _is_safe_message_id(message_id: str) -> bool:
    """Reject a message_id that could escape the store dir (path traversal guard).

    Ids are engine-minted tokens (``new_id()``), so this only ever rejects garbage;
    it keeps the flat-file layout safe even if a malformed id reaches the store.
    """
    if not message_id or ".." in message_id:
        return False
    return "/" not in message_id and "\\" not in message_id


class LocalPausedTurnStore:
    """A flat-file store of durably-paused Sidecar turns (one JSON file per turn)."""

    def __init__(self, base: Path, *, outbox_base: Path | None = None) -> None:
        # ``base`` is the desktop-provided sidecar data dir (e.g.
        # ``<userData>/sidecar/paused``); created lazily on first save.
        self._base = base
        # Sibling outbox dir for D3 journal adjudication (settlement prewrite lives there).
        self._outbox_base = outbox_base if outbox_base is not None else base.parent / "outbox"
        recovered = self.recover_stale_claims()
        if recovered:
            logger.info("sidecar.paused_stale_claims_recovered", count=recovered)

    def _path(self, message_id: str) -> Path:
        return self._base / f"{message_id}.json"

    def _claimed_path(self, message_id: str) -> Path:
        return self._path(message_id).with_suffix(".json.claimed")

    def recover_stale_claims(self) -> int:
        """Adjudicate orphan ``.claimed`` files left by a crashed mid-resume (D3).

        A claim renames ``<id>.json`` → ``<id>.json.claimed``. On startup:

        - No matching settlement in the outbox journal (crash before D1 prewrite) →
          restore ``.json`` so the user can re-authorize (prior behaviour).
        - Settlement already durable (crash after prewrite, before/during confirm) →
          drop the ``.claimed`` file (idempotent consume); projection becomes
          ``interrupted_after_decision``, not a resurrected decision card.
        """
        if not self._base.is_dir():
            return 0
        from agentcore.sidecar.settlement_prewrite import outbox_has_settlement_for_frame

        recovered = 0
        for claimed in self._base.glob("*.json.claimed"):
            target = claimed.with_name(claimed.name.removesuffix(".claimed"))
            message_id = target.stem
            try:
                record = json.loads(claimed.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning(
                    "sidecar.paused_stale_claim_unreadable",
                    path=str(claimed),
                )
                continue
            frame = record.get("frame") if isinstance(record, dict) else None
            frame = frame if isinstance(frame, dict) else {}
            checkpoint_id = str(frame.get("checkpoint_id") or "")
            suspension_kind = str(frame.get("kind") or "")
            has_settlement = outbox_has_settlement_for_frame(
                self._outbox_base,
                message_id=str(record.get("message_id") or message_id),
                checkpoint_id=checkpoint_id,
                suspension_kind=suspension_kind,
            )
            try:
                if has_settlement:
                    claimed.unlink(missing_ok=True)
                    recovered += 1
                    logger.info(
                        "sidecar.paused_stale_claim_consumed",
                        message_id=message_id,
                        reason="settlement_present",
                    )
                else:
                    os.replace(claimed, target)
                    recovered += 1
                    logger.info(
                        "sidecar.paused_stale_claim_recovered",
                        message_id=message_id,
                        reason="no_settlement",
                    )
            except OSError as e:
                logger.warning(
                    "sidecar.paused_stale_claim_recover_failed",
                    path=str(claimed),
                    error=str(e),
                )
        return recovered

    # --- engine-facing closures (suspension_saver / suspension_deleter) --------

    async def save(self, suspension: TurnSuspension) -> None:
        """Persist one paused-turn frame + its journal/history-so-far (atomic).

        Upsert: re-pausing the same turn (resume → pause again) overwrites in place. The
        frame is ``TurnSuspension.to_json()`` (resume CONTROL metadata only — it omits the
        window-rebuild inputs by design, 执行级事件溯源 Phase 2 ⑤). The Sidecar has no DB, so
        this local file is its ENTIRE persistence: the cloud splits a pause across the
        ``turn_journal`` table (the fact stream) + the messages table (prior-turn history),
        but here BOTH ride inline —
          * ``journal_entries`` — the §8.3 fact stream (唯一权威载体) the resume folds via
            ``window_from_journal`` to rebuild the CEO window; the display ``journal`` resume
            seed is a DERIVED property of it (P0-B Phase 3), NOT stored — so the Sidecar seeds
            identically to the cloud claim;
          * ``history`` — the window's prior-turn prefix the resume splices ahead of the
            folded rounds (the journal stores only its length; the cloud reloads it from the
            message DB — the Sidecar from here).
        Raises on write failure (D11 — aligns with cloud ``save_paused_turn``).
        """
        if not _is_safe_message_id(suspension.message_id):
            raise ValueError(f"unsafe paused-turn message_id: {suspension.message_id!r}")
        # Local turns still bind an in-process TurnJournalWriter (append-on-emit). Flush
        # it before the file write so the inline journal_entries snapshot is complete;
        # seal after a successful write so post-save emits cannot diverge the in-proc
        # durable stream (same hard boundary as cloud save_paused_turn).
        from agentcore.runtime.journal.writer import current_journal_writer

        writer = current_journal_writer.get()
        if writer is not None:
            await writer.flush()
            if writer.degraded:
                suspension.journal_degraded = True
        record = {
            "message_id": suspension.message_id,
            "conversation_id": suspension.conversation_id,
            "user_id": suspension.user_id,
            "frame": suspension.to_json(),
            # The §8.3 fact stream (唯一权威载体) + prior-turn history — the window-rebuild inputs
            # the cloud keeps in turn_journal + the message DB. Here they ride inline since the
            # Sidecar has no DB (this file is self-contained). Resume still DERIVES its journal
            # seed from ``journal_entries`` (P0-B Phase 3). ``display_runs`` is an extra pin for
            # desktop reopen collab-graph hydrate before local-turns journal writeback lands.
            "journal_entries": list(suspension.journal_entries),
            "history": list(suspension.history),
            # The resume-card summary (the wire shape) is computed ONCE here and stored
            # verbatim, so both the ``listPaused`` RPC and the desktop's direct file
            # read return the same shape with no re-projection drift.
            "summary": paused_summary(suspension),
            "trace_id": suspension.trace_id,
            "created_at": time.time(),
            # Desktop reopen collab graph: pin display runs so recovery can
            # hydrate without waiting for local-turns journal writeback.
            "display_runs": _display_runs_for_pause(suspension.journal_entries),
        }
        try:
            await asyncio.to_thread(self._write_sync, suspension.message_id, record)
        except Exception as e:
            logger.error(
                "sidecar.paused_save_failed",
                message_id=suspension.message_id,
                conversation_id=suspension.conversation_id,
                error=str(e),
            )
            raise
        if writer is not None:
            await writer.seal()

    def _write_sync(self, message_id: str, record: dict[str, Any]) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        target = self._path(message_id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)  # atomic on the same filesystem

    async def delete(self, message_id: str) -> None:
        """Drop a paused-turn frame (a live in-process resolve / timeout settled it).

        Best-effort: a stale frame left by a failed delete is harmless — the next
        ``claim`` only resurrects a turn the user can re-decide, and a re-pause
        overwrites it. NEVER raises into the turn.
        """
        if not _is_safe_message_id(message_id):
            return
        try:
            await asyncio.to_thread(self._unlink_sync, self._path(message_id))
        except Exception as e:  # noqa: BLE001 — cleanup must never break the turn
            logger.warning("sidecar.paused_delete_failed", message_id=message_id, error=str(e))

    @staticmethod
    def _unlink_sync(path: Path) -> None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

    # --- resume / listPaused backing ------------------------------------------

    async def load(
        self, message_id: str, *, conversation_id: str | None = None
    ) -> TurnSuspension | None:
        """Read a paused turn without claiming (D8 cold peek before settlement / deferred).

        Mirrors cloud :func:`~agentcore.runtime.suspension.persistence.load_paused_turn`.
        ``None`` when absent / wrong conversation / unreadable.
        """
        if not _is_safe_message_id(message_id):
            return None
        try:
            record = await asyncio.to_thread(self._load_sync, message_id, conversation_id)
        except Exception as e:  # noqa: BLE001 — peek failure reads as "not resumable"
            logger.warning("sidecar.paused_load_failed", message_id=message_id, error=str(e))
            return None
        if record is None:
            return None
        return _suspension_from_record(record)

    def _load_sync(
        self, message_id: str, conversation_id: str | None
    ) -> dict[str, Any] | None:
        target = self._path(message_id)
        try:
            record = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        if conversation_id is not None and record.get("conversation_id") != conversation_id:
            return None
        return record

    async def claim(
        self, message_id: str, *, conversation_id: str | None = None
    ) -> TurnSuspension | None:
        """Atomically claim a paused turn for resume; ``None`` if gone.

        Renames ``<id>.json`` → ``<id>.json.claimed`` FIRST (atomic ``os.replace``) so a
        second / racing resume of the same turn gets ``None`` — a turn is never resumed
        twice. After D1, :meth:`confirm_claim` runs once settlement is durable (not when
        the whole pipeline finishes); :meth:`rollback_claim` only applies when prewrite
        fails before confirm. Pass ``conversation_id`` (the one the caller is scoped to)
        so a frame is only claimed within its conversation. The journal-so-far is
        rehydrated onto :attr:`TurnSuspension.journal` so the resume replays the
        pre-pause graph.
        """
        if not _is_safe_message_id(message_id):
            return None
        try:
            record = await asyncio.to_thread(self._claim_sync, message_id, conversation_id)
        except Exception as e:  # noqa: BLE001 — a claim failure reads as "not resumable"
            logger.warning("sidecar.paused_claim_failed", message_id=message_id, error=str(e))
            return None
        if record is None:
            return None
        return _suspension_from_record(record)

    def _claim_sync(self, message_id: str, conversation_id: str | None) -> dict[str, Any] | None:
        target = self._path(message_id)
        claimed = self._claimed_path(message_id)
        try:
            os.replace(target, claimed)  # atomic; raises if already claimed/absent
        except FileNotFoundError:
            return None
        try:
            record = json.loads(claimed.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            with contextlib.suppress(FileNotFoundError):
                claimed.unlink()  # torn / unreadable frame — drop it
            return None
        # Scope check while holding the claim: on a conversation mismatch RESTORE the
        # frame (claim nothing) rather than consume it — so a stray / cross-conversation
        # resume can't destroy a valid pause (IDOR-safe, like the cloud DELETE...WHERE).
        if not isinstance(record, dict) or (
            conversation_id is not None and record.get("conversation_id") != conversation_id
        ):
            with contextlib.suppress(OSError):
                os.replace(claimed, target)
            return None
        return record

    async def confirm_claim(self, message_id: str) -> None:
        """Drop the ``.claimed`` file after settlement is durable (D1; best-effort).

        Also stamps ``paused_turn_outcomes`` (same conclusion a cloud claim writes)
        so a later cloud ``POST .../resume`` classifies the miss as settled.
        """
        if not _is_safe_message_id(message_id):
            return
        try:
            record = await asyncio.to_thread(self._take_claimed_sync, message_id)
        except Exception as e:  # noqa: BLE001 — cleanup must never break the turn
            logger.warning(
                "sidecar.paused_confirm_claim_failed", message_id=message_id, error=str(e)
            )
            return
        if record is None:
            return
        await self._stamp_outcome_for_claimed(record)

    def _take_claimed_sync(self, message_id: str) -> dict[str, Any] | None:
        """Read then drop the ``.claimed`` file; ``None`` if already gone / unreadable."""
        claimed = self._claimed_path(message_id)
        try:
            raw = claimed.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            record = json.loads(raw)
        except (OSError, ValueError):
            self._unlink_sync(claimed)
            return None
        self._unlink_sync(claimed)
        return record if isinstance(record, dict) else None

    async def _stamp_outcome_for_claimed(self, record: dict[str, Any]) -> None:
        from agentcore.sidecar.settlement_prewrite import (
            outbox_settlement_decision_for_frame,
        )

        raw_frame = record.get("frame")
        frame: dict[str, Any] = raw_frame if isinstance(raw_frame, dict) else {}
        message_id = str(record.get("message_id") or frame.get("message_id") or "")
        conversation_id = str(
            record.get("conversation_id") or frame.get("conversation_id") or ""
        )
        checkpoint_id = str(frame.get("checkpoint_id") or "")
        kind = str(frame.get("kind") or "")
        decision = outbox_settlement_decision_for_frame(
            self._outbox_base,
            message_id=message_id,
            checkpoint_id=checkpoint_id,
            suspension_kind=kind,
        )
        await stamp_sidecar_paused_outcome(
            message_id=message_id,
            conversation_id=conversation_id,
            frame=frame,
            decision=decision,
        )

    async def rollback_claim(self, message_id: str) -> None:
        """Restore frame only when settlement was NOT durable (prewrite failure).

        After D1 confirm, pipeline failures must NOT call this — the decision is a
        fact; projection becomes interrupted_after_decision instead.
        """
        if not _is_safe_message_id(message_id):
            return
        try:
            await asyncio.to_thread(self._rollback_claim_sync, message_id)
        except Exception as e:  # noqa: BLE001 — restore must never break the caller
            logger.warning(
                "sidecar.paused_rollback_claim_failed", message_id=message_id, error=str(e)
            )

    def _rollback_claim_sync(self, message_id: str) -> None:
        claimed = self._claimed_path(message_id)
        target = self._path(message_id)
        with contextlib.suppress(FileNotFoundError):
            os.replace(claimed, target)

    async def list_pending(self, conversation_id: str) -> list[TurnSuspension]:
        """A conversation's pending paused turns (oldest first), rebuilt as suspensions.

        Read-only (does not claim). Best-effort: an unreadable store yields an empty
        list so reopening never fails on a paused-turn lookup.
        """
        records = await self._records(conversation_id)
        return [_suspension_from_record(r) for r in records]

    async def list_summaries(self, conversation_id: str) -> list[dict[str, Any]]:
        """A conversation's pending pauses as stored resume-card summaries (wire shape).

        The summary was projected at save time, so this reads it verbatim — no frame
        rebuild. Backs the ``listPaused`` RPC; the desktop also reads these files
        directly on reopen (no process spawn for a read-only list).
        """
        records = await self._records(conversation_id)
        return [r.get("summary") or {} for r in records]

    async def _records(self, conversation_id: str) -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(self._list_sync, conversation_id)
        except Exception as e:  # noqa: BLE001 — a list failure degrades to "none pending"
            logger.warning(
                "sidecar.paused_list_failed",
                conversation_id=conversation_id,
                error=str(e),
            )
            return []

    def _list_sync(self, conversation_id: str) -> list[dict[str, Any]]:
        if not self._base.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in self._base.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # skip a torn / unreadable frame rather than fail the list
            if isinstance(record, dict) and record.get("conversation_id") == conversation_id:
                records.append(record)
        records.sort(key=lambda r: r.get("created_at") or 0.0)
        return records


def _suspension_from_record(record: dict[str, Any]) -> TurnSuspension:
    """Rebuild a :class:`TurnSuspension` from a stored record (frame + inline fact stream).

    Re-hydrates the window-rebuild inputs the frame omits (Phase 2 ⑤): ``journal_entries``
    (folded by ``window_from_journal``) and ``history`` (spliced ahead of the rounds) — the
    Sidecar's local stand-ins for the cloud's ``turn_journal`` table + message DB. The display
    ``journal`` resume seed is DERIVED from ``journal_entries`` (a property, P0-B Phase 3), so it
    is neither stored nor re-hydrated here — the Sidecar seeds identically to the cloud claim.
    """
    suspension = suspension_from_json(record.get("frame") or {})
    suspension.journal_entries = list(record.get("journal_entries") or [])
    suspension.history = list(record.get("history") or [])
    return suspension


def paused_summary(suspension: TurnSuspension) -> dict[str, Any]:
    """Project a paused frame into the desktop's resume-card summary (the wire shape).

    Keys mirror the cloud ``PausedTurnSummary`` **verbatim** (snake_case) — shared
    via :func:`~agentcore.runtime.suspension.suspension_paused_summary` so cloud and
    sidecar cannot drift on kind-specific slots.
    """
    return suspension_paused_summary(suspension)
