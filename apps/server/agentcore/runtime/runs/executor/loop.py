"""AGENT-node react+capture loop body + contract decision ladder orchestration.

Split from ``.node`` — pure move; consumed only by the node facade.
Retry predicates live in sibling modules.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.runtime.debate.speech_pipeline import research_then_draft
from agentcore.runtime.events import FinishReason
from agentcore.runtime.runs.constants import HANDOFF_TOOL_NAME
from agentcore.runtime.runs.contract import (
    ContractVerdict,
    check_contract,
    format_feedback,
    format_handoff_feedback,
    format_interrupted_pass_note,
    format_write_pass_feedback,
    handoff_expectation_met,
    is_zero_files_gap,
    worker_expects_handoff,
)
from agentcore.runtime.runs.executor.context import (
    _safe_index_files,
)
from agentcore.runtime.runs.executor.env import AgentExecutorEnv
from agentcore.runtime.runs.executor.retry import (
    _can_write_pass,
    _narrow_for_light_repair,
    _pass_max_rounds,
    _retry_token_budget,
    _wind_down_entered,
    bind_round_budget_on_begin,
    should_skip_contract_retry_for_budget,
    should_skip_full_contract_retry_for_round_ceiling,
    stamp_coord_round_budget,
)
from agentcore.runtime.runs.executor.setup import AgentNodePrepared
from agentcore.runtime.runs.executor.shared import _react_and_capture, _retry_message
from agentcore.runtime.runs.landing_product import filter_product_landing_paths
from agentcore.runtime.runs.retrieval_budget import rework_refill_slots
from agentcore.runtime.runs.serialize import (
    debrief_from_transcript,
    files_touched_from_transcript,
    landing_write_failure_kind,
)
from agentcore.runtime.runs.types import RunSpec

logger = get_logger(__name__)


@dataclass
class ContractLoopResult:
    """Outputs from the produce → check → retry loop for terminal builders."""

    content: str
    reasoning: str
    verdict: ContractVerdict
    worker_citations: list[dict]
    priced_model: str
    run_usage: TokenUsage
    run_rounds: int
    finish_override: list[FinishReason]
    cutoff_reasons: list[str]
    tool_failures: list[dict]
    write_pass_used: bool
    workspace_paths: list[str] | None
    product_landing_artifacts: list[str] | None
    runtime_file_products: list[Any]
    deliverable: Any
    tool_ctx: Any


async def run_contract_loop(
    env: AgentExecutorEnv,
    spec: RunSpec,
    agent_id: str,
    prepared: AgentNodePrepared,
    *,
    messages: list[LLMMessage],
    streamed_content: list[str],
    inflight: list[TokenUsage],
    gate_escalations: list[dict[str, Any]],
    run_usage_box: list[TokenUsage],
    run_rounds_box: list[int],
) -> ContractLoopResult:
    """Produce → check contract → re-prompt shortfalls until accept / exhaust.

    ``run_usage_box`` / ``run_rounds_box`` are single-element mutable accumulators
    shared with the caller's exception path (B-deep 失败计费) — same hoist semantics
    as the pre-split locals.
    """
    deliverable = prepared.deliverable
    profile = prepared.profile
    priced_model = prepared.priced_model
    request_model = prepared.request_model
    tool_ctx = prepared.tool_ctx
    worker_tools = prepared.worker_tools
    allowed_tools = prepared.allowed_tools
    files_expected = prepared.files_expected
    report_delivery = prepared.report_delivery
    product_landing_artifacts = prepared.product_landing_artifacts
    short_write_posture = prepared.short_write_posture
    tighten_verify_exec_thrash = prepared.tighten_verify_exec_thrash
    token_ceiling = prepared.token_ceiling
    attempts = prepared.attempts

    run_usage = run_usage_box[0]
    run_rounds = run_rounds_box[0]
    # Pass-local round counter for the CEO idle brief (busy-channel used/limit).
    # Not injected into the worker window — rounds are an engine ceiling.
    # ``run_rounds`` still accumulates every produce pass (billing / exception).
    pass_round_used = [0]
    pass_round_limit = [profile.max_rounds]

    def _live_tokens_spent() -> int:
        extra = inflight[0].fuse_tokens if inflight else 0
        return run_usage_box[0].fuse_tokens + extra

    on_round_begin = bind_round_budget_on_begin(
        pass_round_used,
        pass_round_limit,
        run_id=spec.run_id,
        tokens_spent_of=_live_tokens_spent,
    )

    # Produce → check contract → re-prompt with the specific shortfalls.
    # This content-quality retry is intentionally separate from the
    # scheduler's infra-failure retry (RunPolicy.on_failure): they answer
    # different questions and must not be conflated.
    content = ""
    # Keep the last non-empty prose across contract retries. A handoff-gate
    # correction often only calls ``handoff`` (empty streamed content); without
    # retention the prior ~合格正文 is wiped and check_contract mis-fires「产出为空」.
    retained_content = ""
    # The worker's full thinking from the LAST attempt (parallel to
    # ``content``, which each attempt overwrites): carried onto the terminal
    # RunState → its ``message_final`` fact so resume / reload rebuild the
    # worker's 思考全文 from the journal, not from the (being-retired)
    # ``run_reasoning_delta`` stream (执行级事件溯源: deltas 退场).
    reasoning = ""
    verdict = ContractVerdict(ok=True)
    # Web sources this worker consults, de-duped across contract retries.
    # Pool merge still collect-only into this list → DelegateTool → turn card.
    # Stable ``#rN`` annotation (when ``env.turn_evidence_ledger`` is set) is
    # separate — not the old ``[n]`` annotate path (引用即出处 P1).
    worker_citations: list[dict] = []
    ledger_registrant = f"worker:{agent_id}"

    # Accepted react pass's finish override (cleared each attempt so a clean
    # rework after an interrupted first pass does not keep the interrupt warning).
    finish_override: list[FinishReason] = []
    # C·掐断透明化：正轨 token 撞顶等结构化原因码（与 DEGRADED 分流正交）。
    cutoff_reasons: list[str] = []
    # Sticky across attempts: cutoff_reasons is cleared each pass, but a round
    # fuse already blown must not reopen a full investigation after write_pass.
    round_ceiling_hit = False
    # Last accepted react pass's tool-failure facts (circuit-breaker tally).
    tool_failures: list[dict] = []
    # Cross-pass LoopController latches (validation path-stop / thrash).
    pass_controller_seed: dict | None = None
    controller_seed_out: list[dict] = []
    # Zero-disk (pinned landing): one short write pass — never a full investigation retry.
    write_pass_used = False
    light_mode = False
    runtime_file_products: list[Any] = []
    workspace_paths: list[str] | None = None
    attempt = 0
    while attempt < attempts:
        streamed_content.clear()
        finish_override.clear()
        cutoff_reasons.clear()
        tool_failures.clear()
        controller_seed_out.clear()
        pass_token_budget = _retry_token_budget(
            ceiling=token_ceiling, spent=run_usage.fuse_tokens
        )
        is_light_pass = light_mode
        pass_max = _pass_max_rounds(
            light_pass=is_light_pass,
            profile_max=profile.max_rounds,
            spent=run_rounds,
        )
        if pass_max is not None and pass_max <= 0:
            logger.info(
                "contract.retry_skipped_budget",
                run_id=spec.run_id,
                reason="round_cap",
                rounds=run_rounds,
            )
            break
        pass_profile = replace(
            profile, max_rounds=pass_max if pass_max is not None else 0
        )
        pass_tools = worker_tools
        pass_allowed = allowed_tools
        if is_light_pass:
            # Dedicated short-pass cap (not leftover from a main-pool formula).
            # Retrieval stays off; local inspect/run remain. run_rounds still
            # accumulates whatever this pass spends.
            pass_tools, pass_allowed = _narrow_for_light_repair(
                worker_tools,
                allowed_tools,
            )
            light_mode = False
        pass_round_used[0] = 0
        pass_round_limit[0] = pass_profile.max_rounds
        stamp_coord_round_budget(
            spec.run_id,
            used=0,
            limit=pass_profile.max_rounds,
            tokens_spent=_live_tokens_spent(),
        )
        use_rtd = (
            attempt == 0
            and not write_pass_used
            and spec.research_then_draft
            and (spec.draft_brief or "").strip()
        )
        if use_rtd:
            content, reasoning, round_usage, round_rounds = await research_then_draft(
                messages,
                llm=env.llm,
                tools=pass_tools,
                sink=env.sink,
                tool_ctx=tool_ctx,
                profile=pass_profile,
                turn_model=request_model,
                allowed_tools=pass_allowed,
                run_id=spec.run_id,
                agent_id=agent_id,
                citation_sink=worker_citations,
                approval_gate=env.approval_gate,
                draft_system=spec.draft_system or (spec.system_prompt_supplement or ""),
                draft_brief=spec.draft_brief,
                allow_research=True,
                evidence_ledger=env.evidence_ledger,
                side_key=spec.side_key,
                check_evidence_ledger=spec.evidence_ledger_check,
                usage_sink=inflight,
                on_round_begin=on_round_begin,
                streamed_content=streamed_content,
                gate_escalation_sink=gate_escalations,
                token_budget=pass_token_budget,
                finish_override_sink=finish_override,
                cutoff_reason_sink=cutoff_reasons,
            )
        else:
            content, reasoning, round_usage, round_rounds = await _react_and_capture(
                messages,
                llm=env.llm,
                tools=pass_tools,
                sink=env.sink,
                tool_ctx=tool_ctx,
                profile=pass_profile,
                turn_model=request_model,
                allowed_tools=pass_allowed,
                run_id=spec.run_id,
                agent_id=agent_id,
                citation_sink=worker_citations,
                turn_evidence_ledger=env.turn_evidence_ledger,
                ledger_registrant=ledger_registrant,
                approval_gate=env.approval_gate,
                usage_sink=inflight,
                on_round_begin=on_round_begin,
                streamed_content=streamed_content,
                gate_escalation_sink=gate_escalations,
                token_budget=pass_token_budget,
                finish_override_sink=finish_override,
                cutoff_reason_sink=cutoff_reasons,
                tool_failure_sink=tool_failures,
                controller_seed=pass_controller_seed,
                controller_seed_sink=controller_seed_out,
                files_expected=files_expected,
                report_delivery=report_delivery,
                short_write_posture=short_write_posture,
                tighten_verify_exec_thrash=tighten_verify_exec_thrash,
                expects_landing=files_expected,
                product_landing_artifacts=product_landing_artifacts,
            )
        if controller_seed_out:
            pass_controller_seed = dict(controller_seed_out[0])
        run_usage = run_usage + round_usage
        run_rounds += round_rounds
        run_usage_box[0] = run_usage
        run_rounds_box[0] = run_rounds
        if "max_rounds" in cutoff_reasons:
            round_ceiling_hit = True
        # This pass's usage is now folded into run_usage via its return value;
        # drop the mirror so a later non-react raise can't double-count it.
        inflight.clear()
        # Handoff-only / tool-only correction passes often stream no prose —
        # keep the prior non-empty body so contract checks and the terminal
        # RunState still see the already-qualified product.
        # Closing-round 便条 is also streamed content: if a prior body exists,
        # do not replace it with the brief.
        streamed = "".join(streamed_content).strip()
        # ``_react_and_capture`` always appends a trailing no-tool assistant with
        # this pass's content, so "last assistant has handoff" is false even when
        # the pass just finished via handoff. Look back to the last user turn.
        this_pass_handoff = False
        for msg in reversed(messages):
            if msg.role == "user":
                break
            if msg.role != "assistant" or not msg.tool_calls:
                continue
            if any(
                getattr(tc.function, "name", "") == HANDOFF_TOOL_NAME
                for tc in msg.tool_calls
            ):
                this_pass_handoff = True
                break
        if streamed:
            if retained_content and this_pass_handoff:
                content = retained_content
            else:
                retained_content = content if (content or "").strip() else streamed
        elif retained_content:
            content = retained_content
        elif (content or "").strip():
            # No prior body: accept this pass (incl. a brief as sole product).
            retained_content = content
        # files_written backs pinned-path landing; workspace_paths
        # reconciles declarative artifacts against the live workspace (+ this
        # run's own writes). Product gate: any successful write counts.
        touched_now = files_touched_from_transcript(messages)
        product_touched_now = filter_product_landing_paths(
            touched_now, product_landing_artifacts
        )
        product_files_written = len(product_touched_now)
        landing_fail_kind = landing_write_failure_kind(messages)
        debrief_now = debrief_from_transcript(messages)
        if deliverable and deliverable.artifacts:
            live_index = await _safe_index_files(tool_ctx.backend)
            workspace_paths = list(dict.fromkeys([*live_index, *touched_now]))
        else:
            workspace_paths = list(touched_now)
        verdict = check_contract(
            content,
            deliverable,
            files_written=product_files_written,
            debrief=debrief_now,
            workspace_paths=workspace_paths,
            landing_failure_kind=landing_fail_kind,
        )
        needs_handoff = worker_expects_handoff(env.plan, spec.run_id)
        handoff_offered = worker_tools.get_optional(HANDOFF_TOOL_NAME) is not None
        handoff_ok = (
            (not needs_handoff)
            or handoff_expectation_met(debrief_now)
            or not handoff_offered
        )
        if (verdict.ok and handoff_ok) or attempt == attempts - 1:
            break
        if token_ceiling > 0 and run_usage.fuse_tokens >= token_ceiling:
            logger.info(
                "contract.retry_skipped_budget",
                run_id=spec.run_id,
                reason="hard_ceiling",
                tokens=run_usage.fuse_tokens,
                ceiling=token_ceiling,
            )
            break
        budget_wind_down = _wind_down_entered(
            cutoff_reasons=cutoff_reasons,
            token_ceiling=token_ceiling,
            tokens_spent=run_usage.fuse_tokens,
        )
        if should_skip_contract_retry_for_budget(
            handoff_ok=handoff_ok,
            wind_down_entered=budget_wind_down,
        ):
            logger.info(
                "contract.retry_skipped_budget",
                run_id=spec.run_id,
                reason="wind_down",
                handoff_ok=True,
                tokens=run_usage.fuse_tokens,
                ceiling=token_ceiling,
                failures=verdict.failures,
            )
            break
        pass_interrupted = any(
            fr in (FinishReason.ERROR, FinishReason.DEGRADED) for fr in finish_override
        )
        if (
            _can_write_pass(
                verdict=verdict,
                files_expected=files_expected,
                files_written=product_files_written,
                write_pass_used=write_pass_used,
            )
        ):
            write_pass_used = True
            light_mode = True
            parts = [format_write_pass_feedback(verdict)]
            if needs_handoff and handoff_offered and not handoff_expectation_met(debrief_now):
                parts.append(format_handoff_feedback())
            messages.append(_retry_message("\n\n".join(p for p in parts if p)))
            logger.info(
                "contract.write_pass",
                run_id=spec.run_id,
                failures=verdict.failures,
                tokens_spent=run_usage.fuse_tokens,
                rounds_spent=run_rounds,
            )
            continue
        if write_pass_used and is_zero_files_gap(verdict):
            logger.info(
                "contract.write_pass_exhausted",
                run_id=spec.run_id,
                failures=verdict.failures,
            )
            break
        if should_skip_full_contract_retry_for_round_ceiling(
            cutoff_reasons=cutoff_reasons,
            prior_round_ceiling=round_ceiling_hit,
        ):
            logger.info(
                "contract.retry_skipped_budget",
                run_id=spec.run_id,
                reason="max_rounds",
                rounds=run_rounds,
                failures=verdict.failures,
            )
            break
        retry_cap = _pass_max_rounds(
            light_pass=False, profile_max=profile.max_rounds, spent=run_rounds
        )
        if retry_cap is not None and retry_cap <= 0:
            logger.info(
                "contract.retry_skipped_budget",
                run_id=spec.run_id,
                reason="round_cap",
                rounds=run_rounds,
            )
            break
        parts = []
        if pass_interrupted:
            parts.append(format_interrupted_pass_note())
        if not verdict.ok:
            parts.append(format_feedback(verdict))
        if needs_handoff and handoff_offered and not handoff_expectation_met(debrief_now):
            parts.append(format_handoff_feedback())
        messages.append(_retry_message("\n\n".join(p for p in parts if p)))
        rb = tool_ctx.retrieval_budget
        original_rb = int(spec.retrieval_budget or (rb.limit if rb else 0) or 0)
        wind_down = budget_wind_down
        write_disk_form = bool(files_expected)
        slice_n = rework_refill_slots(
            original_limit=original_rb,
            wind_down_entered=wind_down,
            write_disk_form=write_disk_form,
        )
        if rb is not None and slice_n > 0:
            new_remaining = await rb.refill_within_cap(slice_n, cap=original_rb)
            logger.info(
                "retrieval_budget.rework_refill",
                run_id=spec.run_id,
                added=slice_n,
                remaining=new_remaining,
                limit=rb.limit,
                cap=original_rb,
                wind_down=wind_down,
            )
        elif wind_down or write_disk_form:
            logger.info(
                "retrieval_budget.rework_refill_skipped",
                run_id=spec.run_id,
                reason="wind_down" if wind_down else "write_disk_form",
                original_limit=original_rb,
            )
        logger.info(
            "contract.retry",
            run_id=spec.run_id,
            attempt=attempt + 1,
            failures=verdict.failures,
            handoff_ok=handoff_ok,
            pass_interrupted=pass_interrupted,
        )
        attempt += 1

    return ContractLoopResult(
        content=content,
        reasoning=reasoning,
        verdict=verdict,
        worker_citations=worker_citations,
        priced_model=priced_model,
        run_usage=run_usage,
        run_rounds=run_rounds,
        finish_override=finish_override,
        cutoff_reasons=cutoff_reasons,
        tool_failures=tool_failures,
        write_pass_used=write_pass_used,
        workspace_paths=workspace_paths,
        product_landing_artifacts=product_landing_artifacts,
        runtime_file_products=runtime_file_products,
        deliverable=deliverable,
        tool_ctx=tool_ctx,
    )
