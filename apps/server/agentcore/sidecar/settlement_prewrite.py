"""Sidecar local settlement prewrite (回合恢复状态机收口 · D1).

Mirrors cloud ``prewrite_cold_resume_settlement`` against the local OutboxStore:
hang-frame ``journal_entries`` are seeded at explicit seq first (cloud already has
them in PG), then durable ``*_resolved`` continues from the next seq **before**
the paused frame is consumed. The settlement payload may embed ``resume_frame``
as audit/control metadata (frameless continue-after-decision was abolished).
"""

from __future__ import annotations

from typing import Any

from agentcore.conversation.store.outbox import OutboxStore, journal_entries_from_map
from agentcore.runtime.settlement import cold_resume_settlement_event, entry_from_sse
from agentcore.runtime.suspension import TurnSuspension


def resume_frame_blob(
    suspension: TurnSuspension,
    *,
    user_message_id: str,
    decision: str,
    note: str,
    selected: list[str],
    excluded_run_ids: list[str] | None = None,
    write_capability_overrides: list[dict[str, str]] | None = None,
    model_overrides: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Settlement control metadata embedded alongside ``*_resolved``."""
    blob: dict[str, Any] = {
        "frame": suspension.to_json(),
        "history": list(suspension.history),
        "journal_entries": list(suspension.journal_entries),
        "user_message_id": user_message_id,
        "decision": decision,
        "note": note,
        "selected": list(selected),
    }
    if excluded_run_ids:
        blob["excluded_run_ids"] = list(excluded_run_ids)
    if write_capability_overrides:
        blob["write_capability_overrides"] = list(write_capability_overrides)
    if model_overrides:
        blob["model_overrides"] = dict(model_overrides)
    return blob


async def prewrite_sidecar_resume_settlement(
    outbox: OutboxStore,
    suspension: TurnSuspension,
    *,
    decision: str,
    note: str = "",
    selected: list[str] | None = None,
    user_message_id: str,
    trace_id: str = "",
    excluded_run_ids: list[str] | None = None,
    write_capability_overrides: list[dict[str, str]] | None = None,
    model_overrides: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Durable-write hang-frame journal then ``*_resolved`` (+ resume_frame).

    Cloud already has pause frames in PG before settlement continues at the next
    seq. Local resume often starts from an empty outbox (prior READY writeback
    deleted the file) — seed ``suspension.journal_entries`` at explicit seq
    ``0..n-1`` first so ``process_*`` survive cancel/writeback/refresh.

    ``excluded_run_ids`` / ``write_capability_overrides`` / ``model_overrides`` mirror
    cloud cold resume settlement (开工组队有限否决 + 人盖模型 → ``team_preview_resolved``).

    Raises on write failure so the caller can restore the claimed frame.
    Returns the settlement journal entry that was written (also appended onto
    ``suspension.journal_entries`` for resume-pipeline dedupe seeding).
    """
    picks = list(selected or [])
    excluded = list(excluded_run_ids or [])
    overrides = list(write_capability_overrides or [])
    models = dict(model_overrides or {})
    tid = suspension.message_id
    cid = suspension.conversation_id
    tr = trace_id or getattr(suspension, "trace_id", None)
    # Seed hang-frame facts before settlement so *_resolved does not occupy seq0
    # on an empty outbox (leaving process_* out of the durable journal forever).
    await outbox.seed_journal_entries_durable(
        turn_id=tid,
        conversation_id=cid,
        trace_id=tr,
        entries=list(suspension.journal_entries or []),
        user_message_id=user_message_id,
    )
    event = cold_resume_settlement_event(
        suspension,
        decision=decision,
        note=note,
        selected=picks,
        excluded_run_ids=excluded,
        write_capability_overrides=overrides,
        model_overrides=models,
    )
    entry = entry_from_sse(event)
    entry["payload"] = {
        **dict(entry.get("payload") or {}),
        "resume_frame": resume_frame_blob(
            suspension,
            user_message_id=user_message_id,
            decision=decision,
            note=note,
            selected=picks,
            excluded_run_ids=excluded,
            write_capability_overrides=overrides,
            model_overrides=models,
        ),
    }
    await outbox.append_journal_durable(
        turn_id=tid,
        conversation_id=cid,
        trace_id=tr,
        entry=entry,
        user_message_id=user_message_id,
    )
    suspension.journal_entries = list(suspension.journal_entries) + [entry]
    # Same conclusion a cloud claim writes: local JSON is the frame, but a later
    # cloud POST resume reads ``paused_turn_outcomes``. Best-effort inside the
    # helper (outbox settlement is already durable; a PG miss degrades to today's
    # regenerated 404 rather than blocking local resume).
    from agentcore.sidecar.paused_store import stamp_sidecar_paused_outcome

    await stamp_sidecar_paused_outcome(
        message_id=tid,
        conversation_id=cid,
        frame=suspension.to_json(),
        decision=decision,
    )
    return entry


def settlement_keys_in_entries(
    entries: list[dict[str, Any]] | None,
) -> set[tuple[str, str]]:
    """Return ``{(resolved_kind, checkpoint_id)}`` present in journal entries."""
    found: set[tuple[str, str]] = set()
    for entry in entries or []:
        kind = str(entry.get("kind") or entry.get("type") or "")
        if not kind.endswith("_resolved"):
            continue
        payload = dict(entry.get("payload") or {})
        cid = str(payload.get("checkpoint_id") or "")
        if cid:
            found.add((kind, cid))
    return found


_KIND_TO_RESOLVED = {
    "ask_user": "checkpoint_resolved",
    "plan_review": "plan_review_resolved",
    "team_preview": "team_preview_resolved",
}


def _matching_resolved_payload(
    outbox_base: Any,
    *,
    message_id: str,
    checkpoint_id: str,
    suspension_kind: str,
) -> dict[str, Any] | None:
    """Payload of the matching ``*_resolved`` journal row, if any."""
    from pathlib import Path

    from agentcore.conversation.store.outbox import list_outbox_records

    resolved_kind = _KIND_TO_RESOLVED.get(suspension_kind)
    if not resolved_kind or not checkpoint_id:
        return None
    base = Path(outbox_base)
    for record in list_outbox_records(base):
        if str(record.get("message_id") or "") != message_id:
            continue
        for entry in journal_entries_from_map(record.get("journal")) or []:
            kind = str(entry.get("kind") or entry.get("type") or "")
            payload = dict(entry.get("payload") or {})
            if kind == resolved_kind and str(payload.get("checkpoint_id") or "") == checkpoint_id:
                return payload
    return None


def outbox_settlement_decision_for_frame(
    outbox_base: Any,
    *,
    message_id: str,
    checkpoint_id: str,
    suspension_kind: str,
) -> str:
    """Decision on the matching ``*_resolved`` row, or ``""`` when none."""
    payload = _matching_resolved_payload(
        outbox_base,
        message_id=message_id,
        checkpoint_id=checkpoint_id,
        suspension_kind=suspension_kind,
    )
    if payload is None:
        return ""
    decision = str(payload.get("decision") or "")
    if decision:
        return decision
    blob = payload.get("resume_frame")
    if isinstance(blob, dict):
        return str(blob.get("decision") or "")
    return ""


def outbox_has_settlement_for_frame(
    outbox_base: Any,
    *,
    message_id: str,
    checkpoint_id: str,
    suspension_kind: str,
) -> bool:
    """True when an outbox journal already holds the matching ``*_resolved``."""
    return (
        _matching_resolved_payload(
            outbox_base,
            message_id=message_id,
            checkpoint_id=checkpoint_id,
            suspension_kind=suspension_kind,
        )
        is not None
    )
