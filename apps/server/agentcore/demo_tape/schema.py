"""Demo tape schema + constants (dev-only product-demo screen recording)."""

from __future__ import annotations

from typing import Any

# Reserved key for demo-tape frame divert / cursor. Content/reasoning live on the
# shared ``turn_paused`` fact (not under this key). Cursor meta rides
# ``turn_paused.extras[DEMO_TAPE_FRAME_KEY]`` and is mirrored in
# ``debate_arguments`` so ``is_demo_tape_frame`` can divert without scanning journal.
DEMO_TAPE_FRAME_KEY = "__demo_tape__"

# v2: event elements use SSE contract fields ``type`` + ``timestamp`` (plus pacing
# supersets like ``t_ms``). v1 on disk used the dialect ``kind`` + ``ts``; readers
# alias-compat at load/play time and never rewrite stock tape files.
TAPE_FORMAT_VERSION = 2
RECORDING_FORMAT_VERSION = 2
LEGACY_TAPE_FORMAT_VERSION = 1

# Cold-path durable pause cards — player stops, persists a tape frame, ends the
# turn as PAUSED, and waits for ``POST …/resume``.
PAUSE_REQUIRED_KINDS = frozenset(
    {
        "checkpoint_required",
        "plan_review_required",
    }
)

# Cold-path durable pauses wired for true tape-frame suspend + live resume.
TAPE_WIRED_PAUSE_KINDS = frozenset(
    {
        "checkpoint_required",
        "plan_review_required",
    }
)
TAPE_UNWIRED_PAUSE_KINDS = PAUSE_REQUIRED_KINDS - TAPE_WIRED_PAUSE_KINDS

# Hot-path tool approval — player registers into InteractionRegistry, awaits the
# live ``POST …/interactions/{id}`` resolve, keeps the turn running (no paused
# frame / no cold resume). Same decision-logging rule as cold path: any decision
# continues the recorded stream (no fork).
TAPE_HOT_PAUSE_KINDS = frozenset({"approval_required"})

# Director seek / burst: any interactive stop that may need auto-confirm when crossed.
TAPE_INTERACTIVE_PAUSE_KINDS = TAPE_WIRED_PAUSE_KINDS | TAPE_HOT_PAUSE_KINDS

# Recorded resolve events are skipped; the live resolve is emitted fresh.
PAUSE_RESOLVED_KINDS = frozenset(
    {
        "checkpoint_resolved",
        "plan_review_resolved",
        "approval_resolved",
        "team_preview_resolved",
    }
)

# Client-tool required events: hard-cut from tapes AND re-asserted at export time
# (defense beyond the cut table — future edit of TAPE_EXCLUDED_KINDS must not silently
# ship them). Full-chain tool stand-ins are a confirmed future direction at
# ``execute_tools`` (short-circuit by tool_call_id from recorded I/O); not built yet.
CLIENT_TOOL_REQUIRED_KINDS = frozenset(
    {
        "workspace_op_required",
        "board_op_required",
        "board_read_required",
        "desktop_notify_required",
        "external_mount_readonly_required",
        "host_op_required",
    }
)

# Recording → tape cut list. A recording captures the live stream verbatim; a tape
# must not replay:
# - message_start / message_end — source-turn lifecycle from the recording. The
#   replay turn's lifecycle follows the same live capture/bootstrap/resume
#   contract (player emits message_start on the send leg; resume uses shared
#   bootstrap which already emitted message_start, so that leg sets
#   emit_message_start=False — alignment, not a private lifecycle);
# - recorded pause settlements — the player re-emits the LIVE resolve on resume;
# - per-turn route/meta chrome (turn_saved / title / followups_generated / citations)
#   — turn_saved / title stay live-minted; followups_generated stays cut for old-tape
#   compatibility (chips mint offline; meta.followups on replay is ignored);
# - error — a transient banner from the source run must not replay as a real error;
# - transport-only client-tool requests — replaying them would drive REAL side
#   effects on the attached desktop (file ops / board mutations / OS notifications).
TAPE_EXCLUDED_KINDS = PAUSE_RESOLVED_KINDS | CLIENT_TOOL_REQUIRED_KINDS | frozenset(
    {
        "message_start",
        "message_end",
        "turn_saved",
        "title_generated",
        "followups_generated",
        "citations",
        "error",
        "team_preview_required",
        "team_preview_resolved",
    }
)


def event_type(ev: dict[str, Any]) -> str:
    """SSE event type from a tape/recording element.

    Contract field ``type`` is authoritative; legacy ``kind`` is a read-time alias.
    (Payload keys like ``payload.kind == "captain"`` are unrelated.)
    """
    return str(ev.get("type") or ev.get("kind") or "")


def persisted_captain_content_from_events(events: list[dict[str, Any]]) -> str:
    """Rebuild ``messages.content`` the way live finish does across durable pauses.

    Live SSE never emits the pause-boundary paragraph joiner — ``join_segments``
    inserts it only when persisting (and when the tape player assembles
    ``result[\"content\"]``). Raw ``content_delta`` concat therefore under-counts
    vs the DB oracle by that seam; this helper applies the same join at each
    wired cold-path pause so tape / replay can be compared to product truth.
    """
    from agentcore.runtime.engine.segments import join_segments

    acc = ""
    buf: list[str] = []
    for ev in events:
        et = event_type(ev)
        if et in TAPE_WIRED_PAUSE_KINDS:
            acc = join_segments(acc, "".join(buf))
            buf = []
            continue
        if et != "content_delta":
            continue
        delta = (ev.get("payload") or {}).get("delta") or ""
        if delta:
            buf.append(str(delta))
    return join_segments(acc, "".join(buf))


def event_timestamp(ev: dict[str, Any]) -> str | None:
    """Wall-clock ISO timestamp; prefer ``timestamp``, fall back to legacy ``ts``."""
    raw = ev.get("timestamp")
    if isinstance(raw, str):
        return raw
    raw = ev.get("ts")
    if isinstance(raw, str):
        return raw
    return None


def normalize_tape_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Copy an event element onto contract fields (``type`` / ``timestamp``).

    Drops legacy aliases ``kind`` / ``ts``. Preserves pacing supersets (``t_ms``)
    and any other keys. Does not mutate the input.
    """
    out = {
        k: v for k, v in ev.items() if k not in ("kind", "ts", "type", "timestamp")
    }
    out["type"] = event_type(ev)
    # Preserve explicit null timestamps from either dialect.
    if "timestamp" in ev:
        out["timestamp"] = ev.get("timestamp")
    elif "ts" in ev:
        out["timestamp"] = ev.get("ts")
    else:
        out["timestamp"] = None
    return out


def normalize_tape_events(events: list[Any]) -> list[dict[str, Any]]:
    """Normalize a list of event dicts; non-dicts pass through unchanged shape-wise."""
    out: list[dict[str, Any]] = []
    for ev in events:
        if isinstance(ev, dict):
            out.append(normalize_tape_event(ev))
        else:
            raise ValueError(f"tape event must be an object, got {type(ev).__name__}")
    return out


def is_demo_tape_frame(frame_or_suspension: Any) -> bool:
    """True when a paused frame / suspension carries the demo-tape marker."""
    if frame_or_suspension is None:
        return False
    args = getattr(frame_or_suspension, "debate_arguments", None)
    if isinstance(args, dict) and DEMO_TAPE_FRAME_KEY in args:
        return True
    if isinstance(frame_or_suspension, dict):
        nested = frame_or_suspension.get("debate_arguments") or {}
        if isinstance(nested, dict) and DEMO_TAPE_FRAME_KEY in nested:
            return True
        extras = frame_or_suspension.get(DEMO_TAPE_FRAME_KEY)
        if extras is not None:
            return True
    # turn_paused.extras adjunct (authoritative cursor home).
    entries = getattr(frame_or_suspension, "journal_entries", None)
    if isinstance(entries, list):
        from agentcore.runtime.facts import pre_pause_from_journal

        fact = pre_pause_from_journal(entries)
        if (
            fact is not None
            and isinstance(fact.extras, dict)
            and DEMO_TAPE_FRAME_KEY in fact.extras
        ):
            return True
    return False


def tape_frame_meta(suspension: Any) -> dict[str, Any]:
    """Demo-tape cursor meta (frame index / path / pacing).

    Prefers ``debate_arguments`` (divert marker; tests may retarget the path), then
    ``turn_paused.extras`` (authoritative home written at capture).
    """
    args = getattr(suspension, "debate_arguments", None)
    if isinstance(args, dict):
        nested = args.get(DEMO_TAPE_FRAME_KEY)
        if isinstance(nested, dict) and nested.get("tape"):
            return dict(nested)
    entries = getattr(suspension, "journal_entries", None)
    if isinstance(entries, list):
        from agentcore.runtime.facts import pre_pause_from_journal

        fact = pre_pause_from_journal(entries)
        if fact is not None and isinstance(fact.extras, dict):
            nested = fact.extras.get(DEMO_TAPE_FRAME_KEY)
            if isinstance(nested, dict):
                return dict(nested)
    if isinstance(args, dict):
        nested = args.get(DEMO_TAPE_FRAME_KEY)
        if isinstance(nested, dict):
            return dict(nested)
    return {}
