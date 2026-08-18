"""Apply a LoopDirective after a ReAct round (Return / Finalize / Rework / Continue)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from agentcore.core.error_codes import ErrorCode
from agentcore.core.types import ToolEffect
from agentcore.llm.errors import empty_response_error_context, empty_response_event_message
from agentcore.llm.profiles import ProfileParams
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.events import EventSink, FinishReason, error_event
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.runtime.loop_controller import LoopController
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry

from .ask_user_absorb import prepare_blocking_ask_user_tool_calls
from .directive import Continue, Finalize, LoopDirective, Return, Rework
from .escalation_gate import apply_escalation_gate
from .finalize import force_finalize
from .governance import (
    apply_circuit_breaker,
    govern_after_tools,
    maybe_restore_team_gate_tools,
    note_delegate_batches,
    resolve_openai_tool_defs,
)
from .outcome import RoundOutcome
from .round import apply_exit_ledger_ref_strip, apply_finish_guard_rework
from .segments import join_segments
from .tool_exec import execute_tools


def _captain_closing_honesty(
    content: str,
    controller: LoopController,
    *,
    promotion_ledger: Any = None,
) -> str:
    """CEO soft banners: softⅡ′ → write-ownership → cutoff/partial → cloud-web verify.

    Each enforce_* skips when an honesty banner prefix is already present.
    Cutoff latch（token_budget 等）真源=结构化 gaps/partial，不扩姿势 A 词表。
    """
    if not content:
        return content
    from agentcore.runtime.closing_posture import (
        downgrade_verdict_for_unresolved_write_ownership,
        enforce_ceo_mutation_honesty,
        enforce_cloud_web_verify_honesty,
        enforce_cutoff_closing_honesty,
        enforce_write_ownership_honesty,
        rewrite_stale_ask_after_dispatch,
    )

    downgrade_verdict_for_unresolved_write_ownership(
        promotion_ledger=promotion_ledger,
    )
    out = rewrite_stale_ask_after_dispatch(content)
    out = enforce_ceo_mutation_honesty(
        out,
        landing_succeeded=controller.landing_succeeded,
    )
    out = enforce_write_ownership_honesty(out)
    out = enforce_cutoff_closing_honesty(out)
    return enforce_cloud_web_verify_honesty(out)


@dataclass
class DirectiveApplyResult:
    """Result of applying one loop directive."""

    action: Literal["return", "continue", "rework"]
    # Populated when action == "return"
    content: str = ""
    reasoning: str = ""
    usage: TokenUsage | None = None
    rounds: int = 0
    # Mutated state the loop must adopt on continue/rework
    final_content: str = ""
    final_reasoning: str = ""
    total_usage: TokenUsage | None = None
    finish_guard_reworks: int = 0
    tool_defs: list[dict[str, Any]] | None = None
    tool_defs_changed: bool = False


async def apply_loop_directive(
    *,
    directive: LoopDirective,
    outcome: RoundOutcome,
    messages: list[LLMMessage],
    llm: OpenAICompatibleProvider,
    tools: ToolRegistry,
    tool_context: ToolContext,
    sink: EventSink,
    profile: ProfileParams,
    active_model: str | None,
    base_model: str,
    allowed_tool_names: list[str] | None,
    disabled_tools: set[str],
    emit_content: Callable[[str], None],
    emit_reasoning: Callable[[str], None],
    emit_reset: Callable[[str], None],
    final_content: str,
    final_reasoning: str,
    total_usage: TokenUsage,
    round_idx: int,
    run_id: str,
    role: str,
    finish_override_sink: list[FinishReason] | None,
    approval_gate: ApprovalGate | None,
    citation_sink: list[dict[str, Any]] | None,
    annotate_citations: bool,
    turn_evidence_ledger: EvidenceLedgerCore | None,
    ledger_registrant: str,
    gate_escalation_sink: list[dict[str, Any]] | None,
    controller: LoopController,
    content_before_round: str,
    finish_guard_reworks: int,
    files_expected: bool = False,
    form_prose: bool = False,
) -> DirectiveApplyResult:
    """Dispatch Return / Finalize / Rework / Continue for one round."""
    match directive:
        case Return(finish_reason=fr, extra_content=extra):
            if outcome.llm_failed:
                sink.emit(
                    error_event(
                        outcome.error_code or "",
                        outcome.error_message or "",
                        context=outcome.error_context,
                    )
                )
            elif fr is FinishReason.DEGRADED:
                # Sole user-facing surface for empty-response degraded: SSE error
                # with LLM_EMPTY_RESPONSE (not LLM_ERROR). Clients suppress the
                # finish chip when this card is present — finish_reason stays
                # degraded for metrics. Raw SSE tail stays in llm.empty_response
                # logs; context carries diagnosis / body_kind / base_url only.
                err_ctx = empty_response_error_context(
                    diagnosis=outcome.empty_diagnosis,
                    raw_preview=outcome.empty_raw_preview,
                    base_url=outcome.provider_base_url,
                )
                sink.emit(
                    error_event(
                        ErrorCode.LLM_EMPTY_RESPONSE,
                        empty_response_event_message(outcome.empty_diagnosis),
                        context=err_ctx,
                    )
                )
            if fr is not None and finish_override_sink is not None:
                finish_override_sink.append(fr)
            content = join_segments(final_content, extra) if extra else final_content
            # Q3：回炉耗尽后仍非法的 #rN —— 剥离放行 + 观测（禁止静默）。
            if content and turn_evidence_ledger is not None:
                content = apply_exit_ledger_ref_strip(
                    content,
                    turn_evidence_ledger=turn_evidence_ledger,
                    emit_reset=emit_reset,
                    emit_content=emit_content,
                    run_id=run_id,
                )
            # CEO soft banners：软Ⅱ′零写盘假改 + 云端装包拒仍称验绿 → 仅加横幅，不丢稿不拒发。
            if role == "captain" and content:
                content = _captain_closing_honesty(
                    content,
                    controller,
                    promotion_ledger=tool_context.promotion_ledger,
                )
            # LLM 讲不出话但已有结构化产出：把降级正文推上直播气泡（此前从未 stream）。
            if (
                outcome.llm_failed
                and role == "captain"
                and content
                and not (outcome.content or "").strip()
                and not (content_before_round or "").strip()
            ):
                emit_content(content)
            return DirectiveApplyResult(
                action="return",
                content=content,
                reasoning=final_reasoning,
                usage=total_usage,
                rounds=round_idx + 1,
            )
        case Finalize(reason=reason, finish_reason=fr):
            if fr is not None and finish_override_sink is not None:
                finish_override_sink.append(fr)
            # Mid-loop zero_write → DEGRADED + raised「Worker 因零写…」已退役。
            # Hard-ceiling thrashing still uses ceiling.record_thrashing_backstop.
            # 08-08 定案①：validation thrash 早停也要向上交缺口（勿重做 e94 PARTIAL）。
            if reason == "validation_thrash" and role == "worker":
                from agentcore.runtime.engine.ceiling import record_thrashing_backstop

                record_thrashing_backstop(
                    run_id=run_id,
                    agent_id=tool_context.agent_id,
                    question=(
                        "Worker 因同类参数/契约错误连撞已早停，"
                        "交付可能不完整——请续派或换策略补缺口。"
                    ),
                    evidence=f"validation_thrash: rounds={round_idx + 1}",
                    sink=sink,
                    gate_escalation_sink=gate_escalation_sink,
                    source="validation_thrash",
                )
            finalize_allowed = allowed_tool_names
            (
                final_content,
                final_reasoning,
                total_usage,
                rounds,
                coordination,
            ) = await force_finalize(
                messages=messages,
                llm=llm,
                profile=profile,
                active_model=active_model or base_model,
                tools=tools,
                allowed_tool_names=finalize_allowed,
                disabled_tools=disabled_tools,
                emit_content=emit_content,
                emit_reasoning=emit_reasoning,
                final_content=final_content,
                final_reasoning=final_reasoning,
                total_usage=total_usage,
                rounds=round_idx + 1,
                reason=reason,
                run_id=run_id,
                on_reset=emit_reset,
                outstanding_tool_failures=controller.outstanding_tool_failures(),
                files_expected=files_expected,
                form_prose=form_prose,
                workspace_channel_dead=controller.workspace_channel_dead,
            )
            if coordination is not None and coordination.kind == "coordination_tools":
                if coordination.content:
                    final_content = join_segments(final_content, coordination.content)
                    # Update point 3/3 (G4): mirror before tools may suspend.
                    if role == "captain":
                        from agentcore.runtime.engine.loop import sync_captain_loop_mirror

                        sync_captain_loop_mirror(final_content=final_content)
                if coordination.reasoning:
                    final_reasoning += coordination.reasoning
                tool_calls, folded = prepare_blocking_ask_user_tool_calls(
                    coordination.tool_calls or [],
                    coordination.content or "",
                )
                if role == "captain":
                    from agentcore.runtime.engine.loop import sync_captain_loop_mirror

                    sync_captain_loop_mirror(ask_user_content_folded=folded)
                messages.append(
                    LLMMessage(
                        role="assistant",
                        content=coordination.content or None,
                        tool_calls=tool_calls,
                        reasoning_content=coordination.reasoning or None,
                    )
                )
                from agentcore.runtime.engine.tool_clear import apply_file_read_clear_state

                tool_context = apply_file_read_clear_state(
                    tool_context,
                    messages,
                    investigation_tools=controller.investigation_tool_names,
                )
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
                    allowed_tool_names=finalize_allowed,
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
                if terminal is not None:
                    usage_meta = terminal.metadata or {}
                    total_usage = total_usage + TokenUsage(
                        input_tokens=usage_meta.get("input_tokens", 0),
                        output_tokens=usage_meta.get("output_tokens", 0),
                        reasoning_tokens=usage_meta.get("reasoning_tokens", 0),
                        cache_hit_tokens=usage_meta.get("cache_hit_tokens", 0),
                        cache_miss_tokens=usage_meta.get("cache_miss_tokens", 0),
                    )
                    if (
                        terminal.effect is ToolEffect.SUSPEND
                        and finish_override_sink is not None
                    ):
                        finish_override_sink.append(FinishReason.PAUSED)
                    return DirectiveApplyResult(
                        action="return",
                        content=_captain_closing_honesty(
                            join_segments(final_content, terminal.final_text or ""),
                            controller,
                            promotion_ledger=tool_context.promotion_ledger,
                        )
                        if role == "captain"
                        else join_segments(final_content, terminal.final_text or ""),
                        reasoning=final_reasoning,
                        usage=total_usage,
                        rounds=rounds,
                    )
                controller.record(attempts)
                note_delegate_batches(controller, tool_calls, attempts)
                gate_restored = maybe_restore_team_gate_tools(
                    controller, disabled_tools=disabled_tools, attempts=attempts
                )
                tool_defs = resolve_openai_tool_defs(
                    tools, finalize_allowed, disabled_tools
                )
                breaker = apply_circuit_breaker(
                    controller,
                    messages=messages,
                    run_id=run_id,
                    round_idx=round_idx,
                    disabled_tools=disabled_tools,
                )
                if breaker.refresh_tool_defs or gate_restored:
                    tool_defs = resolve_openai_tool_defs(
                        tools, finalize_allowed, disabled_tools
                    )
                from agentcore.runtime.runs.cutoff import worker_keeps_notes_in_wind_down

                _ = govern_after_tools(
                    outcome=RoundOutcome(
                        content=coordination.content,
                        reasoning=coordination.reasoning,
                        usage=coordination.usage,
                        tool_calls=coordination.tool_calls,
                        tool_results=tool_results,
                        attempts=attempts,
                    ),
                    controller=controller,
                    messages=messages,
                    round_idx=round_idx,
                    run_id=run_id,
                    breaker_message=breaker.message,
                    role=role,
                    disabled_tools=disabled_tools,
                    investigation_tools=controller.investigation_tool_names,
                    keep_notes=worker_keeps_notes_in_wind_down(
                        available=set(tools.names),
                        allowed=(
                            list(finalize_allowed)
                            if finalize_allowed is not None
                            else None
                        ),
                    ),
                )
                return DirectiveApplyResult(
                    action="continue",
                    final_content=final_content,
                    final_reasoning=final_reasoning,
                    total_usage=total_usage,
                    tool_defs=tool_defs,
                    tool_defs_changed=True,
                    finish_guard_reworks=finish_guard_reworks,
                )
            return DirectiveApplyResult(
                action="return",
                content=(
                    _captain_closing_honesty(
                        final_content,
                        controller,
                        promotion_ledger=tool_context.promotion_ledger,
                    )
                    if role == "captain"
                    else final_content
                ),
                reasoning=final_reasoning,
                usage=total_usage,
                rounds=rounds,
            )
        case Rework():
            final_content, finish_guard_reworks = apply_finish_guard_rework(
                messages=messages,
                emit_reset=emit_reset,
                final_content=final_content,
                content_before_round=content_before_round,
                round_idx=round_idx,
                run_id=run_id,
                annotate_citations=annotate_citations,
                citation_sink=citation_sink,
                finish_guard_reworks=finish_guard_reworks,
                turn_evidence_ledger=turn_evidence_ledger,
                promotion_ledger=tool_context.promotion_ledger,
            )
            return DirectiveApplyResult(
                action="rework",
                final_content=final_content,
                final_reasoning=final_reasoning,
                total_usage=total_usage,
                finish_guard_reworks=finish_guard_reworks,
            )
        case Continue():
            return DirectiveApplyResult(
                action="continue",
                final_content=final_content,
                final_reasoning=final_reasoning,
                total_usage=total_usage,
                finish_guard_reworks=finish_guard_reworks,
            )
    # Exhaustiveness: LoopDirective is a closed union; match covers all arms.
    raise TypeError(f"unknown loop directive: {type(directive)!r}")
