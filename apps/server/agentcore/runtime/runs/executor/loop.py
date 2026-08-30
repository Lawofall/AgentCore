"""AGENT-node react+capture loop body + contract decision ladder orchestration.

Split from ``.node`` — pure move; consumed only by the node facade.
Domain hooks (cite) and retry predicates live in sibling modules.
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
    collect_opaque_source_data_paths,
    format_cite_upgrade_feedback,
    format_feedback,
    format_handoff_feedback,
    format_interrupted_pass_note,
    format_light_repair_feedback,
    format_write_pass_feedback,
    handoff_expectation_met,
    is_zero_files_gap,
    needs_file_contents,
    node_has_dependents,
    partition_citation_failures,
    strip_invalid_ledger_refs_from_surfaces,
    worker_expects_handoff,
)
from agentcore.runtime.runs.executor.context import (
    _load_artifact_contents,
    _safe_index_files,
)
from agentcore.runtime.runs.executor.env import AgentExecutorEnv
from agentcore.runtime.runs.executor.hooks import _grant_citation_rework_reread
from agentcore.runtime.runs.executor.retry import (
    _can_light_repair,
    _can_write_pass,
    _narrow_for_light_repair,
    _pass_max_rounds,
    _retry_token_budget,
    _wind_down_entered,
    bind_round_budget_on_begin,
    should_skip_contract_retry_for_budget,
    should_skip_full_contract_retry_for_round_ceiling,
    stamp_coord_round_budget,
    sync_round_budget_awareness,
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
    two_phase: bool
    cite_upgrade_used: bool
    artifact_contents: dict[str, str] | None
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
    from agentcore.runtime.engine.governance import registry_can_execute

    can_execute = registry_can_execute(worker_tools, tool_ctx)
    allowed_tools = prepared.allowed_tools
    files_expected = prepared.files_expected
    report_delivery = prepared.report_delivery
    product_landing_artifacts = prepared.product_landing_artifacts
    short_write_posture = prepared.short_write_posture
    tighten_verify_exec_thrash = prepared.tighten_verify_exec_thrash
    deliverable_form = prepared.deliverable_form
    token_ceiling = prepared.token_ceiling
    attempts = prepared.attempts
    two_phase = prepared.two_phase

    run_usage = run_usage_box[0]
    run_rounds = run_rounds_box[0]
    # Pass-local round counter for the CEO idle brief (busy-channel used/limit).
    # Not injected into the worker window — rounds are an engine ceiling.
    # ``run_rounds`` still accumulates every produce pass (billing / exception).
    pass_round_used = [0]
    pass_round_limit = [profile.max_rounds]

    def _live_tokens_spent() -> int:
        extra = inflight[0].total_tokens if inflight else 0
        return run_usage_box[0].total_tokens + extra

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
    # fuse already blown must not reopen a full investigation after light_repair.
    round_ceiling_hit = False
    # Last accepted react pass's tool-failure facts (circuit-breaker tally).
    tool_failures: list[dict] = []
    # Cross-pass LoopController latches (validation path-stop / thrash).
    pass_controller_seed: dict | None = None
    controller_seed_out: list[dict] = []
    # Format-only / handoff-thin: one in-place light repair before full contract.retry.
    # Zero-disk (form=files / artifacts): one short write pass — never a full investigation retry.
    # 调研两阶段：A 跳过引用闸；cite 不干净时同 worker 自动升 B（一次），不过则 rejected。
    light_repair_used = False
    write_pass_used = False
    cite_upgrade_used = False
    light_mode = False
    runtime_file_products: list[Any] = []
    artifact_contents: dict[str, str] | None = None
    workspace_paths: list[str] | None = None
    source_data_paths: list[str] | None = None
    attempt = 0
    while attempt < attempts:
        streamed_content.clear()
        finish_override.clear()
        cutoff_reasons.clear()
        tool_failures.clear()
        controller_seed_out.clear()
        pass_token_budget = _retry_token_budget(
            ceiling=token_ceiling, spent=run_usage.total_tokens
        )
        is_light_pass = light_mode
        pass_max = _pass_max_rounds(
            light_pass=is_light_pass,
            profile_max=profile.max_rounds,
            spent=run_rounds,
        )
        if pass_max <= 0:
            logger.info(
                "contract.retry_skipped_budget",
                run_id=spec.run_id,
                reason="round_cap",
                rounds=run_rounds,
            )
            break
        pass_profile = replace(profile, max_rounds=pass_max)
        pass_tools = worker_tools
        pass_allowed = allowed_tools
        if is_light_pass:
            # Dedicated short-pass cap (not leftover from the exhausted main pool),
            # then clipped by the cross-attempt total in ``_pass_max_rounds``.
            # Tools narrow; run_rounds still accumulates whatever this pass spends.
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
        if is_light_pass or attempt > 0:
            # Round 0 of a new react_loop skips on_round_begin — announce the
            # new pass cap once (light_repair after 56/56 must see 4). Do not
            # refresh this fact on later rounds of the same pass.
            sync_round_budget_awareness(
                messages,
                limit=pass_profile.max_rounds,
                before_last_user=True,
            )
        use_rtd = (
            attempt == 0
            and not light_repair_used
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
                form_prose=deliverable_form == "prose",
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
        # Promote path: handoff may put a brief into final_text when round
        # body_chars==0; that must not wipe retained prior prose on a
        # handoff-only light_repair pass.
        streamed = "".join(streamed_content).strip()
        if streamed:
            retained_content = content if (content or "").strip() else streamed
        elif retained_content:
            content = retained_content
        elif (content or "").strip():
            # No prior body: accept this pass (incl. promoted brief as sole product).
            retained_content = content
        # files_written backs form=files / artifacts landing; workspace_paths
        # reconciles declarative artifacts against the live workspace (+ this
        # run's own writes). Handoff gate: nodes with downstream dependents must
        # submit a non-empty brief (one correction shot, then degraded synth).
        # Product gate: any successful write counts (dossier notes under
        # research/reviews/debate included — see landing_product).
        touched_now = files_touched_from_transcript(messages)
        product_touched_now = filter_product_landing_paths(
            touched_now, product_landing_artifacts
        )
        product_files_written = len(product_touched_now)
        # Always classify failed landing attempts so code_audit can demote
        # absence hard-fails when the channel died mid-landing (not only zero-disk).
        landing_fail_kind = landing_write_failure_kind(messages)
        debrief_now = debrief_from_transcript(messages)
        # Re-index the live workspace only when reconciling declarative
        # artifacts — otherwise this run's own writes are enough.
        # 交付形态对齐: for a FILE deliverable, load the landed files' text so the
        # contract's content checks (length / keyword / section) read the product on
        # disk, not just chat prose — artifacts declared → matching paths; else this
        # run's own writes. Also loads when this run's writes are a web batch
        # (HTML+CSS/JS) so the seam gate can cross-check selectors. ``needs_file_contents``
        # skips the read for prose / existence-only non-web deliverables.
        # Citation / bibliography: when the turn evidence ledger is connected,
        # check_contract scans the same content surfaces already loaded here.
        artifact_contents = None
        turn_ledger = env.turn_evidence_ledger
        load_contents = needs_file_contents(
            deliverable,
            landed_paths=touched_now,
        )
        if deliverable and deliverable.artifacts:
            live_index = await _safe_index_files(tool_ctx.backend)
            workspace_paths = list(dict.fromkeys([*live_index, *touched_now]))
            if load_contents:
                patterns = list(
                    dict.fromkeys([*deliverable.artifacts, *touched_now])
                )
                artifact_contents = await _load_artifact_contents(
                    tool_ctx.backend,
                    patterns,
                    workspace_paths,
                )
        else:
            workspace_paths = list(touched_now)
            if touched_now and load_contents:
                artifact_contents = await _load_artifact_contents(
                    tool_ctx.backend,
                    touched_now,
                    workspace_paths,
                )
        source_data_paths = collect_opaque_source_data_paths(
            material_paths=getattr(tool_ctx, "material_paths", None),
            workspace_paths=workspace_paths,
            landed_paths=touched_now,
        )
        # 调研阶段 A：广搜草案不跑成稿引用闸；升 B（cite_upgrade_used）后再验。
        in_phase_a = two_phase and not cite_upgrade_used
        turn_ledger_entries = (
            turn_ledger.all_entries() if turn_ledger is not None else None
        )
        turn_citable_ids = (
            turn_ledger.draft_citable_ids() if turn_ledger is not None else None
        )
        verdict = check_contract(
            content,
            deliverable,
            files_written=product_files_written,
            debrief=debrief_now,
            workspace_paths=workspace_paths,
            artifact_contents=artifact_contents,
            ledger_entries=turn_ledger_entries,
            citable_ids=turn_citable_ids,
            enforce_citations=not in_phase_a,
            landing_failure_kind=landing_fail_kind,
            can_execute=can_execute,
            source_data_paths=source_data_paths,
        )
        # Handoff gate only forces a correction shot when the tool is actually
        # offered (production worker registry). Empty-registry unit tests still
        # get a degraded synth below without burning an extra LLM round.
        # Leaves: substantial work (tools / longer body) also expects a brief so
        # CEO / delivery_status can see incomplete reports — short pure-body exempt.
        has_dependents = node_has_dependents(env.plan, spec.run_id)
        needs_handoff = worker_expects_handoff(
            env.plan,
            spec.run_id,
            content=content,
            messages=messages,
            files_touched=touched_now,
        )
        handoff_offered = worker_tools.get_optional(HANDOFF_TOOL_NAME) is not None
        handoff_ok = (
            (not needs_handoff)
            or handoff_expectation_met(debrief_now, for_dependents=has_dependents)
            or not handoff_offered
        )
        checked_files = (
            list(artifact_contents.keys()) if artifact_contents else None
        )
        # 调研 A→B：阶段 A 已过非引用合同 → 探测引用闸；仅引用问题则先自动剥离再决定。
        if in_phase_a and verdict.ok:
            probe = check_contract(
                content,
                deliverable,
                files_written=product_files_written,
                debrief=debrief_now,
                workspace_paths=workspace_paths,
                artifact_contents=artifact_contents,
                ledger_entries=turn_ledger_entries,
                citable_ids=turn_citable_ids,
                enforce_citations=True,
                landing_failure_kind=landing_fail_kind,
                can_execute=can_execute,
                source_data_paths=source_data_paths,
            )
            cite_fail, other_fail = partition_citation_failures(probe.failures)
            if other_fail:
                verdict = probe
            elif cite_fail:
                # 1) 自动剥离落盘（及正文）非法 #rN，写回后再验；过则免 LLM。
                new_arts, new_body, stripped_ids = (
                    strip_invalid_ledger_refs_from_surfaces(
                        artifact_contents=artifact_contents,
                        body=content,
                        citable_ids=turn_citable_ids,
                    )
                )
                if stripped_ids and new_arts and artifact_contents:
                    for path, text in new_arts.items():
                        if artifact_contents.get(path) == text:
                            continue
                        try:
                            await tool_ctx.backend.write(path, text)
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "contract.cite_upgrade",
                                action="strip_write_failed",
                                path=path,
                                exc_info=True,
                            )
                    artifact_contents = new_arts
                    content = new_body
                    checked_files = (
                        list(artifact_contents.keys()) if artifact_contents else None
                    )
                    probe = check_contract(
                        content,
                        deliverable,
                        files_written=product_files_written,
                        debrief=debrief_now,
                        workspace_paths=workspace_paths,
                        artifact_contents=artifact_contents,
                        ledger_entries=turn_ledger_entries,
                        citable_ids=turn_citable_ids,
                        enforce_citations=True,
                        landing_failure_kind=landing_fail_kind,
                        can_execute=can_execute,
                        source_data_paths=source_data_paths,
                    )
                    cite_fail, other_fail = partition_citation_failures(
                        probe.failures
                    )
                    if other_fail:
                        verdict = probe
                    elif not cite_fail:
                        cite_upgrade_used = True  # 已按 B 处理，跳过阶段 A 安全网重验
                        logger.info(
                            "contract.cite_upgrade",
                            run_id=spec.run_id,
                            action="auto_strip",
                            stripped=stripped_ids,
                            tokens_spent=run_usage.total_tokens,
                            rounds_spent=run_rounds,
                        )
                        # 剥完即过 → 直接验收，不开 LLM。
                        cite_fail = []
                # 2) 剥完仍有引用/书目问题 → 一次 light_mode 短修（禁检索/深读）。
                if cite_fail and not other_fail:
                    cite_upgrade_used = True
                    light_mode = True
                    _grant_citation_rework_reread(
                        tool_ctx,
                        cite_failures=cite_fail,
                        checked_files=checked_files,
                        deliverable=deliverable,
                    )
                    parts = [
                        format_cite_upgrade_feedback(
                            cite_fail,
                            checked_files=checked_files,
                        )
                    ]
                    if needs_handoff and handoff_offered and not handoff_expectation_met(debrief_now, for_dependents=has_dependents):
                        parts.append(
                            format_handoff_feedback(
                                present_but_thin=debrief_now is not None,
                                for_dependents=has_dependents,
                            )
                        )
                    messages.append(
                        _retry_message("\n\n".join(p for p in parts if p))
                    )
                    logger.info(
                        "contract.cite_upgrade",
                        run_id=spec.run_id,
                        action="light_repair",
                        failures=cite_fail,
                        stripped=stripped_ids or None,
                        tokens_spent=run_usage.total_tokens,
                        rounds_spent=run_rounds,
                    )
                    continue
            # else: already cite-clean → accept as B-ready (handoff 仍可走下方返工)
        if (verdict.ok and handoff_ok) or attempt == attempts - 1:
            break
        # B 升级后仍仅引用不过闸 → 收口为 rejected（不再满合同重试 cite）。
        if two_phase and cite_upgrade_used and not verdict.ok:
            cite_fail, other_fail = partition_citation_failures(verdict.failures)
            if cite_fail and not other_fail:
                logger.info(
                    "contract.cite_upgrade_exhausted",
                    run_id=spec.run_id,
                    failures=cite_fail,
                )
                break
        # 二次触顶：已达硬顶则不再开 correction pass（立即收口）。
        if token_ceiling > 0 and run_usage.total_tokens >= token_ceiling:
            logger.info(
                "contract.retry_skipped_budget",
                run_id=spec.run_id,
                reason="hard_ceiling",
                tokens=run_usage.total_tokens,
                ceiling=token_ceiling,
            )
            break
        # 定案 B：已交接成功且进入预算收尾/将尽 → 跳过契约返工（勿空转收尾）。
        budget_wind_down = _wind_down_entered(
            cutoff_reasons=cutoff_reasons,
            token_ceiling=token_ceiling,
            tokens_spent=run_usage.total_tokens,
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
                tokens=run_usage.total_tokens,
                ceiling=token_ceiling,
                failures=verdict.failures,
            )
            break
        # 断流归因：这一遍是被 LLM 传输失败掐断的（ERROR = 没收到正文，
        # DEGRADED = 只收到片段），不是 worker 自己写砸了。不标注的话它会把
        # 「产出为空」当成自己的锅，重试轮里只补一次 handoff 而不重写正文。
        pass_interrupted = any(
            fr in (FinishReason.ERROR, FinishReason.DEGRADED) for fr in finish_override
        )
        # 断流收尾且合同已过、仅缺 handoff：勿开 light_repair（会清掉 finish_override），
        # 直接收口 → terminal 保留 FINISH_INTERRUPT + degraded 对账。
        if pass_interrupted and verdict.ok and not handoff_ok:
            logger.info(
                "contract.retry_skipped_interrupt",
                run_id=spec.run_id,
                reason="finish_interrupt_handoff",
                handoff_ok=False,
            )
            break
        if (
            _can_light_repair(
                verdict=verdict,
                handoff_ok=handoff_ok,
                light_repair_used=light_repair_used,
            )
            and _pass_max_rounds(
                light_pass=True, profile_max=profile.max_rounds, spent=run_rounds
            )
            > 0
        ):
            light_repair_used = True
            light_mode = True
            parts = []
            if not verdict.ok:
                parts.append(
                    format_light_repair_feedback(
                        verdict,
                        prior_content=content,
                        checked_files=checked_files,
                    )
                )
            if needs_handoff and handoff_offered and not handoff_expectation_met(debrief_now, for_dependents=has_dependents):
                parts.append(
                    format_handoff_feedback(
                        present_but_thin=debrief_now is not None,
                        for_dependents=has_dependents,
                    )
                )
            messages.append(_retry_message("\n\n".join(p for p in parts if p)))
            logger.info(
                "contract.light_repair",
                run_id=spec.run_id,
                failures=verdict.failures,
                handoff_ok=handoff_ok,
                tokens_spent=run_usage.total_tokens,
                rounds_spent=run_rounds,
            )
            continue
        if (
            _can_write_pass(
                verdict=verdict,
                files_expected=files_expected,
                files_written=product_files_written,
                write_pass_used=write_pass_used,
            )
            and _pass_max_rounds(
                light_pass=True, profile_max=profile.max_rounds, spent=run_rounds
            )
            > 0
        ):
            write_pass_used = True
            light_mode = True  # reuse narrow write/handoff surface + short rounds
            parts = [format_write_pass_feedback(verdict)]
            if needs_handoff and handoff_offered and not handoff_expectation_met(debrief_now, for_dependents=has_dependents):
                parts.append(
                    format_handoff_feedback(
                        present_but_thin=debrief_now is not None,
                        for_dependents=has_dependents,
                    )
                )
            messages.append(_retry_message("\n\n".join(p for p in parts if p)))
            logger.info(
                "contract.write_pass",
                run_id=spec.run_id,
                failures=verdict.failures,
                tokens_spent=run_usage.total_tokens,
                rounds_spent=run_rounds,
            )
            continue
        # Write pass already spent and still zero disk → hard-fail path (no full retry).
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
        if retry_cap <= 0:
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
            parts.append(format_feedback(verdict, checked_files=checked_files))
        if needs_handoff and handoff_offered and not handoff_expectation_met(debrief_now, for_dependents=has_dependents):
            parts.append(
                format_handoff_feedback(
                    present_but_thin=debrief_now is not None,
                    for_dependents=has_dependents,
                )
            )
        messages.append(_retry_message("\n\n".join(p for p in parts if p)))
        # Citation-related full retry: refresh sticky reread so cleared drafts stay readable.
        cite_fail_retry, _other_retry = partition_citation_failures(verdict.failures)
        if cite_fail_retry:
            _grant_citation_rework_reread(
                tool_ctx,
                cite_failures=cite_fail_retry,
                checked_files=checked_files,
                deliverable=deliverable,
            )
        # Full contract.retry: refill only within original retrieval cap, and
        # never after wind_down（不得恢复全量检索）. 写盘形态返工缺的是定向补写而非检索；
        # 引用类失败例外——它本就需要重读来源，且上面已发放 sticky reread。
        rb = tool_ctx.retrieval_budget
        original_rb = int(spec.retrieval_budget or (rb.limit if rb else 0) or 0)
        wind_down = budget_wind_down
        write_disk_form = bool(files_expected) and not cite_fail_retry
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

    # 调研两阶段安全网：若未升到 B 就收口，仍把成稿引用闸失败并入 verdict，
    # 避免草案被 silent accepted（draft 不得进 delivered_files）。
    if two_phase and not cite_upgrade_used and verdict.ok:
        _touched_safe = files_touched_from_transcript(messages)
        _product_safe = len(
            filter_product_landing_paths(
                _touched_safe,
                product_landing_artifacts,
            )
        )
        probe = check_contract(
            content,
            deliverable,
            files_written=_product_safe,
            debrief=debrief_from_transcript(messages),
            workspace_paths=workspace_paths,
            artifact_contents=artifact_contents,
            ledger_entries=(
                env.turn_evidence_ledger.all_entries()
                if env.turn_evidence_ledger is not None
                else None
            ),
            citable_ids=(
                env.turn_evidence_ledger.draft_citable_ids()
                if env.turn_evidence_ledger is not None
                else None
            ),
            enforce_citations=True,
            landing_failure_kind=landing_write_failure_kind(messages),
            can_execute=can_execute,
            source_data_paths=source_data_paths,
        )
        cite_fail, other_fail = partition_citation_failures(probe.failures)
        if other_fail:
            verdict = probe
        elif cite_fail:
            verdict = ContractVerdict(
                ok=False,
                failures=cite_fail,
                warnings=list(probe.warnings),
                warning_rows=list(probe.warning_rows),
                soft_failures=list(probe.soft_failures),
                visual_failures=list(probe.visual_failures),
            )
            logger.info(
                "contract.cite_phase_a_terminal_reject",
                run_id=spec.run_id,
                failures=cite_fail,
            )

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
        two_phase=two_phase,
        cite_upgrade_used=cite_upgrade_used,
        artifact_contents=artifact_contents,
        workspace_paths=workspace_paths,
        product_landing_artifacts=product_landing_artifacts,
        runtime_file_products=runtime_file_products,
        deliverable=deliverable,
        tool_ctx=tool_ctx,
    )
