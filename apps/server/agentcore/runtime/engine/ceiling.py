"""Hard-ceiling termination: token budget or max_rounds force-finalize."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolEffect
from agentcore.llm.profiles import ProfileParams
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.events import EventSink, FinishReason, escalation_raised
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.runtime.loop_controller import LoopController
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry

from .ask_user_absorb import prepare_blocking_ask_user_tool_calls
from .escalation_gate import apply_escalation_gate
from .finalize import force_finalize
from .segments import join_segments
from .tool_exec import execute_tools

logger = get_logger(__name__)

# Thrashing signal source for hard-ceiling DEGRADED (mid-loop zero_write retired).
CEILING_BACKSTOP_SOURCE = "ceiling_backstop"


def thrashing_backstop_payload(
    *,
    question: str,
    evidence: str,
    source: str,
) -> dict[str, Any]:
    """Structured escalation row for thrashing DEGRADED (signal only — no auto replan).

    ``source`` must match the SSE ``run_escalation`` payload (e.g.
    ``ceiling_backstop`` / ``validation_thrash``).
    """
    return {
        "question": question,
        "assumption": "",
        "blocking": False,
        "kind": "normal",
        "source": source,
        "gate_kind": "normal",
        "evidence": evidence,
        "tool_name": "",
        "layer": "scheme",
    }


def record_thrashing_backstop(
    *,
    run_id: str,
    agent_id: str,
    question: str,
    evidence: str,
    sink: EventSink,
    gate_escalation_sink: list[dict[str, Any]] | None,
    source: str,
) -> None:
    """Append gate escalation + emit ``escalation_raised`` for a thrashing worker."""
    if gate_escalation_sink is not None:
        gate_escalation_sink.append(
            thrashing_backstop_payload(
                question=question, evidence=evidence, source=source
            )
        )
    sink.emit(
        escalation_raised(
            run_id,
            agent_id,
            question=question,
            assumption="",
            blocking=False,
            kind="normal",
            source=source,
        )
    )


async def ceiling_finalize(
    *,
    messages: list[LLMMessage],
    llm: OpenAICompatibleProvider,
    profile: ProfileParams,
    active_model: str | None,
    base_model: str,
    tools: ToolRegistry,
    allowed_tool_names: list[str] | None,
    disabled_tools: set[str],
    emit_content: Callable[[str], None],
    emit_reasoning: Callable[[str], None],
    emit_reset: Callable[[str], None],
    final_content: str,
    final_reasoning: str,
    total_usage: TokenUsage,
    ceiling_reason: str,
    round_idx: int,
    role: str,
    run_id: str,
    token_budget: int,
    controller: LoopController,
    tool_context: ToolContext,
    sink: EventSink,
    finish_override_sink: list[FinishReason] | None,
    approval_gate: ApprovalGate | None,
    citation_sink: list[dict[str, Any]] | None,
    annotate_citations: bool,
    turn_evidence_ledger: EvidenceLedgerCore | None,
    ledger_registrant: str,
    gate_escalation_sink: list[dict[str, Any]] | None,
    cutoff_reason_sink: list[str] | None = None,
    files_expected: bool = False,
    form_prose: bool = False,
) -> tuple[str, str, TokenUsage, int]:
    """Force-finalize after the round loop exits on a hard ceiling.

    Routes the finish by run health so an on-track worker delivers while a
    thrashing one is flagged DEGRADED + escalated (signal only — no auto replan).
    On-track ``token_budget`` still stamps ``cutoff_reason_sink`` so delivery_status
    / CEO gaps stay honest (不标 DEGRADED、不自动 replan).

    ``approval_gate`` / ``citation_sink`` / ``annotate_citations`` /
    ``turn_evidence_ledger`` / ``ledger_registrant`` are required (no defaults):
    收口轮仍可能调 GRANTABLE / 调研工具，本臂的 ``execute_tools`` 必须带上与孪生履约点
    (``directive_apply`` 的 Finalize 臂) 相同的审批闸与引用/台账汇聚通道。收口点转
    fail-closed 后，漏传 ``approval_gate`` 不再意味着 file_write 绕卡落盘，而是这一臂
    本该弹卡的调用整类被拒（该问却没人可问）——两种都是坏的，故签名不留默认值，让漏传
    在类型层面就是 ``TypeError``。
    """
    # Hard-ceiling termination: the token backstop broke the loop, or max_rounds
    # exhausted. Always force-finalize (杜绝死循环); route the finish by run health so an
    # on-track worker delivers its work while a thrashing one is flagged. 据审计: the
    # signal is SURFACED, not auto-actioned — there is no「升级→CEO 自动重分解」闭环; the
    # CEO may voluntarily replan off this signal.
    rounds_done = round_idx if ceiling_reason == "token_budget" else profile.max_rounds
    thrashing = role == "worker" and controller.is_thrashing()
    logger.warning(
        "engine.ceiling_finalize",
        reason=ceiling_reason,
        thrashing=thrashing,
        rounds=rounds_done,
        tokens=total_usage.total_tokens,
        token_budget=token_budget,
        run_id=run_id,
    )
    # C·掐断透明化：正轨 token 撞顶也要结构化原因码（与打转 DEGRADED 分流正交）。
    if (
        ceiling_reason == "token_budget"
        and role == "worker"
        and cutoff_reason_sink is not None
        and "token_budget" not in cutoff_reason_sink
    ):
        cutoff_reason_sink.append("token_budget")
    if thrashing:
        if finish_override_sink is not None:
            finish_override_sink.append(FinishReason.DEGRADED)
        ceiling_question = (
            f"Worker 到达硬顶（{ceiling_reason}）时仍在打转，"
            "已强制收口并交付当前产出——可能不完整。"
        )
        # 结构化落入 RunState.escalations（经 gate_escalation_sink → 执行器 harvest 合并去重），
        # 让 CEO 的 escalation 聚合真正看得到「到顶打转」这一条、可自愿重规划——不止 UI 横幅
        # （否则「升级了却没人接」）。kind=normal：纯上浮，不触发 wave 边界自动动作，对齐
        # 「不自动重分解、CEO 自愿决策」的设计取舍。
        record_thrashing_backstop(
            run_id=run_id,
            agent_id=tool_context.agent_id,
            question=ceiling_question,
            evidence=(
                f"{ceiling_reason}: tokens={total_usage.total_tokens}, "
                f"rounds={rounds_done}"
            ),
            sink=sink,
            gate_escalation_sink=gate_escalation_sink,
            source=CEILING_BACKSTOP_SOURCE,
        )
    final_content, final_reasoning, total_usage, rounds, coordination = await force_finalize(
        messages=messages,
        llm=llm,
        profile=profile,
        active_model=active_model or base_model,
        tools=tools,
        allowed_tool_names=allowed_tool_names,
        disabled_tools=disabled_tools,
        emit_content=emit_content,
        emit_reasoning=emit_reasoning,
        final_content=final_content,
        final_reasoning=final_reasoning,
        total_usage=total_usage,
        rounds=rounds_done,
        reason=ceiling_reason,
        run_id=run_id,
        on_reset=emit_reset,
        outstanding_tool_failures=controller.outstanding_tool_failures(),
        files_expected=files_expected,
        form_prose=form_prose,
        workspace_channel_dead=controller.workspace_channel_dead,
    )
    # CEO / captain：硬顶强制收口不得无条件姿势 A（finish_guard 被绕过）。
    # max_rounds / token_budget 对称；worker salvage 靠 finalize 注入的 ceiling_honesty_steer。
    if role != "worker" and ceiling_reason in ("max_rounds", "token_budget"):
        from agentcore.runtime.closing_posture import (
            downgrade_verdict_for_ceiling,
            enforce_ceiling_closing_honesty,
            note_cutoff_delivery_gap,
        )

        downgrade_verdict_for_ceiling(
            reason=ceiling_reason,
            promotion_ledger=tool_context.promotion_ledger,
        )
        if ceiling_reason == "token_budget":
            note_cutoff_delivery_gap()

        def _honest_close(text: str) -> str:
            return enforce_ceiling_closing_honesty(text, reason=ceiling_reason)
    else:

        def _honest_close(text: str) -> str:
            return text

    # force_finalize contract: when soft round returns tools, caller must execute.
    # Files workers may call file_write/handoff here — discarding would leave
    # form=files / artifacts unmet after we explicitly kept those tools on the surface.
    if coordination is not None and coordination.kind == "coordination_tools":
        if coordination.content:
            final_content = join_segments(final_content, coordination.content)
            # G4: mirror before tools may suspend (same update point as the twin).
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
            allowed_tool_names=allowed_tool_names,
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
        # No next round to govern here — record only so the run's terminal export
        # (tool_failure_facts / controller seed) still sees this last round's
        # attempts. The twin's post-tool governance (breaker / tool_defs /
        # govern_after_tools) steers the NEXT round and has no meaning past the ceiling.
        controller.record(attempts)
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
            return (
                _honest_close(join_segments(final_content, terminal.final_text or "")),
                final_reasoning,
                total_usage,
                rounds,
            )
    return _honest_close(final_content), final_reasoning, total_usage, rounds
