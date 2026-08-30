"""Assemble a ``turn_paused`` fact at durable suspension (batch 4 capture side).

Builds the §二契约 payload from the live sink + captain-loop mirror + prior journal
snapshot. Best-effort: missing mirror / sink / fields degrade to empty; callers must
not let assembly failure abort the pause persist path.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.engine.segments import join_segments
from agentcore.runtime.facts import TurnPausedFact, pre_pause_from_journal

logger = get_logger(__name__)

# ask_user: folded prose → pre-round bubble; model-owned message → keep final_content.
_ASK_USER_KIND = "ask_user"


def build_turn_paused_fact(
    *,
    checkpoint_id: str,
    suspension_kind: str,
    required_event: Any,
    journal_entries_before_trailing: list[dict[str, Any]],
    sink: Any | None,
    extras: dict[str, Any] | None = None,
) -> TurnPausedFact:
    """Assemble one ``TurnPausedFact`` from live capture inputs (G1–G5 / G7).

    ``journal_entries_before_trailing`` must be the fact-log snapshot **without**
    this pause's ``*_required`` / ``turn_paused`` trailing entries — multi-cycle
    inheritance reads the last prior ``turn_paused`` from it.
    ``extras`` rides the same fact for adjuncts (e.g. demo-tape frame cursor).
    """
    prior = pre_pause_from_journal(journal_entries_before_trailing)
    prior_content = prior.content if prior is not None else ""
    prior_reasoning = prior.reasoning if prior is not None else ""

    segment_content = _segment_content(suspension_kind)
    # Event-source replay (and any path without a captain-loop mirror) keeps the
    # deliverable in the sink process lane — use it when the mirror is absent.
    if not segment_content and sink is not None:
        try:
            segment_content = sink.streamed_content() or ""
        except Exception:
            logger.warning(
                "turn_paused.streamed_content_failed",
                checkpoint_id=checkpoint_id,
                exc_info=True,
            )
    content = join_segments(prior_content, segment_content)

    live_reasoning = ""
    # Process / run_processes: progressive ``process_*`` / ``run_process_*`` are the
    # sole write path (flushed below). ``turn_paused`` no longer dual-writes the
    # timeline — read side still accepts legacy frames via fold / rehydrate fallback.
    process: list[dict[str, Any]] = []
    run_processes: dict[str, list[dict[str, Any]]] = {}
    if sink is not None:
        # Marker first (owns captain lane through the pause anchor — keeps
        # before_last_team order), then flush worker lanes + any remainder.
        # Pre-flushing captain before the marker used to pin ``team`` ahead of
        # ``team_preview`` in process_* (wrong cold-reload order).
        try:
            live_reasoning = sink.streamed_reasoning() or ""
        except Exception:
            logger.warning(
                "turn_paused.streamed_reasoning_failed",
                checkpoint_id=checkpoint_id,
                exc_info=True,
            )
        try:
            event_type = _event_type(required_event)
            payload = dict(getattr(required_event, "payload", None) or {})
            persist_marker = getattr(sink, "persist_required_marker", None)
            if event_type is not None and callable(persist_marker):
                persist_marker(event_type, payload)
        except Exception:
            logger.warning(
                "turn_paused.process_marker_failed",
                checkpoint_id=checkpoint_id,
                exc_info=True,
            )
        flush = getattr(sink, "flush_process_to_journal", None)
        if callable(flush):
            try:
                flush()
            except Exception:
                logger.warning(
                    "turn_paused.process_flush_failed",
                    checkpoint_id=checkpoint_id,
                    exc_info=True,
                )

    reasoning = join_segments(prior_reasoning, live_reasoning)

    citations: list[dict[str, Any]] = []
    try:
        from agentcore.runtime.suspension import turn_citations

        citations = list(turn_citations.get() or [])
    except Exception:
        logger.warning(
            "turn_paused.citations_failed",
            checkpoint_id=checkpoint_id,
            exc_info=True,
        )

    evidence_ledger: list[dict[str, Any]] = []
    try:
        from agentcore.runtime.suspension import turn_evidence_ledger

        led = turn_evidence_ledger.get()
        if led is not None:
            all_entries = getattr(led, "all_entries", None)
            if callable(all_entries):
                evidence_ledger = list(all_entries())
    except Exception:
        logger.warning(
            "turn_paused.evidence_ledger_failed",
            checkpoint_id=checkpoint_id,
            exc_info=True,
        )

    presentation_format: dict[str, Any] | None = None
    try:
        from agentcore.core.log_context import get_log_value
        from agentcore.runtime.runs.presentation_format import (
            snapshot_presentation_format_for_pause,
        )

        presentation_format = snapshot_presentation_format_for_pause(
            journal_entries_before_trailing,
            conversation_id=str(get_log_value("conversation_id") or "") or None,
        )
    except Exception:
        logger.warning(
            "turn_paused.presentation_format_failed",
            checkpoint_id=checkpoint_id,
            exc_info=True,
        )

    automation_delivery: dict[str, Any] | None = None
    try:
        from agentcore.core.log_context import get_log_value
        from agentcore.runtime.runs.automation_delivery import (
            snapshot_automation_delivery_for_pause,
        )

        automation_delivery = snapshot_automation_delivery_for_pause(
            journal_entries_before_trailing,
            conversation_id=str(get_log_value("conversation_id") or "") or None,
        )
    except Exception:
        logger.warning(
            "turn_paused.automation_delivery_failed",
            checkpoint_id=checkpoint_id,
            exc_info=True,
        )

    controller = _controller_seed()

    return TurnPausedFact(
        checkpoint_id=checkpoint_id,
        suspension_kind=suspension_kind,
        content=content,
        reasoning=reasoning,
        process=process,
        run_processes=run_processes,
        citations=citations,
        evidence_ledger=evidence_ledger,
        controller=controller,
        presentation_format=presentation_format,
        automation_delivery=automation_delivery,
        extras=dict(extras) if extras else None,
    )


def _segment_content(suspension_kind: str) -> str:
    """This pause's deliverable segment from the captain mirror (G4)."""
    try:
        from agentcore.runtime.engine.loop import current_captain_loop

        mirror = current_captain_loop.get()
    except Exception:
        logger.warning("turn_paused.mirror_read_failed", exc_info=True)
        return ""
    if mirror is None:
        return ""
    if suspension_kind == _ASK_USER_KIND and mirror.ask_user_content_folded:
        return mirror.content_before_round or ""
    return mirror.final_content or ""


def _controller_seed() -> dict[str, Any]:
    try:
        from agentcore.runtime.engine.loop import current_captain_loop

        mirror = current_captain_loop.get()
    except Exception:
        logger.warning("turn_paused.controller_read_failed", exc_info=True)
        return {}
    if mirror is None or mirror.controller is None:
        return {}
    try:
        return dict(mirror.controller.export_seed())
    except Exception:
        logger.warning("turn_paused.controller_export_failed", exc_info=True)
        return {}


def _event_type(required_event: Any) -> Any | None:
    t = getattr(required_event, "type", None)
    if t is None:
        return None
    return getattr(t, "value", t)
