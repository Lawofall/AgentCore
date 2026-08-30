"""Single-point turn-state rehydration from ``turn_paused`` (batch 5).

Resume reads the last ``turn_paused`` in the frame's journal and rehydrates
display + control state in one place. Process timelines prefer progressive
``process_*`` / ``run_process_*`` journal rows; legacy frames without those fall
back to ``turn_paused.process`` / ``run_processes``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.events import EventSink, message_start
from agentcore.runtime.facts import TurnPausedFact, pre_pause_from_journal
from agentcore.runtime.journal.entries import _PROCESS_PREFIX, _RUN_PROCESS_PREFIX
from agentcore.runtime.loop_controller import LoopController
from agentcore.runtime.suspension import (
    PlanReviewSuspension,
    TurnSuspension,
)

logger = get_logger(__name__)


def _process_lanes_from_journal(
    entries: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Project captain / worker process steps from progressive journal kinds."""
    process: list[dict[str, Any]] = []
    run_processes: dict[str, list[dict[str, Any]]] = {}
    if not entries:
        return process, run_processes
    for entry in entries:
        kind = str(entry.get("kind") or "")
        payload = dict(entry.get("payload") or {})
        if kind.startswith(_PROCESS_PREFIX):
            process.append(payload)
        elif kind.startswith(_RUN_PROCESS_PREFIX):
            rid = payload.get("run_id")
            if rid:
                step = {k: v for k, v in payload.items() if k != "run_id"}
                run_processes.setdefault(str(rid), []).append(step)
    return process, run_processes


@dataclass
class RehydratedTurnState:
    """Resume-side turn state resolved from ``turn_paused`` (or legacy fallbacks)."""

    pre_pause_content: str | None = None
    """Authoritative deliverable content when ``from_turn_paused``; else ``None``
    (caller keeps the transcript heuristic)."""

    pre_pause_reasoning: str = ""
    controller_seed: dict[str, Any] | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    evidence_ledger: list[dict[str, Any]] = field(default_factory=list)
    presentation_format: dict[str, Any] | None = None
    automation_delivery: dict[str, Any] | None = None
    from_turn_paused: bool = False
    fact: TurnPausedFact | None = None


def rehydrate_from_turn_paused(
    *,
    sink: EventSink,
    suspension: TurnSuspension,
) -> RehydratedTurnState:
    """Seed sink display state + resolve content/reasoning/controller/citations.

    Call after ``sink.seed_journal(...)``. When the journal has no ``turn_paused``,
    returns legacy citations from ``suspension.citations`` and leaves pre_pause /
    controller unset so the caller keeps current heuristics.
    """
    fact = pre_pause_from_journal(suspension.journal_entries)
    if fact is None:
        return RehydratedTurnState(
            citations=list(suspension.citations or []),
            from_turn_paused=False,
        )

    process, run_processes = _process_lanes_from_journal(suspension.journal_entries)
    # Prefer progressive process_* ; legacy turn_paused snapshot is read-only fallback.
    if not process and fact.process:
        process = list(fact.process)
    if not run_processes and fact.run_processes:
        run_processes = dict(fact.run_processes)
    if process:
        sink.seed_process(process)
    if run_processes:
        sink.seed_run_processes(run_processes)

    # G2: fact is authoritative; frame.citations is the fallback.
    citations = list(fact.citations or suspension.citations or [])
    evidence_ledger = list(fact.evidence_ledger or [])
    controller = dict(fact.controller) if fact.controller else {}
    presentation_format = (
        dict(fact.presentation_format) if fact.presentation_format else None
    )
    automation_delivery = (
        dict(fact.automation_delivery) if fact.automation_delivery else None
    )

    logger.info(
        "pipeline.resume_rehydrated",
        checkpoint_id=fact.checkpoint_id,
        suspension_kind=fact.suspension_kind,
        process_steps=len(process),
        run_process_keys=len(run_processes),
        citations=len(citations),
        evidence_ledger=len(evidence_ledger),
        has_controller=bool(controller),
        has_presentation_format=bool(presentation_format),
        has_automation_delivery=bool(automation_delivery),
    )
    return RehydratedTurnState(
        pre_pause_content=fact.content or "",
        pre_pause_reasoning=fact.reasoning or "",
        controller_seed=controller,
        citations=citations,
        evidence_ledger=evidence_ledger,
        presentation_format=presentation_format,
        automation_delivery=automation_delivery,
        from_turn_paused=True,
        fact=fact,
    )


def bootstrap_resume_display(
    *,
    sink: EventSink,
    suspension: TurnSuspension,
    conversation_id: str | None = None,
) -> RehydratedTurnState:
    """Shared resume display open: ``message_start`` + journal seed + turn_paused rehydrate.

    Live ``resume_chat_pipeline`` and demo-tape continue both call this so display
    continuity (process lanes, citations, pre_pause) cannot drift between paths.
    G6 reinjection is armed separately via :func:`arm_content_reset_reinjection`
    once the authoritative pre_pause string is known.
    """
    cid = conversation_id if conversation_id is not None else suspension.conversation_id
    sink.emit(message_start(suspension.message_id, conversation_id=cid))
    sink.seed_journal(suspension.journal)
    return rehydrate_from_turn_paused(sink=sink, suspension=suspension)


def arm_content_reset_reinjection(sink: EventSink, pre_pause: str) -> None:
    """G6: after each ``content_reset``, display-only reinject ``pre_pause`` (+ joiner).

    Stale「请确认」ask framing is not reinjected — the question already lives on the
    ask_user card; reinjecting it and then streaming「已全部收卷」recreates A∪C live.

    Dispatch/process kickoff（方向：派团队…）likewise is not reinjected — process
    already happened; reinjecting it makes the user-visible bubble a work log.
    """
    if not pre_pause:
        return
    from agentcore.runtime.closing_posture import (
        claims_full_delivery,
        claims_needs_confirm,
        pre_pause_for_user_visible_continuity,
    )

    base = pre_pause_for_user_visible_continuity(pre_pause)
    if not base:
        return
    if claims_needs_confirm(base) and not claims_full_delivery(base):
        return
    sink.set_content_reset_reinjection(base + "\n\n")


def batch_shape_for_settled_suspension(
    suspension: TurnSuspension,
) -> tuple[int, bool]:
    """``(node_count, has_deps)`` for settle-side ``mark_post_delegate`` (G5)."""
    plan = getattr(suspension, "plan", None)
    nodes = getattr(plan, "nodes", None) if plan is not None else None
    if isinstance(nodes, list) and nodes:
        return len(nodes), any(bool(getattr(n, "depends_on", None)) for n in nodes)

    return 0, False


def mark_controller_after_settle(
    controller_seed: dict[str, Any] | None,
    suspension: TurnSuspension,
) -> dict[str, Any] | None:
    """After plan_review settle, latch post_delegate with batch shape.

    Only meaningful on the ``turn_paused`` path (caller gates on ``from_turn_paused``):
    the snapshot's ``post_delegate`` is False because the pause happened before the
    delegate/debate tool returned. Ask-user settles leave the seed unchanged.
    """
    if not isinstance(suspension, PlanReviewSuspension):
        return controller_seed

    controller = LoopController()
    if controller_seed:
        controller.apply_seed(controller_seed)
    node_count, has_deps = batch_shape_for_settled_suspension(suspension)
    controller.mark_post_delegate(node_count=node_count, has_deps=has_deps)
    seed = controller.export_seed()
    logger.info(
        "pipeline.resume_settle_post_delegate",
        kind=suspension.kind.value,
        node_count=node_count,
        has_deps=has_deps,
        first_batch_substantial=seed.get("first_batch_substantial"),
    )
    return seed
