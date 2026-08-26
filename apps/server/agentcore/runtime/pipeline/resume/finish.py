"""Bill and close a resumed turn after settlement."""

from __future__ import annotations

from agentcore.runtime.closing_posture import reconcile_resume_closing
from agentcore.runtime.engine import join_segments
from agentcore.runtime.events import EventSink, FinishReason, message_end
from agentcore.runtime.facts import current_fact_log
from agentcore.runtime.pipeline.finalize import _journal_entries_for_turn
from agentcore.runtime.pipeline.settle import settle_successful_turn


async def finish_resume_turn(
    *,
    message_id: str,
    captain_run_id: str,
    captain_state,
    pre_pause_content: str,
    delegate_tool,
    debate_tool,
    profile,
    citations: list[dict],
    sink: EventSink,
    fact_log,
    audit_recorder,
    roster_writer,
    journal_writer,
    vision_cost_runs: list | None = None,
    pre_pause_reasoning: str = "",
    ask_settled: bool = False,
) -> dict:
    """Bill + close a resumed turn whose CEO loop ran (plan_review / ask_user continue).

    Assembles resume-specific content/reasoning first (``reconcile_resume_closing`` /
    ``join_segments``), then delegates the fold / citations / message_end / soft-fail
    stamp to :func:`settle_successful_turn` — same billing kernel as a fresh turn.
    The whole turn bills once here under the ORIGINAL ``message_id``.

    ``vision_cost_runs`` are the resumed turn's board_read 读图 ledger rows (role=vision,
    §九.4 Gap ②), collected off the shared ``ToolContext.cost_sink``.

    ``ask_settled`` is True when this resume answered an ask_user card: join then
    keeps only the post-resume body (do not splice leftover「请确认」prose).
    Plan-review / rate-limit continue leave it False.

    ``pre_pause_reasoning`` joins ahead of the live captain reasoning (G3) so
    multi-cycle pauses keep ``join(join(r1, r2), live)`` continuity.
    """
    # Resume-only body assembly — must land on captain_state before settle reads it.
    final_reasoning = join_segments(pre_pause_reasoning, captain_state.reasoning or "")
    captain_state.content = reconcile_resume_closing(
        pre_pause_content, captain_state.content, ask_settled=ask_settled
    )
    captain_state.reasoning = final_reasoning

    result = await settle_successful_turn(
        message_id=message_id,
        captain_run_id=captain_run_id,
        captain_state=captain_state,
        delegate_tool=delegate_tool,
        debate_tool=debate_tool,
        profile=profile,
        citations=citations,
        vision_cost_sink=vision_cost_runs or [],
        sink=sink,
        fact_log=fact_log,
        audit_recorder=audit_recorder,
        roster_writer=roster_writer,
        journal_writer=journal_writer,
    )
    # Preserve resume empty-reasoning → None (join_segments "" → falsy).
    result["reasoning_content"] = final_reasoning or None
    return result


def finish_terminal_resume(
    *,
    message_id: str,
    pre_pause_content: str,
    closing: str,
    sink: EventSink,
    pre_pause_reasoning: str = "",
    ask_settled: bool = False,
) -> dict:
    """Close a resumed turn whose settle returned terminal ``INTERACT`` (no CEO round).

    Historically used for ask_user stop; that path now CONTINUE-feeds the CEO on the
    first stop (拒答可见). A second consecutive same-turn stop upgrades settle to
    ``INTERACT`` again and lands here. Kept for any settle that still sets
    ``terminal_text``. No CEO round
    runs — ``closing`` is the whole reply. The pre-pause CEO round that raised the
    checkpoint was never billed (the turn paused before persistence), and a
    terminal finish runs nothing new, so this turn bills nothing — consistent with
    the「paused before persist = never billed」model. The seeded journal
    (checkpoint_required) + the emitted ``checkpoint_resolved`` persist so a reload
    replays the settled card.

    ``pre_pause_reasoning`` is preserved as the turn's reasoning (no live segment to
    join — G3 terminal path).
    """
    finish = FinishReason.END_TURN
    sink.emit(message_end(finish, rounds=0))
    journal_entries = _journal_entries_for_turn(current_fact_log.get(), sink=sink, finish=finish)
    return {
        "message_id": message_id,
        "content": reconcile_resume_closing(
            pre_pause_content, closing, ask_settled=ask_settled
        ),
        "reasoning_content": pre_pause_reasoning or None,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "rounds": 0,
        "finish_reason": finish,
        "citations": [],
        "cost_runs": [],
        "journal_entries": journal_entries,
    }


def finish_paused_resume(
    *,
    message_id: str,
    pre_pause_content: str,
    sink: EventSink,
    pre_pause_reasoning: str = "",
) -> dict:
    """Close a resumed turn that re-suspended during settle (downstream checkpoint).

    Mirrors the live engine's ``ToolEffect.SUSPEND`` → ``FinishReason.PAUSED`` path:
    no CEO round, the suspended tool_call stays PENDING, and the fresh durable frame
    (persisted inside ``resume_plan``) is the record. Worker spend from the interrupted
    drive rides that frame's ``completed`` and bills on the next cold resume — same as
    a live soft-pause yield.
    """
    finish = FinishReason.PAUSED
    sink.emit(message_end(finish, rounds=0))
    journal_entries = _journal_entries_for_turn(current_fact_log.get(), sink=sink, finish=finish)
    return {
        "message_id": message_id,
        "content": pre_pause_content,
        "reasoning_content": pre_pause_reasoning or None,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "rounds": 0,
        "finish_reason": finish,
        "citations": [],
        "cost_runs": [],
        "journal_entries": journal_entries,
    }
