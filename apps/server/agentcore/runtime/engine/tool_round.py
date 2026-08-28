"""Tool-call arm of a ReAct round: execute tools, absorb ask_user, govern."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.events import EventSink, FinishReason
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.runtime.loop_controller import LoopController
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry

from .ask_user_absorb import (
    absorb_blocking_ask_user_content,
    prepare_blocking_ask_user_tool_calls,
)
from .directive import LoopDirective, Return
from .escalation_gate import apply_escalation_gate
from .governance import (
    apply_circuit_breaker,
    govern_after_tools,
    note_delegate_batches,
    resolve_openai_tool_defs,
)
from .outcome import RoundOutcome
from .tool_exec import execute_tools


@dataclass
class ToolRoundResult:
    """Outcome of the tool-call arm for one ReAct round."""

    outcome: RoundOutcome
    directive: LoopDirective
    final_content: str
    total_usage: TokenUsage
    # Set when the round continues after tools (circuit breaker may have refreshed).
    # ``None`` means leave the caller's ``tool_defs`` unchanged (terminal Return).
    tool_defs: list[dict[str, Any]] | None = None
    tool_defs_changed: bool = False


async def handle_tool_calls_round(
    *,
    outcome: RoundOutcome,
    messages: list[LLMMessage],
    tools: ToolRegistry,
    tool_context: ToolContext,
    sink: EventSink,
    approval_gate: ApprovalGate | None,
    citation_sink: list[dict[str, Any]] | None,
    annotate_citations: bool,
    turn_evidence_ledger: EvidenceLedgerCore | None,
    ledger_registrant: str,
    run_id: str,
    role: str,
    gate_escalation_sink: list[dict[str, Any]] | None,
    deliverable_only: bool,
    on_reset: Callable[[str], None] | None,
    emit_reset: Callable[[str], None],
    content_before_round: str,
    final_content: str,
    round_result_content: str,
    total_usage: TokenUsage,
    controller: LoopController,
    allowed_tool_names: list[str] | None,
    disabled_tools: set[str],
    round_idx: int,
) -> ToolRoundResult:
    """Execute tools for a round that produced tool calls; return next directive."""
    tool_calls, content_folded = prepare_blocking_ask_user_tool_calls(
        outcome.tool_calls,
        outcome.content or "",
    )
    from agentcore.runtime.engine.loop import sync_captain_loop_mirror

    sync_captain_loop_mirror(ask_user_content_folded=content_folded)
    messages.append(
        LLMMessage(
            role="assistant",
            content=outcome.content or None,
            tool_calls=tool_calls,
            reasoning_content=outcome.reasoning or None,
        )
    )
    # Stamp same-round prose length so handoff can log deliverable body_chars
    # (distinct from summary ``chars``).
    tool_context = replace(
        tool_context,
        round_content_chars=len(outcome.content or ""),
    )
    # R1: sync projected-window verbatim / fully-cleared file_read ledger before tools run.
    from agentcore.runtime.engine.tool_clear import apply_file_read_clear_state

    tool_context = apply_file_read_clear_state(
        tool_context,
        messages,
        investigation_tools=controller.investigation_tool_names,
    )
    # Team-gate / circuit-breaker strip from defs; also deny at execute so a
    # scripted or rogue tool_call cannot land after hard-stop.
    exec_allowed = allowed_tool_names
    if disabled_tools:
        base = list(tools.names) if allowed_tool_names is None else list(allowed_tool_names)
        exec_allowed = [n for n in base if n not in disabled_tools]
    tool_results, terminal, attempts = await execute_tools(
        tool_calls,
        tools,
        tool_context,
        sink,
        approval_gate=approval_gate,
        citation_sink=citation_sink,
        annotate_citations=annotate_citations,
        turn_evidence_ledger=turn_evidence_ledger,
        ledger_registrant=ledger_registrant,
        run_id=run_id,
        role=role,
        allowed_tool_names=exec_allowed,
    )
    messages.extend(tool_results)
    if gate_escalation_sink is not None and role == "worker":
        apply_escalation_gate(
            attempts=attempts,
            tool_results=tool_results,
            sink=sink,
            run_id=run_id,
            agent_id=tool_context.agent_id,
            gate_escalation_sink=gate_escalation_sink,
        )
    outcome = replace(
        outcome,
        tool_results=tool_results,
        attempts=attempts,
        terminal_handoff=(terminal.final_text or "") if terminal is not None else None,
    )

    if terminal is not None:
        if absorb_blocking_ask_user_content(
            messages=messages,
            tool_calls=tool_calls,
            attempts=attempts,
            terminal_effect=terminal.effect,
            emit_reset=emit_reset,
            content_folded=content_folded,
        ):
            # Prose folded into the ask_user card; roll bubble back (may be empty).
            # Do not engine-inject wait-confirm copy — the card is the pause face.
            final_content = content_before_round
        usage_meta = terminal.metadata or {}
        total_usage = total_usage + TokenUsage.from_call_meta(usage_meta)
        # 挂起即收口 (②): a SUSPEND terminal ended the turn at a durable
        # checkpoint awaiting /resume — NOT because an answer was produced.
        # Stamp FinishReason.PAUSED (via finish_override_sink) so the pipeline
        # emits a paused message_end and the persist tail parks the turn (the
        # frame is its record). INTERACT / HANDOFF carry their final_text and
        # finish on the default reason (finish_reason=None).
        paused = terminal.effect is ToolEffect.SUSPEND
        directive: LoopDirective = Return(
            finish_reason=FinishReason.PAUSED if paused else None,
            extra_content=outcome.terminal_handoff or "",
        )
        return ToolRoundResult(
            outcome=outcome,
            directive=directive,
            final_content=final_content,
            total_usage=total_usage,
        )

    # 交付正文只留最终交付、旁白入 journal (Fork-B): this round wrote prose
    # and then called a NON-terminal tool, so that prose is process
    # narration (a lead-in, or an acknowledgement of an injected
    # [系统提示] steer 的致谢寒暄), not deliverable. Roll it
    # back off final_content — it already streamed live + was journaled this
    # round (llm_call fact) — mirroring the finish_guard Rework rollback, so
    # only the FINAL answer round's text reaches the persisted product.
    #
    # Exception: when EVERY tool in the round failed, the prose was not a
    # successful lead-in to work the user already got — it is still the
    # product they saw (e.g. CEO 案件简介 before a rejected ``debate`` call).
    # Keep it so a later retry / pause cannot silently drop streamed body.
    #
    # Exception: CEO attached_inject closing round. After wait ate
    # ALL_COMPLETED the same-turn prose is the deliverable (终稿), not a
    # lead-in — even if a still-offered non-terminal tool (closed
    # ``update_synthesis`` returns success so as not to burn a retry)
    # runs in that round. Workers / debaters / mid-turn CEO narration
    # still roll back: skip only when the live bubble already holds
    # post-inject visible close (same predicate as harvest skip).
    if deliverable_only and round_result_content and not outcome.all_tools_failed:
        # A run whose LIVE display shares the deliverable channel (worker /
        # debater / revision: on_reset routes run_output_reset, and the card
        # replays from the message_final fact) must also clear the streamed
        # narration off its card, so 直播 == the rolled-back deliverable ==
        # 重载 (合成自 message_final) — the conformance invariant. The CEO
        # streams to a SEPARATE process timeline (on_reset is None): its
        # narration stays visible there (透明可见), only its persisted content
        # (messages.content, 旁路 conformance) is trimmed.
        from agentcore.runtime.coordination.session import (
            attached_inject_closed_visibly,
            resolve_coordination_session,
        )

        coord = resolve_coordination_session(tool_context.execution_id)
        attached_inject_closing = (
            on_reset is None
            and coord is not None
            and attached_inject_closed_visibly(coord)
        )
        if not attached_inject_closing:
            if on_reset is not None:
                emit_reset("narration")
            final_content = content_before_round
    controller.record(outcome.attempts)
    # Mark post-delegate mode if delegate was called
    note_delegate_batches(controller, tool_calls, outcome.attempts)
    # 工具面瘦身: after tools run, coordination / supervised yield may have appeared —
    # promote gated tools onto the registry before re-resolving OpenAI defs.
    from agentcore.runtime.resolve.ceo_surface import promote_coordination_surface_if_needed

    surface_changed = promote_coordination_surface_if_needed(tools)
    tool_defs = resolve_openai_tool_defs(tools, allowed_tool_names, disabled_tools)
    breaker = apply_circuit_breaker(
        controller,
        messages=messages,
        run_id=run_id,
        round_idx=round_idx,
        disabled_tools=disabled_tools,
    )
    # Honest finalize: keep system-prompt hard constraint in sync with the same
    # run-scoped failure tally the circuit breaker uses (compensated → cleared).
    from agentcore.runtime.tool_failures import sync_tool_failure_constraint_in_system

    sync_tool_failure_constraint_in_system(
        messages, controller.outstanding_tool_failures()
    )
    if breaker.refresh_tool_defs or surface_changed:
        tool_defs = resolve_openai_tool_defs(tools, allowed_tool_names, disabled_tools)
    from agentcore.runtime.runs.cutoff import worker_keeps_notes_in_wind_down

    keep_notes = worker_keeps_notes_in_wind_down(
        available=set(tools.names),
        allowed=list(allowed_tool_names) if allowed_tool_names is not None else None,
    )
    directive = govern_after_tools(
        outcome,
        controller,
        messages=messages,
        round_idx=round_idx,
        run_id=run_id,
        breaker_message=breaker.message,
        role=role,
        disabled_tools=disabled_tools,
        investigation_tools=controller.investigation_tool_names,
        keep_notes=keep_notes,
    )
    return ToolRoundResult(
        outcome=outcome,
        directive=directive,
        final_content=final_content,
        total_usage=total_usage,
        tool_defs=tool_defs,
        tool_defs_changed=True,
    )
