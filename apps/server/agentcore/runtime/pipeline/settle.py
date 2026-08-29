"""Fresh-turn settle + salvage: success fold, failed captain, exception path."""

from __future__ import annotations

import contextlib
from dataclasses import asdict
from typing import Any

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import (
    UNCLASSIFIED_EXCEPTION_USER_MESSAGE,
    AgentCoreError,
    error_fields_for,
)
from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.citations import merge_citations, reconcile_citations
from agentcore.runtime.costing import aggregate_cost, captain_run_cost_from_state
from agentcore.runtime.events import (
    EventSink,
    FinishReason,
    citations_event,
    content_delta,
    error_event,
    message_end,
)
from agentcore.runtime.facts import TurnFactLog, TurnPausedFact, record_turn_fact
from agentcore.runtime.ledger_channel import emit_turn_evidence_ledger
from agentcore.runtime.pipeline.finalize import _journal_entries_for_turn
from agentcore.runtime.runs import RunPhase
from agentcore.runtime.turn.ceo_continue import (
    is_ceo_rate_limit_pause,
    mark_host_turn_paused,
)
from agentcore.runtime.turn.outcome import (
    last_delegate_tool_output_from_events,
    resolve_turn_outcome,
)

logger = get_logger(__name__)


def _salvage_reply_and_outcome(
    *,
    sink: EventSink,
    content: str,
    finish: object,
) -> tuple[str, str | None]:
    """Fill empty captain prose from last user-facing delegate output; stamp turn outcome.

    Coordination host echo is CEO-audience and is not copied into the user bubble.
    """
    events = sink.history_snapshot()
    if not (content or "").strip():
        salvaged = last_delegate_tool_output_from_events(events)
        if salvaged:
            content = salvaged
            sink.emit(content_delta(salvaged))
            logger.info(
                "engine.structured_reply_salvaged",
                chars=len(salvaged),
                source="delegate_events",
            )
            events = sink.history_snapshot()
    outcome = resolve_turn_outcome(
        events=events,
        finish_reason=finish,
        has_error=sink.last_turn_error() is not None,
    )
    return content, outcome


async def settle_successful_turn(
    *,
    message_id: str,
    captain_run_id: str,
    captain_state: Any,
    delegate_tool: Any,
    debate_tool: Any,
    profile: Any,
    citations: list[dict],
    vision_cost_sink: list,
    sink: EventSink,
    fact_log: TurnFactLog,
    audit_recorder: Any,
    roster_writer: Any,
    journal_writer: Any,
) -> dict:
    """Fold usage/cost/citations and emit message_end for a successful captain run."""
    # 受监督的波循环 P5「Edge」: if the CEO yielded at a delegate boundary (晚绑定 / scope)
    # but ended the turn without a ``replan``, fold the已完成 workers' usage / ledger /
    # citations in and release the dangling supervised plan (implicit stop) — else that
    # work would be unbilled and its sources unshown. No-op when nothing is paused, so a
    # normal turn is untouched. Must run BEFORE the turn usage / cost / citations fold.
    await delegate_tool.dispose_open_supervised()

    final_content = captain_state.content
    final_reasoning = captain_state.reasoning
    rounds = captain_state.rounds
    finish = captain_state.finish_override or (
        FinishReason.END_TURN if rounds < profile.max_rounds else FinishReason.MAX_ROUNDS
    )
    final_content, outcome = _salvage_reply_and_outcome(
        sink=sink, content=final_content, finish=finish
    )
    if is_ceo_rate_limit_pause(sink=sink, finish=finish):
        outcome = "paused"
        mark_host_turn_paused()
        record_turn_fact(
            TurnPausedFact(
                checkpoint_id="",
                suspension_kind="ceo_continue",
                content=final_content,
                reasoning=final_reasoning or "",
            ).to_fact()
        )

    # Turn usage = the captain run's own spend (priced once in the executor onto
    # captain_state.cost/.usage) + the delegated workers' usage + every 续派
    # continuation's usage (folded into delegate via continue_from / redirect),
    # both accumulated on their tool instances across the turn. ``delegate`` is
    # non-terminal, so the captain loop never metered their tokens; the cache
    # split rides along so the folded total stays priceable.
    turn_usage = (
        TokenUsage.from_usage_dict(captain_state.usage)
        + TokenUsage.from_usage_dict(delegate_tool.usage)
        + TokenUsage.from_usage_dict(debate_tool.usage)
    )

    # Per-run cost ledger for 落账 (决策②: captain root + one row per member).
    # The captain was priced once in the executor (captain_state.cost); read it
    # into the captain ledger row (no re-price). Members were priced onto their
    # RunState in the executor and collected on the delegate tool. Built before
    # message_end so the turn total can ride on it (回合总账实时); the service
    # then attaches the user/conversation/message envelope and persists the
    # rows (warning-only on failure).
    captain_cost = captain_run_cost_from_state(captain_run_id, captain_state)
    cost_runs = [
        asdict(captain_cost),
        *(asdict(r) for r in delegate_tool.run_ledger),
        # 辩论：主持人一行 + 每个辩手每轮一行（含 continue_run 续写），各自 parented
        # 到上级（辩手→主持人、主持人→captain），与 delegate 同形折账。
        *(asdict(r) for r in debate_tool.run_ledger),
        # AI 协作白板 读图: each board_read 视觉子调用 is its own role=vision row,
        # parented to the calling run (§九.4 Gap ②). Empty unless a read billed.
        *(asdict(r) for r in vision_cost_sink),
    ]
    turn_cost = aggregate_cost(cost_runs)

    # Fold the delegated workers' web sources into the turn's shared card
    # (deduped/capped against the CEO's own searches). The CEO collected its
    # sources live during the loop (numbered + cited inline); workers collected
    # theirs un-numbered, so appending them here keeps the CEO's [n] stable and
    # still surfaces the WHOLE team's research to the user. Mirrors how worker
    # usage/cost are folded back off the delegate tool instance above.
    merge_citations(citations, delegate_tool.citations)
    merge_citations(citations, debate_tool.citations)

    # 引用出口：正文不剥号。来源卡 = 已登记非 blocked（含 search-only）；
    # 未登记 #rN / 悬空 [n] 留白字，只记观测。
    led = None
    citable_ids = None
    try:
        from agentcore.runtime.suspension import turn_evidence_ledger

        led = turn_evidence_ledger.get()
        if led is not None:
            # settle：正文实际引用且已 deep_read → 持久 selected（跨回合 hydrate 保留）。
            led.mark_selected_from_content(final_content)
            citable_ids = led.citable_ids()
    except Exception:
        logger.warning("citations.ledger_lookup_failed", message_id=message_id, exc_info=True)
    final_content, citations, stray_n, stray_r = reconcile_citations(
        final_content, citations, citable_ids=citable_ids
    )
    if stray_n:
        logger.warning(
            "citations.out_of_range",
            message_id=message_id,
            markers=stray_n,
            citation_count=len(citations),
        )
    if stray_r:
        logger.warning(
            "citations.invalid_ledger_ref",
            message_id=message_id,
            markers=stray_r,
            citable_count=len(citable_ids or ()),
        )

    # Turn 级台账通道 + P2 投影：全量 entries + cited_ids；citations_event = 仅引用集。
    citations, evidence_ledger, cited_ids = emit_turn_evidence_ledger(
        sink, ledger=led, content=final_content, citations=citations
    )

    # Emit before message_end so the client attaches source cards to the
    # assistant message while it is still the live streaming bubble.
    if citations:
        sink.emit(citations_event(citations))

    collab = {
        **delegate_tool.collab,
        "revises": delegate_tool.continuation_count,
        # 上一行的子集：用户点「立即改此人」促成的那几次。运营口径读 revises 总数不变；
        # 用户面减掉这份，才不会把用户自己的操作说成队友互检。
        "revises_by_user": delegate_tool.user_continuation_count,
        "audit_drops": audit_recorder.drops,
    }
    sink.emit(
        message_end(
            finish,
            input_tokens=turn_usage.input_tokens,
            output_tokens=turn_usage.output_tokens,
            reasoning_tokens=turn_usage.reasoning_tokens,
            cache_hit_tokens=turn_usage.cache_hit_tokens,
            cache_miss_tokens=turn_usage.cache_miss_tokens,
            rounds=rounds,
            cost=turn_cost,
            collab=collab,
            outcome=outcome,
        )
    )

    journal_entries = _journal_entries_for_turn(
        fact_log, sink=sink, finish=finish, outcome=outcome
    )

    # Drain journal → audit projection fully BEFORE 定格 audit_drops: the teardown
    # flush (finally) re-drains the writer, which can schedule + drop more audit
    # writes after drops was read — undercounting the persisted turn_metrics.audit_drops
    # (采集降级遥测 → admin aggregate). Mirror the finally order (journal then recorder);
    # best-effort so a drain fault never turns a successful turn into an error.
    with contextlib.suppress(Exception):
        await journal_writer.flush()
    await audit_recorder.flush()
    if roster_writer is not None:
        await roster_writer.flush()
    # 回合收口前 boundary flush (P1) — segments cleared after finalize snapshot.
    with contextlib.suppress(Exception):
        await sink.flush_stream_state()
    result: dict = {
        "message_id": message_id,
        "content": final_content,
        "reasoning_content": final_reasoning,
        "input_tokens": turn_usage.input_tokens,
        "output_tokens": turn_usage.output_tokens,
        "prompt_tokens": TokenUsage.from_usage_dict(captain_state.usage).last_prompt_tokens,
        "reasoning_tokens": turn_usage.reasoning_tokens,
        "cache_hit_tokens": turn_usage.cache_hit_tokens,
        "cache_miss_tokens": turn_usage.cache_miss_tokens,
        "rounds": rounds,
        "finish_reason": finish,
        "citations": citations,
        "evidence_ledger": evidence_ledger,
        "cited_ids": cited_ids,
        "cost_runs": cost_runs,
        "journal_entries": journal_entries,
        # 协作质量 (学·度量 §2.5): turn-level orchestration signals for turn_metrics +
        # chat.turn_complete / message_end — boundary_yields / scope_signals /
        # escalations off the delegate accumulator, plus the revise count (定向唤回).
        "collab": collab,
        "audit_drops": audit_recorder.drops,
        "outcome": outcome,
    }
    # Soft-fail path (raise_on_error=False → settle_successful_turn): the live
    # ``error`` SSE must also land on the settle result so cloud persist stamps
    # turn_end.error / usage.status=failed (reload error card).
    turn_error = sink.last_turn_error()
    if turn_error is not None:
        result["error"] = turn_error.get("message") or ""
        result["error_code"] = turn_error.get("code") or ErrorCode.LLM_ERROR
    return result


async def salvage_failed_captain(
    *,
    message_id: str,
    captain_run_id: str,
    captain_state: Any,
    vision_cost_sink: list,
    sink: EventSink,
    audit_recorder: Any,
    roster_writer: Any,
) -> dict:
    """Salvage content/cost when the captain run ends FAILED."""
    coded = (getattr(captain_state, "error_code", None) or "").strip()
    raw = (getattr(captain_state, "error", None) or "").strip()
    if coded:
        code, message = coded, raw or UNCLASSIFIED_EXCEPTION_USER_MESSAGE
    else:
        # Uncoded crash (TypeError in tool ctor, …): logs already have str(exc)
        # on ``run.captain_failed``; the turn face must not leak it or flatten a
        # later coded failure. Same product sentence as salvage_pipeline_exception.
        code, message = ErrorCode.PIPELINE_ERROR, UNCLASSIFIED_EXCEPTION_USER_MESSAGE
    err_ctx = None
    retry_after = getattr(captain_state, "error_retry_after", None)
    if retry_after is not None:
        err_ctx = {"retry_after": retry_after}
    sink.emit(error_event(code, message, context=err_ctx))
    # Salvage longest available text (segment / captain_state / sink) — P1 §3.4.
    with contextlib.suppress(Exception):
        await sink.flush_stream_state()
    from agentcore.conversation.store.merge import pick_longest
    from agentcore.runtime.events.stream_checkpointer import (
        CHANNEL_CAPTAIN_CONTENT,
        CHANNEL_CAPTAIN_REASONING,
    )

    mem = sink.stream_memory_snapshot()
    salvaged_content = pick_longest(
        mem.get(CHANNEL_CAPTAIN_CONTENT),
        captain_state.content,
        sink.streamed_content(),
    )
    salvaged_reasoning = pick_longest(
        mem.get(CHANNEL_CAPTAIN_REASONING),
        captain_state.reasoning,
        sink.streamed_reasoning(),
    )
    salvaged_content, outcome = _salvage_reply_and_outcome(
        sink=sink, content=salvaged_content, finish=FinishReason.ERROR
    )
    sink.emit(message_end(FinishReason.ERROR, outcome=outcome))
    # A captain that died mid-loop still burned tokens (B-deep 失败计费): the
    # executor priced them onto captain_state, so carry the captain ledger row
    # back even on error — _persist_turn_result writes cost_runs independently
    # of whether any assistant text landed. Skip when nothing metered (no
    # usage → no row), so a pre-LLM crash stays free.
    cost_runs = [
        *(
            [asdict(captain_run_cost_from_state(captain_run_id, captain_state))]
            if captain_state.usage
            else []
        ),
        # A board_read 读图 sub-call may have billed before the captain died
        # (§九.4 Gap ②): carry those vision rows so the spend isn't lost on error.
        *(asdict(r) for r in vision_cost_sink),
    ]
    await audit_recorder.flush()
    if roster_writer is not None:
        await roster_writer.flush()
    return {
        "message_id": message_id,
        "content": salvaged_content,
        "reasoning_content": salvaged_reasoning or None,
        "error": message,
        "error_code": code,
        "finish_reason": FinishReason.ERROR,
        "prompt_tokens": TokenUsage.from_usage_dict(
            captain_state.usage or {}
        ).last_prompt_tokens,
        "cost_runs": cost_runs,
        "audit_drops": audit_recorder.drops,
        "outcome": outcome,
    }


async def salvage_pipeline_exception(
    *,
    e: BaseException,
    message_id: str,
    sink: EventSink,
    fact_log: TurnFactLog | None,
    audit_recorder: Any,
    roster_writer: Any,
) -> dict:
    """Salvage journal + streamed text when the pipeline raises."""
    # Original exception text + typed details stay in logs only (product face below).
    log_fields: dict[str, Any] = {
        "error": str(e),
        "error_type": type(e).__name__,
    }
    if isinstance(e, AgentCoreError) and e.details:
        log_fields["error_details"] = e.details
    logger.error("pipeline.error", **log_fields, exc_info=True)
    # Preserve a structured AgentCoreError.code that escaped to the pipeline
    # boundary (e.g. LLM_KEY_INVALID) instead of flattening every crash to
    # PIPELINE_ERROR — the client only acts on specific codes (统一错误码).
    # Unclassified exceptions: curated product fallback — never str(e) on the face.
    code, message, err_ctx = error_fields_for(
        e,
        fallback_code=ErrorCode.PIPELINE_ERROR,
        fallback_message=UNCLASSIFIED_EXCEPTION_USER_MESSAGE,
    )
    sink.emit(error_event(code, message, context=err_ctx))
    # Salvage longest available text from segment / sink (captain_state may be absent).
    with contextlib.suppress(Exception):
        await sink.flush_stream_state()
    from agentcore.conversation.store.merge import pick_longest
    from agentcore.runtime.events.stream_checkpointer import (
        CHANNEL_CAPTAIN_CONTENT,
        CHANNEL_CAPTAIN_REASONING,
    )

    mem = sink.stream_memory_snapshot()
    salvaged_content = pick_longest(
        mem.get(CHANNEL_CAPTAIN_CONTENT),
        sink.streamed_content(),
    )
    salvaged_reasoning = pick_longest(
        mem.get(CHANNEL_CAPTAIN_REASONING),
        sink.streamed_reasoning(),
    )
    salvaged_content, outcome = _salvage_reply_and_outcome(
        sink=sink, content=salvaged_content, finish=FinishReason.ERROR
    )
    sink.emit(message_end(FinishReason.ERROR, outcome=outcome))
    # 异常也落库: a crash mid-turn must NOT discard already-finished work (a
    # completed debate / delegated workers). Carry the journal so persist_turn_result
    # writes it under the abnormal message even with empty reply content — otherwise a
    # 6-min debate that survived the turn would vanish on the next refresh. Best-effort:
    # never let journal assembly mask the original error.
    try:
        crash_journal = _journal_entries_for_turn(
            fact_log, sink=sink, finish=FinishReason.ERROR, outcome=outcome
        )
    except Exception:  # noqa: BLE001 — salvage is best-effort; keep the real error
        crash_journal = None
    await audit_recorder.flush()
    if roster_writer is not None:
        await roster_writer.flush()
    return {
        "message_id": message_id,
        "content": salvaged_content,
        "reasoning_content": salvaged_reasoning or None,
        "error": message,
        "error_code": code,
        "finish_reason": FinishReason.ERROR,
        "journal_entries": crash_journal,
        "audit_drops": audit_recorder.drops,
        "outcome": outcome,
    }


def captain_failed(captain_state: Any) -> bool:
    """True when the captain run ended in FAILED phase."""
    return captain_state.phase is RunPhase.FAILED
