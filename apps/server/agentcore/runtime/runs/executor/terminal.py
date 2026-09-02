"""AGENT-node salvage / cancel / terminal RunState builders."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.llm.pricing import calculate_cost
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.runtime.events import (
    FinishReason,
    run_cancelled,
    run_completed,
    run_failed,
)
from agentcore.runtime.runs.contract import (
    ContractVerdict,
    handoff_expectation_met,
    has_salvageable_half_product,
    synthesize_debrief,
    worker_expects_handoff,
)
from agentcore.runtime.runs.executor.env import AgentExecutorEnv
from agentcore.runtime.runs.executor.hooks import _stamp_retrieval_evidence_gap
from agentcore.runtime.runs.executor.identities import LeadSubteam
from agentcore.runtime.runs.executor.shared import (
    _apply_cutoff_reasons,
    _apply_finish_interrupt,
    _delivery_gaps_from_warnings,
    _priced_failure,
)
from agentcore.runtime.runs.file_acceptance import (
    build_file_acceptance,
    path_rejections_from_contract_messages,
)
from agentcore.runtime.runs.landing_product import filter_product_landing_paths
from agentcore.runtime.runs.salvage import (
    cancelled_state_from_salvage,
    content_from_transcript,
    freeze_partial_transcript,
    try_salvage_session,
)
from agentcore.runtime.runs.serialize import (
    debrief_from_transcript,
    escalations_from_transcript,
    file_products_from_transcript,
    files_touched_from_transcript,
    merge_file_products,
)
from agentcore.runtime.runs.types import ContextBlock, RunPhase, RunSpec, RunState
from agentcore.tools.file_products import FileProduct

logger = get_logger(__name__)


def build_terminal_run_state(
    env: AgentExecutorEnv,
    spec: RunSpec,
    agent_id: str,
    *,
    messages: list[LLMMessage],
    content: str,
    reasoning: str,
    verdict: ContractVerdict,
    deliverable: Any,
    product_landing_artifacts: list[str] | None,
    resolutions: dict[str, dict[str, Any]],
    gate_escalations: list[dict[str, Any]],
    worker_citations: list[dict],
    priced_model: str,
    run_usage: TokenUsage,
    run_rounds: int,
    duration_ms: int,
    finish_override: list[FinishReason],
    cutoff_reasons: list[str],
    tool_failures: list[dict],
    write_pass_used: bool,
    received_blocks: list[ContextBlock],
    tool_ctx: Any,
    runtime_file_products: list[FileProduct] | None = None,
) -> RunState:
    """Build COMPLETED terminal RunState (contract misses stay warnings) + emit."""
    _ = (write_pass_used, product_landing_artifacts)
    usage = run_usage.as_dict()
    cost = asdict(calculate_cost(priced_model, run_usage))
    # Upward escalations this worker raised (escalate tool calls), harvested
    # once from the transcript and carried on BOTH terminal states — a worker
    # that flags a blocker then fails its contract should still surface that
    # blocker to the CEO. 阻塞式求决策: fold each blocking escalate's resolution
    # (answer / timeout) in by question, so CEO synthesis knows which were already
    # settled with the user and must not be re-asked (设计 §4.5/§4.7).
    escalations = escalations_from_transcript(messages)
    for esc in escalations:
        settled = resolutions.get(esc.get("question", ""))
        if settled is not None:
            esc["status"] = settled["status"]
            esc["answer"] = settled["answer"]
    # Merge Escalation Gate scheme-layer signals (dedupe by question).
    seen_questions = {e.get("question", "") for e in escalations}
    for gate_esc in gate_escalations:
        q = gate_esc.get("question", "")
        if q and q not in seen_questions:
            escalations.append(gate_esc)
            seen_questions.add(q)
    # 完工交接简报: harvest the worker's structured brief from its ``handoff`` tool call
    # (best-effort; None when it finished without one) so downstream dep injection / CEO
    # synthesis read the author's own 结论 + 建议下一步 instead of re-deriving them from
    # prose. Contract misses stay COMPLETED with warnings and still carry this brief.
    # Nodes that expect a handoff (has dependents) but still lack a minimum-quality
    # brief get an engine-synthesized degraded debrief so CEO / delivery_status can
    # see「汇报不完整」. Only when salvageable half-product (body / disk / qualified
    # brief) — empty inventory must not mint an empty ``degraded_synth``. Leaves
    # are not gated: missing brief stays missing (CEO reads body or landed paths).
    debrief = debrief_from_transcript(messages)
    products = merge_file_products(
        file_products_from_transcript(messages),
        runtime_file_products or (),
    )
    touched = [p.path for p in products]
    author_brief = debrief
    expects_handoff = worker_expects_handoff(env.plan, spec.run_id)
    can_synth = has_salvageable_half_product(content, touched, author_brief)
    if (
        expects_handoff
        and not handoff_expectation_met(debrief)
        and can_synth
    ):
        debrief = synthesize_debrief(content, touched)
        logger.info(
            "handoff.degraded_synth",
            run_id=spec.run_id,
            had_author_brief=author_brief is not None,
        )
    # 队员卡片/简报跟对话成稿同一口径：不剥 search-only / 未登记号。
    # 落盘成文仍走文件合同 ``citation_quality_reworks``。
    path_rej = path_rejections_from_contract_messages(
        [*verdict.failures, *verdict.soft_failures]
    )
    # Soft-accept / clean complete: still surface an interrupted LLM finish so CEO
    # synthesis sees the gap (files may be on disk but handoff missing/thin).
    warnings = [] if verdict.ok else [
        *verdict.failures,
        *verdict.soft_failures,
        *verdict.visual_failures,
    ]
    if verdict.ok and verdict.warnings:
        # Placeholder / unverified-content notes already on the verdict.
        warnings = list(verdict.warnings)
    elif verdict.warnings:
        warnings = [*warnings, *verdict.warnings]
    warnings, debrief = _apply_finish_interrupt(
        finish_override,
        warnings=warnings,
        debrief=debrief,
        content=content,
        files_touched=touched,
        run_id=spec.run_id,
    )
    warnings = _apply_cutoff_reasons(cutoff_reasons, warnings=warnings)
    # degraded_handoff 一律 warning，不抬硬缺口。
    delivery_gaps = _delivery_gaps_from_warnings(
        warnings,
        debrief,
        files_landed=bool(touched),
        stamped_rows=verdict.warning_rows,
    )
    # 成篇质量：有下游 + 相对合同未满足且无成篇 prose 落盘 → 失败（与 handoff 同口径）。
    # 认 tool_ctx.landed_artifact_kinds（跨 replace 存活）；勿用 has_landed_files /
    # 泛 files_touched（骨架落盘会误豁免）。地板固定非空（不跟合同字数字段）。
    # 非 prose：正文空但 debrief.summary 在 → 先升格再验地板。
    # prose + 有下游：便条不算交付正文，禁止升格顶地板。
    from agentcore.runtime.runs.research_quality import (
        brief_may_satisfy_body_floor,
        promote_brief_to_deliverable,
        upstream_body_floor_satisfied,
    )

    body_chars = len((content or "").strip())
    floor = 0
    form = deliverable.form if deliverable is not None else None
    if (
        body_chars == 0
        and debrief
        and brief_may_satisfy_body_floor(deliverable_form=form)
        and not spec.research_then_draft
    ):
        brief_summary = str((debrief or {}).get("summary") or "").strip()
        if brief_summary:
            candidate = promote_brief_to_deliverable(
                brief_summary,
                (debrief or {}).get("key_points"),
            )
            if upstream_body_floor_satisfied(
                body_chars=len(candidate),
                landed_artifact_kinds=tool_ctx.landed_artifact_kinds,
                min_body_chars=floor,
            ):
                content = candidate
                body_chars = len(candidate)
    # 空交 / 未落盘不再把节点打成 FAILED。
    # The worker's terminal RunState is journaled at the ``execute`` choke point
    # below (run_final_fact — covers COMPLETED *and* FAILED in one place), so resume
    # re-seeds it from facts not the旁路 frame (执行级事件溯源 Phase 2 ⑥).
    env.sink.emit(
        run_completed(
            spec.run_id,
            agent_id,
            # 交接简报单一源: the summary IS the worker's authored 结论 (best-effort "" when
            # it wrote none — the full deliverable is persisted + shown either way), never a
            # truncation; the structured debrief rides alongside for the run-detail card.
            output_summary=(debrief or {}).get("summary", ""),
            duration_ms=duration_ms,
            # 阶段1 scheduled runs are all delegated workers → member row;
            # the already-priced usage/cost light up the payroll live.
            role="member",
            model=priced_model,
            usage=usage,
            cost=cost,
            debrief=debrief,
            output_files=touched or None,
            gaps=delivery_gaps or None,
            execution_id=env.execution_id,
        )
    )
    return _stamp_retrieval_evidence_gap(
        RunState(
            phase=RunPhase.COMPLETED,
            content=content,
            reasoning=reasoning,
            warnings=warnings,
            delivery_gaps=delivery_gaps,
            escalations=escalations,
            debrief=debrief,
            citations=worker_citations,
            model=priced_model,
            duration_ms=duration_ms,
            rounds=run_rounds,
            files_touched=touched,
            file_acceptance=build_file_acceptance(
                touched,
                phase=RunPhase.COMPLETED,
                path_rejections=path_rej,
                products=products,
            ),
            tool_failures=list(tool_failures),
            usage=usage,
            cost=cost,
            transcript=messages,
            received_context=received_blocks,
        ),
        tool_ctx,
        search_policy=spec.search_policy or "",
    )


def cancel_reason_from_exc(exc: BaseException | None) -> str:
    """Wire ``run_cancelled.reason`` — same mapping as the Wave member path.

    ``cancel(arg)`` is the only carrier of WHY the task was killed: a hard-timeout
    kill says ``worker_timeout``, never「已改方向」.
    """
    if not isinstance(exc, asyncio.CancelledError):
        return "stop"
    arg = str(exc.args[0]) if exc.args else ""
    if arg in ("redirect", "worker_timeout"):
        return arg
    if arg == "user_stop":
        return "user_stop"
    return "stop"


def handle_agent_node_cancel(
    env: AgentExecutorEnv,
    spec: RunSpec,
    agent_id: str,
    e: asyncio.CancelledError,
    *,
    messages: list[LLMMessage],
    streamed_content: list[str],
    inflight: list[TokenUsage] | None = None,
    run_usage: TokenUsage | None = None,
    run_rounds: int = 0,
    priced_model: str | None = None,
) -> RunState | None:
    """Quadruple cancel: redirect / worker_timeout → salvage CANCELLED;
    user_stop → CANCELLED absorb; stop → emit then re-raise.

    Returns a RunState on redirect/worker_timeout/user_stop absorb; returns None when
    caller must ``raise``.
    Absorbed cancels fold already-spent usage (completed rounds + in-flight pass)
    onto the CANCELLED state — same honesty as the exception / FAILED path — so
    ``cancel_worker`` / run-stop does not evaporate escalations or member billing.

    The cancel ``arg`` is the ONLY carrier of WHY the task was killed, so it maps
    1:1 onto the wire ``run_cancelled.reason``: a hard-timeout kill says
    ``worker_timeout``, never「已改方向」.
    """
    cancel_reason = cancel_reason_from_exc(e)
    env.sink.emit(
        run_cancelled(
            spec.run_id,
            agent_id,
            reason=cancel_reason,
            execution_id=env.execution_id,
        )
    )
    if cancel_reason in ("redirect", "worker_timeout"):
        # Fold live streamed draft into messages when the ReAct pass was cut
        # before the final assistant turn was appended (用户已看见的一半产出).
        # A timeout kill salvages exactly like a redirect: the partial 现场 is what
        # the CEO 续派s from — only the recorded cause differs.
        draft = "".join(streamed_content).strip()
        salvage_msgs = list(messages)
        if draft and not any(m.role in ("assistant", "tool") for m in salvage_msgs):
            salvage_msgs.append(LLMMessage(role="assistant", content=draft))
        session = try_salvage_session(spec=spec, messages=salvage_msgs)
        usage_acc = run_usage or TokenUsage()
        if inflight:
            usage_acc = usage_acc + inflight[0]
        salvage_fields = {
            "run_id": spec.run_id,
            "salvage": session is not None,
            "transcript_len": len(session.transcript) if session else 0,
            "streamed_chars": len(draft),
            "tokens": usage_acc.total_tokens,
        }
        if cancel_reason == "redirect":
            logger.info("run.redirect_cancelled", **salvage_fields)
        else:
            logger.info("run.timeout_cancelled", **salvage_fields)
        return cancelled_state_from_salvage(
            session,
            error="redirected" if cancel_reason == "redirect" else "worker_timeout",
            usage=usage_acc,
            model=priced_model,
            rounds=run_rounds,
        )
    if cancel_reason == "user_stop":
        # Absorb like redirect so the wave continues, but do not salvage for hot
        # continue — run-stop never triggers revision / ``_redir``.
        usage_acc = run_usage or TokenUsage()
        if inflight:
            usage_acc = usage_acc + inflight[0]
        logger.info(
            "run.user_stop_cancelled",
            run_id=spec.run_id,
            tokens=usage_acc.total_tokens,
        )
        return cancelled_state_from_salvage(
            None,
            error="user_stopped",
            usage=usage_acc,
            model=priced_model,
            rounds=run_rounds,
        )
    return None


def handle_agent_node_exception(
    env: AgentExecutorEnv,
    spec: RunSpec,
    agent_id: str,
    e: BaseException,
    *,
    start: float,
    messages: list[LLMMessage],
    inflight: list[TokenUsage],
    run_usage: TokenUsage,
    run_rounds: int,
    priced_model: str | None,
    product_landing_artifacts: list[str] | None,
    tool_ctx: Any | None,
) -> RunState:
    """Price partial spend and return FAILED RunState for infra/call failures."""
    import time

    duration_ms = int((time.monotonic() - start) * 1000)
    usage_acc = run_usage
    # Bill the rounds that completed before the failure: finished attempts are
    # already in run_usage; an in-flight pass that raised left its spend in
    # ``inflight`` (B-deep 失败计费).
    if inflight:
        usage_acc = usage_acc + inflight[0]
    # 确定性失败区分 (BL-6): ``run_error_signal`` reads ``llm_failure_class``
    # (rate-limit stays transient after the leaf flips ``exc.retryable``).
    # Closed httpx client is terminal — retrying the same closed client just
    # multiplies llm.call_failed / run.failed.
    from agentcore.runtime.runs.error_signal import run_error_signal

    signal = run_error_signal(e)
    e = signal.exc
    retryable = signal.retryable
    logger.error(
        "run.failed",
        run_id=spec.run_id,
        error=str(e),
        retryable=retryable,
        error_code=signal.error_code,
        retry_after=signal.retry_after,
        exc_info=True,
    )
    frozen = freeze_partial_transcript(messages) if messages else []
    product_landed = False
    if frozen:
        touched = files_touched_from_transcript(frozen)
        product_landed = (
            len(filter_product_landing_paths(touched, product_landing_artifacts)) > 0
        )
    env.sink.emit(
        run_failed(
            spec.run_id,
            agent_id,
            str(e),
            failure_kind="call",
            execution_id=env.execution_id,
            product_landed=product_landed or None,
            error_code=signal.error_code,
            retryable=retryable,
            retry_after=signal.retry_after,
        )
    )
    failed = _priced_failure(
        str(e),
        model=priced_model,
        usage=usage_acc,
        rounds=run_rounds,
        duration_ms=duration_ms,
        retryable=retryable,
        error_code=signal.error_code or "",
        retry_after=signal.retry_after,
        transcript=frozen or None,
        content=content_from_transcript(frozen) if frozen else "",
    )
    if tool_ctx is not None:
        return _stamp_retrieval_evidence_gap(
            failed, tool_ctx, search_policy=spec.search_policy or ""
        )
    return failed


async def dispose_agent_node(
    spec: RunSpec,
    lead_subteam: LeadSubteam | None,
) -> None:
    """Browser unbind + lead subteam dispose (finally path)."""
    # Browser B: release this run's session bind so a later worker omitting
    # session_id can reuse the conversation's unbound unique/active live tab
    # (complete / fail / cancel — including redirect cancel + re-raise stop).
    try:
        from agentcore.runtime.browser.registry import default_browser_session_registry

        default_browser_session_registry().unbind_run(spec.run_id)
    except Exception:  # noqa: BLE001 - teardown must not fail the worker path
        logger.warning("browser.unbind_run_failed", run_id=spec.run_id)
    # 堵漏账: if this lead opened a sub-plan at a 波边界 but its react loop ended
    # without a final replan (answered directly / hit MAX_ROUNDS / raised), the held
    # sub-team spend still sits in the child delegate's _supervised. Fold it in now —
    # BEFORE the parent drive's absorb_children merges this child's ledger — so no
    # sub-team usage is stranded unbilled. No-op when nothing is paused; best-effort,
    # and in a finally so it runs on the success, MAX_ROUNDS, and exception paths alike.
    if lead_subteam is not None:
        await lead_subteam.dispose()
