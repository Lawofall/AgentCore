"""Pipeline finalize helpers: journal entries and display replay projection."""

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.events import (
    EventSink,
    FinishReason,
)
from agentcore.runtime.facts import (
    TurnFactLog,
)
from agentcore.runtime.journal import (
    KIND_TURN_END,
    journal_entries_from_display_runs,
)
from agentcore.runtime.journal.entries import _PROCESS_PREFIX, _RUN_PROCESS_PREFIX

logger = get_logger(__name__)


def _should_persist_journal(sink: EventSink) -> bool:
    """True when turn has replayable surface (team/process/context/error)."""
    return not (
        sink.execution_journal() is None
        and sink.process_timeline() is None
        and sink.run_process_timelines() is None
        and sink.captain_context() is None
        and sink.last_turn_error() is None
    )


def _build_runs_payload(
    sink: EventSink, finish: FinishReason, *, outcome: str | None = None
) -> dict[str, Any] | None:
    """Assemble the client-facing ``runs`` replay payload from the turn's sink.

    Used only to project ``journal_entries`` back into the wire shape the desktop /
    sidecar forwards on local-turn write-back (:func:`runs_from_entries`). The pipeline
    result itself carries ``journal_entries`` only — not this dict.
    """
    if not _should_persist_journal(sink):
        return None
    journal = sink.execution_journal()
    process = sink.process_timeline()
    run_processes = sink.run_process_timelines()
    captain_context = sink.captain_context()
    turn_error = sink.last_turn_error()
    payload: dict[str, Any] = {
        "events": journal or [],
        "finish_reason": finish.value,
    }
    if outcome in ("ok", "partial", "paused", "error"):
        payload["outcome"] = outcome
    if process:
        payload["process"] = process
    if run_processes:
        payload["run_processes"] = run_processes
    if captain_context is not None:
        payload["captain_context"] = captain_context
    if turn_error is not None:
        # Durable home for the transport-only ``error`` SSE (Tier 2 a).
        payload["error"] = {
            "code": turn_error.get("code") or "",
            "message": turn_error.get("message") or "",
            **(
                {"context": turn_error["context"]}
                if isinstance(turn_error.get("context"), dict)
                else {}
            ),
        }
    return payload


def _turn_end_entry(
    finish: FinishReason,
    *,
    error: dict[str, Any] | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"finish_reason": finish.value}
    if error is not None:
        payload["error"] = error
    if outcome in ("ok", "partial", "paused", "error"):
        payload["outcome"] = outcome
    return {"kind": KIND_TURN_END, "payload": payload, "ts": None}


def _entries_already_have_turn_end(entries: list[dict[str, Any]]) -> bool:
    return any((e.get("kind") or "") == KIND_TURN_END for e in entries)


def _process_kinds_already_in_log(entries: list[dict[str, Any]]) -> bool:
    """True when fact_log already carries progressive process_* / run_process_* facts."""
    for e in entries:
        kind = e.get("kind") or ""
        if kind.startswith(_PROCESS_PREFIX) or kind.startswith(_RUN_PROCESS_PREFIX):
            return True
    return False


def _journal_entries_for_turn(
    fact_log: TurnFactLog | None,
    *,
    sink: EventSink,
    finish: FinishReason,
    outcome: str | None = None,
) -> list[dict[str, Any]] | None:
    """Compose durable journal entries for a completed turn (or None when gated off).

    Progressive process persistence (process lane = mid-run refresh source of truth):
    closed steps already ride ``fact_log`` via append-on-emit. Finalize only flushes
    still-open trailing steps + ``turn_end`` — never re-dumps the full process table.

    Resume / paths without a fact log still flatten the sink's display replay via
    :func:`journal_entries_from_display_runs` (legacy / salvage).
    """
    runs = _build_runs_payload(sink, finish, outcome=outcome)
    if runs is None:
        return None

    turn_error = runs.get("error")

    # Close open text / markers into the ambient log before composing the tail.
    sink.flush_process_to_journal()

    if fact_log is not None:
        entries = fact_log.entries()
        # Progressive path: process steps already in the log (or just flushed).
        # Only append turn_end. Fall back to dumping process from the sink when the
        # log has no process_* yet (tests / degraded turns without a journal writer).
        if not _process_kinds_already_in_log(entries):
            tail = journal_entries_from_display_runs(
                {
                    "process": runs.get("process"),
                    "run_processes": runs.get("run_processes"),
                    "finish_reason": runs.get("finish_reason"),
                    **({"error": turn_error} if turn_error is not None else {}),
                    **({"outcome": outcome} if outcome else {}),
                }
            )
            return entries + (tail or [])
        if not _entries_already_have_turn_end(entries):
            return entries + [_turn_end_entry(finish, error=turn_error, outcome=outcome)]
        return entries

    return journal_entries_from_display_runs(runs)


def _coerce_finish_reason(value: Any) -> FinishReason:
    if isinstance(value, FinishReason):
        return value
    raw = getattr(value, "value", value)
    if isinstance(raw, str):
        try:
            return FinishReason(raw)
        except ValueError:
            pass
    return FinishReason.END_TURN


def refresh_result_journal_from_host(
    result: dict[str, Any] | None, *, sink: EventSink
) -> None:
    """Rebuild ``result.journal_entries`` from the host fact log after a detached drive.

    Pipeline settle snapshots the fact log *before* post-detach run terminals.
    Sidecar outbox finalize then replaces the progressive journal with that
    snapshot — dropping frames that landed on the host writer after detach.
    """
    if not isinstance(result, dict):
        return
    from agentcore.runtime.coordination.session import (
        registered_coordination_for_conversation,
    )

    cid = str(getattr(sink, "_conversation_id", None) or "").strip()
    session = registered_coordination_for_conversation(cid) if cid else None
    fact_log = getattr(session, "host_fact_log", None) if session is not None else None
    if fact_log is None or not hasattr(fact_log, "entries"):
        return
    finish = _coerce_finish_reason(result.get("finish_reason"))
    outcome = result.get("outcome")
    if outcome not in ("ok", "partial", "paused", "error"):
        outcome = None
    entries = _journal_entries_for_turn(
        fact_log, sink=sink, finish=finish, outcome=outcome
    )
    if entries is None:
        # Surface gate can hide a display journal that only grew post-detach
        # run terminals (no run_plan on this sink). The host fact log is the
        # source of truth for finalize replacement.
        entries = fact_log.entries()
        if entries and not _entries_already_have_turn_end(entries):
            entries = entries + [_turn_end_entry(finish, outcome=outcome)]
    if entries:
        result["journal_entries"] = entries
