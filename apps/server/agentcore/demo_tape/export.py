"""Tape file io + recording → tape export (dev-only).

The journal-reconstruction export layer is retired (2026-07): tapes are cut straight
from live-stream recordings (``demo_tape/recorder.py``). The recorded stream already
carries true pacing and every EPHEMERAL liveliness event (typing deltas, composing
heartbeats, tool phases) that ``turn_journal`` never stored, so the former
window-filling / re-chunking / delta-rebuilding heuristics have no object left.

On-disk tape schema (v2)::

    Single-act (stock / single ``--message-id`` export)::

        {version: 2, meta, events[{type, payload, timestamp, t_ms}]}

    Multi-act (assembled from multiple recordings)::

        {version: 2, meta, turns:[{user_prompt, events, followups?}]}

Readers always normalize in memory to a ``turns[]`` document (legacy top-level
``events`` → one act). Stock single-act files are never rewritten for this.

Legacy v1 tapes (``kind``/``ts``) are read with alias compatibility and never
rewritten for format migration. Content governance (sanitize) may rewrite event
bodies in place while keeping the on-disk dialect.

Export gates (offline, before write):

- sanitize + ingest scan (shared with recording_cut) — memory / PII residue;
- refuse unwired pause kinds (empty today — cold + hot approval are wired;
  ``--force`` escape hatch);
- assert no client-tool required kinds remain (cut-table defense-in-depth).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentcore.demo_tape.sanitize import sanitize_and_scan_events
from agentcore.demo_tape.schema import (
    CLIENT_TOOL_REQUIRED_KINDS,
    TAPE_EXCLUDED_KINDS,
    TAPE_FORMAT_VERSION,
    TAPE_UNWIRED_PAUSE_KINDS,
    event_type,
    normalize_tape_events,
)


class TapeExportRefusedError(ValueError):
    """Tape export refused by an offline gate (unwired pause / client-tool)."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__(
            "tape export refused:\n  - " + "\n  - ".join(reasons)
            + "\nPass force=True / --force to override unwired-pause gates only "
            "(client-tool + ingest scan remain hard)."
        )


def _followups_from_recording(recording: dict[str, Any]) -> list[str] | None:
    """Last non-empty ``followups_generated`` payload on the recording (if any).

    Chips do not enter the tape event stream (``TAPE_EXCLUDED_KINDS``); they may still
    ride ``meta.followups`` for old-tape fidelity. Replay ignores them (mint offline).
    """
    found: list[str] | None = None
    for segment in recording.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for ev in segment.get("events") or []:
            if not isinstance(ev, dict):
                continue
            if event_type(ev) != "followups_generated":
                continue
            raw = (ev.get("payload") or {}).get("followups")
            if isinstance(raw, list) and raw:
                found = [str(x) for x in raw if str(x).strip()]
    return found or None


def collect_export_gate_reasons(events: list[dict[str, Any]]) -> list[str]:
    """Unwired-pause + client-tool gate reasons (empty ⇒ pass).

    Unwired-pause reasons are force-overrideable; client-tool reasons are not.
    Hot-path ``approval_*`` is wired (required kept; resolved cut via
    ``PAUSE_RESOLVED_KINDS``) and is not refused here.
    """
    reasons: list[str] = []
    seen_pause: set[str] = set()
    seen_client: set[str] = set()
    for ev in events:
        if not isinstance(ev, dict):
            continue
        et = event_type(ev)
        if not et:
            continue
        if et in TAPE_UNWIRED_PAUSE_KINDS and et not in seen_pause:
            seen_pause.add(et)
            reasons.append(
                f"unwired pause kind {et!r} — not wired for tape-frame suspend; "
                "export refused (use --force to override)"
            )
        if et in CLIENT_TOOL_REQUIRED_KINDS and et not in seen_client:
            seen_client.add(et)
            reasons.append(
                f"client-tool required {et!r} — must not ship on a tape "
                "(cut-table leak; not force-overrideable)"
            )
    return reasons


def assert_export_allowed(
    events: list[dict[str, Any]], *, force: bool = False
) -> None:
    """Raise :class:`TapeExportRefusedError` when export gates fire.

    ``force`` only bypasses unwired-pause refusals. Client-tool presence always
    refuses (sanitize/scan is a separate hard gate).
    """
    reasons = collect_export_gate_reasons(events)
    if force:
        reasons = [r for r in reasons if "client-tool required" in r]
    if reasons:
        raise TapeExportRefusedError(reasons)


def build_tape_from_recording(
    recording: dict[str, Any],
    *,
    meta: dict[str, Any] | None = None,
    user_prompt: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Cut a live-stream recording into a playable tape document.

    Segments (send leg, then each resume leg) are stitched onto one global timeline
    using their wall-clock starts, so the human decision gap at a pause point stays a
    real gap on the tape — the player skips it on resume (prev_t=None → first beat
    immediate) exactly as with journal-era tapes. Event ``t_ms`` are otherwise the
    recorded wall-clock offsets: true pacing, no synthesis. Event types in
    :data:`~agentcore.demo_tape.schema.TAPE_EXCLUDED_KINDS` (turn lifecycle, recorded
    settlements, per-turn meta chrome, client-tool requests) are cut.

    After the cut: memory sanitize + ingest scan (shared with recording_cut), then
    export gates (unwired pause / client-tool assertion).

    Recorded ``followups_generated`` chips are lifted into ``meta.followups`` (still
    cut from ``events``). Caller ``meta`` may override (e.g. ``--followups``).

    Accepts legacy recording events (``kind``/``ts``) via read-time alias; output
    always uses contract fields (``type``/``timestamp``) at
    :data:`~agentcore.demo_tape.schema.TAPE_FORMAT_VERSION`.
    """
    segments = list(recording.get("segments") or [])
    events: list[dict[str, Any]] = []
    base_wall: int | None = None
    last_t = 0
    for segment in segments:
        wall_t0 = segment.get("wall_t0_ms")
        if isinstance(wall_t0, int):
            if base_wall is None:
                base_wall = wall_t0
            offset = max(0, wall_t0 - base_wall)
        else:  # no wall clock on the segment — butt it against the previous one
            offset = last_t
        for ev in segment.get("events") or []:
            if not isinstance(ev, dict):
                continue
            et = event_type(ev)
            if not et or et in TAPE_EXCLUDED_KINDS:
                continue
            t_ms = offset + int(ev.get("t_ms") or 0)
            # Monotonic safety net: wall clocks and per-segment offsets never
            # legitimately rewind; clamp any jitter so pacing math stays simple.
            t_ms = max(t_ms, last_t)
            last_t = t_ms
            ts = ev.get("timestamp") if "timestamp" in ev else None
            events.append(
                {
                    "type": et,
                    "payload": ev.get("payload") or {},
                    "timestamp": ts,
                    "t_ms": t_ms,
                }
            )

    events = sanitize_and_scan_events(events)
    assert_export_allowed(events, force=force)

    extracted_followups = _followups_from_recording(recording)
    doc_meta: dict[str, Any] = {
        "source_conversation_id": (recording.get("meta") or {}).get("conversation_id"),
        "source_message_id": (recording.get("meta") or {}).get("message_id"),
        **({"followups": extracted_followups} if extracted_followups else {}),
        **(meta or {}),
        "user_prompt": user_prompt,
        "duration_ms": int(events[-1]["t_ms"]) if events else 0,
        "event_count": len(events),
    }
    return {
        "version": TAPE_FORMAT_VERSION,
        "meta": doc_meta,
        "events": events,
    }


def write_tape(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _turn_duration_ms(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    return max(int(ev.get("t_ms") or 0) for ev in events)


def _normalize_turn(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"tape turn must be an object, got {type(raw).__name__}")
    events = raw.get("events") or []
    if not isinstance(events, list):
        raise ValueError("tape turn events must be a list")
    turn: dict[str, Any] = {
        k: v for k, v in raw.items() if k not in ("events", "user_prompt", "followups")
    }
    turn["user_prompt"] = str(raw.get("user_prompt") or "").strip()
    turn["events"] = normalize_tape_events(events)
    followups = raw.get("followups")
    if isinstance(followups, list) and followups:
        cleaned = [str(x) for x in followups if str(x).strip()]
        if cleaned:
            turn["followups"] = cleaned
    return turn


def normalize_tape_document(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a tape dict in memory to always expose ``turns[]``.

    - Multi-act disk shape (``turns``) → each turn's events alias-normalized.
    - Stock single-act (top-level ``events``) → one synthetic turn; disk untouched.
    - Catalog fields (``meta.user_prompt`` / ``turn_count`` / counts) filled when absent.

    Never mutates the caller's dict in place beyond returning a shallow-copied envelope.
    """
    meta_in = data.get("meta")
    meta: dict[str, Any] = dict(meta_in) if isinstance(meta_in, dict) else {}

    raw_turns = data.get("turns")
    if isinstance(raw_turns, list) and raw_turns:
        turns = [_normalize_turn(t) for t in raw_turns]
        if not str(meta.get("user_prompt") or "").strip():
            meta["user_prompt"] = turns[0]["user_prompt"]
        meta["turn_count"] = len(turns)
        if not isinstance(meta.get("event_count"), int):
            meta["event_count"] = sum(len(t["events"]) for t in turns)
        if not isinstance(meta.get("duration_ms"), int):
            meta["duration_ms"] = sum(_turn_duration_ms(t["events"]) for t in turns)
        out: dict[str, Any] = {**data, "meta": meta, "turns": turns}
        # Single-act ``turns`` file: mirror first act onto top-level ``events`` so
        # older readers that only look at ``events`` keep working in memory.
        if len(turns) == 1:
            out["events"] = turns[0]["events"]
            if "followups" not in meta and turns[0].get("followups"):
                meta["followups"] = list(turns[0]["followups"])
                out["meta"] = meta
        return out

    events_raw = data.get("events")
    if not isinstance(events_raw, list):
        raise ValueError("invalid tape: need non-empty turns[] or events[]")
    events = normalize_tape_events(events_raw)
    turn: dict[str, Any] = {
        "user_prompt": str(meta.get("user_prompt") or "").strip(),
        "events": events,
    }
    followups = meta.get("followups")
    if isinstance(followups, list) and followups:
        cleaned = [str(x) for x in followups if str(x).strip()]
        if cleaned:
            turn["followups"] = cleaned
    meta = {**meta, "turn_count": 1}
    if not isinstance(meta.get("event_count"), int):
        meta["event_count"] = len(events)
    if not isinstance(meta.get("duration_ms"), int):
        meta["duration_ms"] = _turn_duration_ms(events)
    return {**data, "meta": meta, "events": events, "turns": [turn]}


def tape_turns(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return act list (document should already be :func:`normalize_tape_document`)."""
    turns = document.get("turns")
    if isinstance(turns, list) and turns:
        return list(turns)
    return normalize_tape_document(document)["turns"]


def assemble_multi_turn_tape(
    turn_docs: list[dict[str, Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stitch already-cut single-act tape docs into one multi-act document.

    Each ``turn_docs`` entry is a full single-act tape (as from
    :func:`build_tape_from_recording`). Per-act cut + gates must already have passed.
    """
    if not turn_docs:
        raise ValueError("assemble_multi_turn_tape requires at least one turn")
    turns: list[dict[str, Any]] = []
    source_ids: list[str] = []
    for doc in turn_docs:
        if not isinstance(doc, dict):
            raise ValueError("each turn doc must be an object")
        t_meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        events = doc.get("events")
        if not isinstance(events, list):
            # Allow passing an already-normalized single-act doc.
            norm = normalize_tape_document(doc)
            act = tape_turns(norm)[0]
            turns.append(
                {
                    "user_prompt": act["user_prompt"],
                    "events": list(act["events"]),
                    **(
                        {"followups": list(act["followups"])}
                        if act.get("followups")
                        else {}
                    ),
                }
            )
            mid = (norm.get("meta") or {}).get("source_message_id")
            if mid:
                source_ids.append(str(mid))
            continue
        turn: dict[str, Any] = {
            "user_prompt": str(t_meta.get("user_prompt") or "").strip(),
            "events": list(events),
        }
        fus = t_meta.get("followups")
        if isinstance(fus, list) and fus:
            cleaned = [str(x) for x in fus if str(x).strip()]
            if cleaned:
                turn["followups"] = cleaned
        turns.append(turn)
        mid = t_meta.get("source_message_id")
        if mid:
            source_ids.append(str(mid))

    total_events = sum(len(t["events"]) for t in turns)
    total_dur = sum(_turn_duration_ms(t["events"]) for t in turns)
    doc_meta: dict[str, Any] = {
        **(meta or {}),
        "user_prompt": turns[0]["user_prompt"],
        "duration_ms": total_dur,
        "event_count": total_events,
        "turn_count": len(turns),
    }
    if source_ids:
        doc_meta["source_message_ids"] = source_ids
    return {
        "version": TAPE_FORMAT_VERSION,
        "meta": doc_meta,
        "turns": turns,
    }


def load_tape(path: Path) -> dict[str, Any]:
    """Load a tape; normalize events + acts in memory.

    Does not rewrite the on-disk file (legacy v1 ``kind``/``ts`` and stock
    single-act top-level ``events`` stay as stored).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid tape file: {path}")
    has_turns = isinstance(data.get("turns"), list) and bool(data.get("turns"))
    has_events = isinstance(data.get("events"), list)
    if not has_turns and not has_events:
        raise ValueError(f"invalid tape file (need events or turns): {path}")
    try:
        return normalize_tape_document(data)
    except ValueError as e:
        raise ValueError(f"invalid tape file: {path}: {e}") from e
