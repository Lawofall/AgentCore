"""ReAct main loop: turn control, LLM rounds, tool execution.

Wind-down / delivery-idle / timeout-grace live in ``loop_wind_down``; captain
live-mirror (G4) in ``loop_mirror``; structured-reply salvage in ``loop_salvage``.
Public import path stays this module (``react_loop``, ``ReactLoopOut``,
``CaptainLoopMirror``, ``current_captain_loop``, ``sync_captain_loop_mirror``).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.core.error_codes import ErrorCode
from agentcore.core.logging import get_logger
from agentcore.llm.errors import overlay_progress_failure_message
from agentcore.llm.profiles import ProfileParams, get_profile
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage

if TYPE_CHECKING:
    from agentcore.runtime.approvals import ApprovalGate

from agentcore.runtime.events import (
    EventSink,
    FinishReason,
    content_delta,
    content_reset,
    reasoning_delta,
)
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry

from .ceiling import ceiling_finalize
from .directive import LoopDirective
from .directive_apply import apply_loop_directive
from .governance import (
    apply_exec_env_dead_retire,
    apply_workspace_channel_dead_retire,
    classify_investigation_tools,
    coordination_injection_has_all_completed,
    create_loop_controller,
    decide_llm_failure,
    maybe_inject_audit_gate,
    maybe_inject_availability_status_nudge,
    maybe_inject_debate_gate,
    maybe_inject_turn_token_budget_gate,
    resolve_openai_tool_defs,
)
from .loop_mirror import (
    CaptainLoopMirror,
    current_captain_loop,
    sync_captain_loop_mirror,
)
from .loop_salvage import maybe_salvage_captain_reply
from .loop_wind_down import LoopWindDown
from .outcome import RoundOutcome
from .round import (
    LlmRoundFailure,
    decide_no_tool_round,
    record_round_start,
    run_llm_round,
)
from .segments import join_segments
from .soft_gates import maybe_soft_gate_no_tool_return
from .tool_protocol_sanitize import prepare_assistant_content
from .tool_round import handle_tool_calls_round

logger = get_logger(__name__)


@dataclass
class ReactLoopOut:
    """Mutable out-param bag for ``react_loop`` side channels.

    Callers pass one instance with only the channels they care about set; the loop
    mutates those lists in place. Survives ``raise_on_error`` so failure billing can
    still read consumed ``usage`` after an exception — intentionally not folded into
    the return tuple. Adding a new side channel is a new field here, not a new
    ``react_loop`` parameter.
    """

    rounds: list[int] | None = None
    citations: list[dict[str, Any]] | None = None
    usage: list[TokenUsage] | None = None
    finish_override: list[FinishReason] | None = None
    gate_escalations: list[dict[str, Any]] | None = None
    cutoff_reasons: list[str] | None = None
    tool_failures: list[dict[str, Any]] | None = None
    controller_seed_out: list[dict[str, Any]] | None = None


def _messages_have_tool_progress(messages: list[LLMMessage]) -> bool:
    """True when this turn already issued or completed a tool call (process exists)."""
    return any(
        m.role == "tool" or (m.role == "assistant" and m.tool_calls) for m in messages
    )


async def react_loop(
    *,
    messages: list[LLMMessage],
    llm: OpenAICompatibleProvider,
    tools: ToolRegistry,
    sink: EventSink,
    tool_context: ToolContext,
    profile: ProfileParams | None = None,
    turn_model: str | None = None,
    allowed_tool_names: list[str] | None = None,
    on_content: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
    on_tool_progress: Callable[[str, int], None] | None = None,
    on_reset: Callable[[str], None] | None = None,
    on_round_begin: Callable[[], list[LLMMessage]] | None = None,
    raise_on_error: bool = False,
    annotate_citations: bool = True,
    turn_evidence_ledger: EvidenceLedgerCore | None = None,
    ledger_registrant: str = "",
    approval_gate: ApprovalGate | None,
    out: ReactLoopOut | None = None,
    run_id: str = "",
    agent_id: str = "",
    role: str = "",
    deliverable_only: bool = False,
    supports_tools: bool | None = None,
    token_budget: int = 0,
    controller_seed: Mapping[str, Any] | None = None,
    files_expected: bool = False,
    report_delivery: bool = False,
    short_write_posture: bool = False,
    tighten_verify_exec_thrash: bool = False,
    form_prose: bool = False,
    product_landing_artifacts: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, str, TokenUsage, int]:
    """Run the ReAct loop.

    Returns ``(final_content, final_reasoning, usage, rounds)`` where ``usage`` is
    the turn's :class:`TokenUsage` summed across every round (carrying the
    cache_hit/cache_miss split so cost stays honest on multi-turn chats — a single
    object instead of loose ints). ``final_reasoning`` is the concatenated
    thinking text across all rounds (empty when thinking is disabled), mirroring
    what was streamed via ``reasoning_delta`` so it can be persisted for replay.

    The ``profile`` drives both the model params and the round budget
    (``profile.max_rounds``); it defaults to the chat profile. By
    default content/reasoning deltas are emitted as conversation events
    (single-agent path). A caller running a multi-agent run passes ``on_content``
    /``on_reasoning`` to redirect text into ``run_output_delta`` instead, and
    ``on_tool_progress`` to surface a worker's tool-call ARGUMENT streaming
    (``(tool_name, cumulative_chars)``, throttled) — the only live signal during a
    long file write, which is neither content nor reasoning.
    ``on_reset`` mirrors that redirection for every draft-discard reset: the
    default clears the CEO bubble (``content_reset``); a worker passes ``on_reset``
    to clear its run card (``run_output_reset``) instead — so the rewrite replaces
    the discarded draft cleanly on whichever surface streamed it (统一底线). It takes
    the ``ResetReason`` (finish_guard / retry / soft_gate / narration / ask_user) —
    each emit site states WHY; folds always clear the draft and leave no process trace.
    ``on_round_begin`` (when provided) is called at the top of every round AFTER the
    first; the messages it returns are appended to the window before that round's LLM
    call. A generic「inject context that accrued while the run was working」hook —
    workers stamp round-budget on the CEO idle brief and currently return no extra
    messages; ``None`` (CEO / solo / tests) is a no-op. The engine only appends what
    it returns — the caller owns the semantics.
    ``allowed_tool_names`` filters which tools the model may call and execute
    (schema offer + ``execute_tools`` enforce; ``None`` = all,
    ``[]`` = none). Tool execution events always go to the sink.
    ``out`` (:class:`ReactLoopOut`) is the single mutable out-param bag for side
    channels (rounds / citations / usage / finish_override / gate_escalations /
    cutoff_reasons / tool_failures / controller_seed_out). Callers set only the
    fields they care about; unset (``None``) fields stay inert — same as the old
    per-sink ``None``. ``usage`` mirrors the running ``total_usage`` after each
    completed round so a caller that catches an exception can still bill tokens
    consumed before the failure (B-deep 失败计费): on ``raise_on_error`` the
    accumulated usage is otherwise lost inside this frame when a mid-loop round
    raises. ``usage`` / ``finish_override`` / ``cutoff_reasons`` are cleared on
    entry and only ever hold the latest cumulative value; on a normal return the
    caller uses the returned usage instead. ``finish_override`` (CEO captain path)
    carries a :class:`FinishReason` the caller should stamp instead of the
    rounds-derived default (``end_turn`` / ``max_rounds``) — ``DEGRADED`` on
    empty-response threshold, ``UNPRODUCTIVE`` on all-tools-failed early-stop (B2);
    left empty on a normal finish. ``citations`` aggregates web sources from
    research tools (de-duped, capped). ``gate_escalations`` (Worker routing Phase 1)
    collects Escalation Gate rows after ``execute_tools`` (CEO / solo leave
    unset — gate inert). ``tool_failures`` / ``controller_seed_out`` are replaced
    on EVERY exit — the clean returns and the abnormal ones alike (a propagated
    round failure under ``raise_on_error``, a hard-timeout force cancel), so a
    FAILED / CANCELLED run reports the circuit-breaker facts it actually
    accumulated (``LoopController.export_seed`` for write_pass / light_repair /
    contract retry).
    ``turn_evidence_ledger`` (调研路径) registers hits into the turn-shared ledger
    and annotates tool output with stable ``#rN=url`` for CEO and workers alike
    (引用即出处 P1). ``annotate_citations`` gates finish_guard's legacy ``[n]`` check
    (CEO True / worker False)；``#rN`` id 存在闸在台账接通且正文出现约定标记时启用
    （Q5；worker 回炉 1 次 / CEO 跟配置）。without a ledger the old ``[n]=url``
    annotate path still applies. Debate speakers omit the turn ledger (场级 ``#e``).
    ``approval_gate`` (the turn's gate — captain and delegated workers alike) pauses
    GRANTABLE tool calls until the user authorizes them — a denial is fed back to
    the model as a tool result so it can adapt. It is REQUIRED: pass ``None`` only
    when this path genuinely has no user to ask, which makes any call that needs
    approval fail closed (tool_exec denies rather than executing ungated).

    ``run_id`` / ``role`` scope the execution-level facts (§8.3) this loop records
    into the turn's ambient :data:`~agentcore.runtime.facts.current_fact_log`
    (round_boundary / llm_call / note) — captain vs worker, so a multi-agent turn's
    facts split per run. They default to empty (a standalone loop / test records
    facts with no scope, or none at all when no log is bound).

    ``deliverable_only`` makes the RETURNED ``final_content`` the 交付正文 only — the
    prose a round streams BEFORE a *non-terminal* tool call is treated as PROCESS
    narration ("我先查一下" / an acknowledgement of an injected ``[系统提示]`` steer)
    and rolled back off the accumulator (mirroring the finish_guard ``Rework``
    rollback), so it never accrues into the persisted product / next-turn history /
    CEO synthesis input — **unless every tool in that round failed**, in which case
    the prose is kept (already streamed to the user; not a successful lead-in).
    It is always journaled per round (llm_call fact → 旁白入 journal). Two display
    disciplines by channel架构:

    - CEO captain (``on_reset`` is None → default ``content_delta`` / ``content_reset``):
      the narration STAYS streamed + visible in the SEPARATE process timeline
      (透明可见); only ``messages.content`` (旁路 conformance) is trimmed. No reset.
    - worker / debater / revision (``on_reset`` routes ``run_output_reset``, and the
      card replays from the ``message_final`` fact — a SINGLE display+data channel):
      the narration rollback ALSO emits ``run_output_reset`` to clear the streamed
      draft off the card, so 直播 == the rolled-back deliverable == 重载 (synthesized
      from ``message_final``) — the conformance invariant.

    Terminal rounds (handoff / suspend checkpoints other than blocking ``ask_user``)
    KEEP their pre-tool text — that IS the deliverable at that boundary. Blocking
    ``ask_user`` absorbs same-round prose into the card instead (see
    ``ask_user_absorb``). Default ``False`` leaves the
    accumulation byte-identical to before (standalone loops / tests).

    ``token_budget`` (Worker hard ceiling · loose backstop): a cumulative
    input+output token cap for the whole run, checked at the TOP of each round. Once
    ``total_usage.total_tokens`` reaches it the loop stops and force-finalizes — the
    backstop against a worker blowing past the configured unified ceiling. The
    terminal finalize (this AND ``max_rounds`` exhaustion) is gate-routed by run
    health (``controller.is_thrashing()``): an on-track run delivers normally; a
    thrashing worker finishes DEGRADED and emits an observable ``escalation_raised``
    signal (no auto re-decompose — the CEO may voluntarily replan). ``0`` (CEO /
    solo / tests / ceiling disabled) disables the backstop, leaving the run bounded
    only by ``profile.max_rounds``.

    ``controller_seed`` (resume path): optional JSON-safe latch snapshot from a prior
    ``turn_paused.controller``; omitted on a fresh turn (behaviour unchanged).
    """
    # Bind legacy local names so the loop body stays a pure interface change.
    round_sink = out.rounds if out is not None else None
    citation_sink = out.citations if out is not None else None
    usage_sink = out.usage if out is not None else None
    finish_override_sink = out.finish_override if out is not None else None
    gate_escalation_sink = out.gate_escalations if out is not None else None
    cutoff_reason_sink = out.cutoff_reasons if out is not None else None
    tool_failure_sink = out.tool_failures if out is not None else None
    controller_seed_sink = out.controller_seed_out if out is not None else None

    profile = profile or get_profile("chat")
    if usage_sink is not None:
        usage_sink.clear()
    if finish_override_sink is not None:
        finish_override_sink.clear()
    if cutoff_reason_sink is not None:
        cutoff_reason_sink.clear()

    disabled_tools: set[str] = set()
    # Re-apply run-scoped read_url retirement from a prior pass (stream-stall →
    # Wave retry, or contract write_pass/retry) so the tool is not re-offered.
    # web_search stays closed with it — otherwise restart re-opens search thrash.
    if run_id:
        from agentcore.tools.builtin.web._net import is_read_url_retired

        if is_read_url_retired(run_id):
            disabled_tools.add("read_url")
            disabled_tools.add("web_search")
    # 检索预算临界（剩 ≤2）一次性 reflection，缓解同轮 fan-out 超订。
    retrieval_critical_warned = False
    # 上次埋点过的分工具用量 (web_search, read_url)：只在变化时记一行。
    retrieval_spend_logged: tuple[int, int] | None = None

    _emit_content_raw = on_content or (lambda delta: sink.emit(content_delta(delta)))
    _emit_reset_raw = on_reset or (lambda reason: sink.emit(content_reset(reason)))

    def emit_content(delta: str) -> None:
        _emit_content_raw(delta)
        if role == "captain" and (delta or "").strip():
            from agentcore.runtime.coordination.session import active_coordination

            coord = active_coordination()
            if coord is not None:
                coord.note_attached_inject_visible_close(delta)

    def emit_reset(reason: str) -> None:
        _emit_reset_raw(reason)
        if role == "captain":
            from agentcore.runtime.coordination.session import active_coordination

            coord = active_coordination()
            if coord is not None:
                coord.clear_attached_inject_visible_close()

    emit_reasoning = on_reasoning or (lambda delta: sink.emit(reasoning_delta(delta)))

    total_usage = TokenUsage()
    final_content = ""
    final_reasoning = ""

    base_model = turn_model
    if base_model is None:
        from agentcore.config import settings

        logger.warning(
            "react_loop.missing_turn_model",
            fallback=settings.platform_model,
        )
        base_model = settings.platform_model

    investigation_tools = classify_investigation_tools(tools, allowed_tool_names)
    controller = create_loop_controller(
        investigation_tools,
        seed=controller_seed,
        files_expected=files_expected,
        report_delivery=report_delivery,
        short_write_posture=short_write_posture,
        tighten_verify_exec_thrash=tighten_verify_exec_thrash,
        max_rounds=profile.max_rounds,
        form_prose=form_prose,
        product_landing_artifacts=product_landing_artifacts,
    )

    wind_down = LoopWindDown(
        role=role,
        run_id=run_id,
        agent_id=agent_id,
        tools=tools,
        sink=sink,
        messages=messages,
        token_budget=token_budget,
        files_expected=files_expected,
        live_allowed=(list(allowed_tool_names) if allowed_tool_names is not None else None),
        controller=controller,
        refresh_tool_defs=lambda: None,
    )

    def _effective_allowed() -> list[str] | None:
        return wind_down.effective_allowed()

    def _resolve_tool_defs() -> list[dict[str, Any]] | None:
        return resolve_openai_tool_defs(tools, _effective_allowed(), disabled_tools)

    tool_defs: list[dict[str, Any]] | None = _resolve_tool_defs()

    def _refresh_tool_defs() -> None:
        nonlocal tool_defs
        tool_defs = _resolve_tool_defs()

    wind_down.refresh_tool_defs = _refresh_tool_defs

    def _maybe_retire_workspace_channel_dead() -> None:
        """Session/backend sticky-dead → strip file family from offered tools."""
        nonlocal tool_defs
        if apply_workspace_channel_dead_retire(
            disabled_tools=disabled_tools,
            controller=controller,
            tool_context=tool_context,
        ):
            tool_defs = _resolve_tool_defs()

    def _maybe_retire_exec_env_dead() -> None:
        """Session sticky exec-env-dead → strip code_execute/test_run (not terminal)."""
        nonlocal tool_defs
        if apply_exec_env_dead_retire(
            disabled_tools=disabled_tools,
            controller=controller,
            tool_context=tool_context,
        ):
            tool_defs = _resolve_tool_defs()

    # Entry: teammates that never hit a dead envelope still inherit session sticky.
    _maybe_retire_workspace_channel_dead()
    _maybe_retire_exec_env_dead()
    # Nested worker lead may resume with _supervised already set (new react_loop).
    # Promote before the opening observe / first LLM so replan is on the menu
    # (CEO wait 套件 still gated on depth==0 + live session inside promote).
    from agentcore.runtime.resolve.ceo_surface import (
        ensure_coordination_surface_before_llm,
    )

    if ensure_coordination_surface_before_llm(tools):
        tool_defs = _resolve_tool_defs()
    if role == "worker":
        from agentcore.runtime.resolve.ceo_surface import observe_tools_offered

        # Opening offer only (wind_down narrowing is a later round). Pass the
        # resolved defs so allowed/disabled filtering is what the model sees.
        observe_tools_offered(tools, scope="worker_run", tool_defs=tool_defs or [])
    # 跑/修·打开验证·贴码写回：引擎不再扫用户文硬分叉；选型/验收靠提示词 + 结构字段。
    if role == "captain":
        maybe_inject_availability_status_nudge(
            messages=messages,
            run_id=run_id or "",
            role=role,
        )
    active_model: str | None = base_model
    finish_guard_reworks = 0
    ceiling_reason = "max_rounds"
    round_idx = 0

    def _stamp_coord_busy(kind: str) -> None:
        # Same busy channel as idle-patrol; piggyback pass-local rounds + this
        # pass's tokens (executor already stamped prior-pass tokens; session
        # keeps the max so a retry pass cannot wipe run-level spend).
        if role == "captain" or not run_id:
            return
        from agentcore.runtime.coordination.session import note_coord_worker_busy

        note_coord_worker_busy(
            run_id,
            kind,
            rounds_used=round_idx,
            rounds_limit=profile.max_rounds,
            tokens_spent=total_usage.total_tokens,
        )

    def _export_tool_failures() -> None:
        if tool_failure_sink is None:
            return
        tool_failure_sink.clear()
        tool_failure_sink.extend(f.to_dict() for f in controller.tool_failure_facts())

    def _export_controller_seed() -> None:
        if controller_seed_sink is None:
            return
        controller_seed_sink.clear()
        controller_seed_sink.append(dict(controller.export_seed()))

    def _export_terminal_state() -> None:
        """Publish circuit-breaker facts + controller latches for the caller.

        Called from the loop's ``finally`` so EVERY exit publishes them — not just
        the two clean returns but also the abnormal ones (``raise_on_error``
        propagating a round failure, hard-timeout force cancel). Those are exactly
        the runs whose ``tool_failures`` the CEO most needs: a FAILED / CANCELLED
        RunState used to carry an empty list, so the「工具失败」section read
        healthier than the run was. Idempotent — each export clears its sink first.
        """
        _export_tool_failures()
        _export_controller_seed()

    def _exit(
        content: str, reasoning: str, usage: TokenUsage, rounds: int
    ) -> tuple[str, str, TokenUsage, int]:
        """Unified content exit: strip residual vendor tool-protocol markers.

        Every react_loop return (CEO / worker / forced finalize) funnels through
        here so the RETURNED deliverable — the text that is persisted and replayed
        on reload — is clean of stray ``<longcat_tool_call>`` / ``</arg_key>`` /
        ``<｜DSML｜…>`` tags some providers leak into prose. Live ``content_delta``
        was already streamed (接受活体流短暂脏、reload 后干净); we clean only the
        final value and never buffer at the SSE-delta level.
        """
        return prepare_assistant_content(content), reasoning, usage, rounds

    # G4: publish captain live mirror only when role=="captain" — NOT via
    # deliverable_only (workers / debaters also set that flag and nest under the
    # captain Task; gating on it would clobber the captain mirror).
    captain_token = None
    # Classic turn steer (P1): accepting window = captain loop lifetime.
    steer_cid = ""
    if role == "captain":
        captain_token = current_captain_loop.set(CaptainLoopMirror(controller=controller))
        steer_cid = (tool_context.conversation_id or "").strip()
        if steer_cid:
            from agentcore.runtime.turn.steer import begin_accepting

            begin_accepting(steer_cid, execution_id=tool_context.execution_id)

    try:
        for round_idx in range(profile.max_rounds):
            # Hard-timeout entry check BEFORE arming wind-down / LLM: after TIMEOUT
            # grant one grace round; after grace force-cancel (no new LLM/tool).
            hard_break = wind_down.enforce_hard_timeout_entry(tokens=total_usage.total_tokens)
            if hard_break is not None:
                import asyncio

                # The cancel arg IS the wire ``run_cancelled.reason`` (executor.terminal
                # reads ``e.args[0]``), so it must carry the real cause — a hardcoded
                # "redirect" told the user their worker was re-tasked when it was killed
                # on the timeout ceiling. ``ceiling_finalize`` is unreachable from here
                # (post-grace bans new LLM work), hence no ``ceiling_reason`` stamp.
                raise asyncio.CancelledError(hard_break)
            # B·收尾窗口必须先于硬顶：单轮 token 从软顶下方直接越过硬顶时，若先判硬顶
            # break，会整轮跳过 wind_down，随后 force_finalize 禁写 → worker 把
            # file_write 糊成正文 DSML。先武装收尾窗；若本轮刚进入，即使已过硬顶也
            # 先跑这一轮落盘/handoff，下一轮再撞硬顶 finalize。
            already_winding = wind_down.wind_down_active
            wind_down.maybe_arm_wind_down(tokens=total_usage.total_tokens)
            just_armed_wind_down = wind_down.wind_down_active and not already_winding
            # Loose token backstop (Worker 硬顶): stop BEFORE starting a round once the run's
            # cumulative input+output tokens reach the ceiling, so a runaway overshoots by at
            # most one round instead of grinding on (根因: 之前没人比对这个累计数). ``total_usage``
            # is updated at each round's end, so this reflects rounds 0..round_idx-1. 0 =
            # disabled (CEO / solo / tests → bounded only by max_rounds).
            if (
                token_budget > 0
                and total_usage.total_tokens >= token_budget
                and not just_armed_wind_down
            ):
                ceiling_reason = "token_budget"
                logger.warning(
                    "engine.token_budget_exhausted",
                    run_id=run_id,
                    role=role,
                    tokens=total_usage.total_tokens,
                    token_budget=token_budget,
                    round=round_idx,
                )
                break
            if round_sink is not None:
                round_sink[:] = [round_idx + 1]
            logger.debug("react.round_start", round=round_idx, messages=len(messages))
            record_round_start(round_idx=round_idx, run_id=run_id, role=role)
            content_before_round = final_content
            # Update point 1/3: round start (content_before_round + current final_content).
            # Gated on role — nested worker loops must not mutate the captain mirror.
            if role == "captain":
                sync_captain_loop_mirror(
                    content_before_round=content_before_round,
                    final_content=final_content,
                )
            # on_round_begin: before each step AFTER the first (generic hook).
            if round_idx and on_round_begin is not None:
                messages.extend(on_round_begin())

            # 跨回合 append 把宿主 eid 只留在共享 tool context 上（delegate 跑在
            # asyncio.gather 子任务里，它的 ContextVar 写不回父任务）。回绑必须早于本轮
            # 所有按 execution 分流的消费方——插话路由、团队事件等待、wait 工具面都读它；
            # 漏回绑时 CEO 既不等队员也拿不到 wait，只能用正文收口把在跑的队员甩成 detached。
            if role == "captain":
                from agentcore.runtime.resolve.ceo_surface import resync_coordination_binding

                resync_coordination_binding(tools)

            # Classic turn steer (P1 · 同对话再发): drain mid-turn user supplements at
            # every step top (incl. round 0), AFTER on_round_begin and BEFORE LLM.
            # Parallel to coordination inject below — do NOT merge / fake coord_inject.
            if role == "captain" and steer_cid:
                from agentcore.runtime.coordination.session import current_execution_id
                from agentcore.runtime.turn.steer import drain_injected

                steer_msgs = await drain_injected(
                    steer_cid,
                    sink=sink,
                    execution_id=current_execution_id.get() or tool_context.execution_id,
                )
                if steer_msgs:
                    messages.extend(steer_msgs)
                    logger.info(
                        "engine.turn_steer_inject",
                        round=round_idx,
                        injected=len(steer_msgs),
                        conversation_id=steer_cid,
                    )

            # CEO 协调模式 Phase 2: only the captain consumes team events (workers share
            # the ContextVar but must not block on the coordination queue).
            if role == "captain":
                from agentcore.runtime.coordination.wait import await_coordination_injection

                coord_t0 = time.perf_counter()
                coord_msgs = await await_coordination_injection(messages)
                coord_ms = int((time.perf_counter() - coord_t0) * 1000)
                if coord_ms >= 50 or coord_msgs:
                    logger.info(
                        "engine.coord_inject",
                        round=round_idx,
                        waited_ms=coord_ms,
                        injected=len(coord_msgs),
                    )
                if coord_msgs:
                    messages.extend(coord_msgs)
                    # Soft gates (all_completed): remind before synthesis / wrap-up
                    # while CEO is still in coordination — not only on no-tool Return.
                    # Debate-commitment before audit (same order as soft_gates.py).
                    # Turn-token wrap-up first: when ceiling is hit, audit/debate gates
                    # are suppressed (cannot dispatch) — steer CEO to close on output.
                    maybe_inject_turn_token_budget_gate(
                        controller,
                        messages=messages,
                        run_id=run_id,
                        round_idx=round_idx,
                        role=role,
                    )
                    if coordination_injection_has_all_completed(coord_msgs):
                        maybe_inject_debate_gate(
                            controller,
                            messages=messages,
                            run_id=run_id,
                            round_idx=round_idx,
                            role=role,
                        )
                        maybe_inject_audit_gate(
                            controller,
                            messages=messages,
                            run_id=run_id,
                            round_idx=round_idx,
                            role=role,
                        )
                else:
                    # No coordination wake this round — still steer if ceiling already hit
                    # (e.g. reject path / resume seed over ceiling before next think).
                    maybe_inject_turn_token_budget_gate(
                        controller,
                        messages=messages,
                        run_id=run_id,
                        round_idx=round_idx,
                        role=role,
                    )

            # Coordination idle-patrol: stamp worker LLM/tool busy so a quiet
            # event queue does not wake the CEO while teammates are still working.
            _stamp_coord_busy("llm")
            if role != "captain" and run_id:
                from agentcore.runtime.runs.run_phase_emit import emit_run_phase

                emit_run_phase(sink, run_id, agent_id, "thinking")
            # 协调已活 / 嵌套 lead 已有子计划 → 进入本轮 LLM 前装好闸内工具
            # （CEO wait 套件；nested worker 仅 replan）。不按 role 跳过：
            # worker 续跑时 _supervised 可能已在，须赶在首轮 LLM 前挂上。
            from agentcore.runtime.resolve.ceo_surface import (
                ensure_coordination_surface_before_llm,
            )

            if ensure_coordination_surface_before_llm(tools):
                tool_defs = _resolve_tool_defs()
            # Sticky channel-dead / exec-env-dead poll immediately before LLM
            # (session-read posture like timeout wind_down): sibling may have
            # stamped after prior round / on_round_begin.
            _maybe_retire_workspace_channel_dead()
            _maybe_retire_exec_env_dead()
            try:
                round_result = await run_llm_round(
                    llm=llm,
                    profile=profile,
                    messages=messages,
                    investigation_tools=investigation_tools,
                    tool_defs=tool_defs,
                    active_model=active_model,
                    emit_content=emit_content,
                    emit_reasoning=emit_reasoning,
                    on_tool_progress=on_tool_progress,
                    round_idx=round_idx,
                    run_id=run_id,
                    raise_on_error=raise_on_error,
                    on_reset=emit_reset,
                )
            finally:
                if role != "captain" and run_id:
                    from agentcore.runtime.coordination.session import (
                        clear_coord_worker_busy,
                    )

                    clear_coord_worker_busy(run_id)

            if isinstance(round_result, LlmRoundFailure):
                # Hard LLM failure (non-raising path): the provider already exhausted its
                # network retries. End on ERROR/DEGRADED (error surfaced in the Return arm).
                final_content = maybe_salvage_captain_reply(
                    final_content=final_content, messages=messages, role=role
                )
                error_message = round_result.error_message
                if not (final_content or "").strip() and _messages_have_tool_progress(
                    messages
                ):
                    error_message = overlay_progress_failure_message(
                        code=round_result.error_code,
                        message=error_message,
                        context=round_result.error_context,
                    )
                outcome = RoundOutcome(
                    content="",
                    reasoning="",
                    usage=None,
                    llm_failed=True,
                    error_code=round_result.error_code,
                    error_message=error_message,
                    error_context=round_result.error_context,
                )
                directive: LoopDirective = decide_llm_failure(
                    final_content=final_content,
                    error_code=round_result.error_code or "",
                    role=role,
                )
            elif round_result.aborted:
                # Post-commit disconnect / stall: keep the partial prose and finish
                # DEGRADED (resume entry stays available via existing infrastructure).
                usage = round_result.usage
                if usage:
                    total_usage = total_usage + usage
                if usage_sink is not None:
                    usage_sink[:] = [total_usage]
                if round_result.content:
                    final_content = join_segments(final_content, round_result.content)
                    # Update point 2/3: prose join.
                    if role == "captain":
                        sync_captain_loop_mirror(final_content=final_content)
                if round_result.reasoning:
                    final_reasoning += round_result.reasoning
                outcome = RoundOutcome(
                    content=round_result.content,
                    reasoning=round_result.reasoning,
                    usage=usage,
                    llm_failed=True,
                    error_code=ErrorCode.LLM_ERROR,
                    error_message="模型响应中断，已保留已生成内容，可继续。",
                )
                final_content = maybe_salvage_captain_reply(
                    final_content=final_content, messages=messages, role=role
                )
                directive = decide_llm_failure(
                    final_content=final_content,
                    error_code=ErrorCode.LLM_ERROR,
                    role=role,
                )
            else:
                usage = round_result.usage
                if usage:
                    total_usage = total_usage + usage
                if usage_sink is not None:
                    usage_sink[:] = [total_usage]

                if round_result.content:
                    final_content = join_segments(final_content, round_result.content)
                    # Update point 2/3: prose join.
                    if role == "captain":
                        sync_captain_loop_mirror(final_content=final_content)
                if round_result.reasoning:
                    final_reasoning += round_result.reasoning

                outcome = RoundOutcome(
                    content=round_result.content,
                    reasoning=round_result.reasoning,
                    usage=usage,
                    tool_calls=round_result.tool_calls,
                    empty_diagnosis=round_result.empty_diagnosis,
                    empty_raw_preview=round_result.empty_raw_preview,
                    finish_reason=round_result.finish_reason,
                    provider_base_url=round_result.provider_base_url,
                )
                # 协调监听豁免：captain 在活跃协调中对纯进展事件保持静默（无正文、无工具）
                # 是被指引的合法行为，不进 B2 空响应梯子；ALL_COMPLETED 注入即关闭 session，
                # 终稿阶段的空响应仍按原梯子降级收口。
                # length+空正文：不再豁免（截断不会因 Continue 变好，避免再挂墙钟）。
                counts_as_empty = outcome.is_empty
                if counts_as_empty and role == "captain" and outcome.finish_reason != "length":
                    from agentcore.runtime.coordination.session import active_coordination

                    coord_session = active_coordination()
                    if coord_session is not None and coord_session.active:
                        counts_as_empty = False
                        logger.info(
                            "engine.coordination_listen",
                            round=round_idx,
                            execution_id=coord_session.execution_id,
                        )
                controller.note_empty_round(counts_as_empty)

                if not outcome.has_tool_calls:
                    directive = decide_no_tool_round(
                        outcome,
                        final_content=final_content,
                        controller=controller,
                        annotate_citations=annotate_citations,
                        citation_sink=citation_sink,
                        finish_guard_reworks=finish_guard_reworks,
                        tools_offered=tool_defs is not None,
                        supports_tools=supports_tools,
                        turn_evidence_ledger=turn_evidence_ledger,
                        promotion_ledger=tool_context.promotion_ledger,
                    )
                    # Soft debate-commitment / audit-gate: captain wrap-up —
                    # discard the draft, inject nudge, continue (one-shot each).
                    directive, rolled = maybe_soft_gate_no_tool_return(
                        directive=directive,
                        outcome=outcome,
                        controller=controller,
                        messages=messages,
                        role=role,
                        round_idx=round_idx,
                        run_id=run_id,
                        content_before_round=content_before_round,
                        emit_reset=emit_reset,
                    )
                    if rolled is not None:
                        final_content = rolled
                else:
                    # Wind-down breach: non-whitelist tool → nudge+handoff-only, or
                    # local synth close (2nd breach / already at hard ceiling).
                    breach = wind_down.apply_tool_breach(outcome, tokens=total_usage.total_tokens)
                    outcome = breach.outcome
                    skip_tool_exec = breach.skip_tool_exec
                    if breach.directive is not None:
                        directive = breach.directive
                    if not skip_tool_exec:
                        _stamp_coord_busy("tool")
                        try:
                            tool_round = await handle_tool_calls_round(
                                outcome=outcome,
                                messages=messages,
                                tools=tools,
                                tool_context=tool_context,
                                sink=sink,
                                approval_gate=approval_gate,
                                citation_sink=citation_sink,
                                annotate_citations=annotate_citations,
                                turn_evidence_ledger=turn_evidence_ledger,
                                ledger_registrant=ledger_registrant,
                                run_id=run_id,
                                role=role,
                                gate_escalation_sink=gate_escalation_sink,
                                deliverable_only=deliverable_only,
                                on_reset=on_reset,
                                emit_reset=emit_reset,
                                content_before_round=content_before_round,
                                final_content=final_content,
                                round_result_content=round_result.content,
                                total_usage=total_usage,
                                controller=controller,
                                allowed_tool_names=_effective_allowed(),
                                disabled_tools=disabled_tools,
                                round_idx=round_idx,
                            )
                        finally:
                            if role != "captain" and run_id:
                                from agentcore.runtime.coordination.session import (
                                    clear_coord_worker_busy,
                                )

                                clear_coord_worker_busy(run_id)
                        outcome = tool_round.outcome
                        directive = tool_round.directive
                        final_content = tool_round.final_content
                        total_usage = tool_round.total_usage
                        if tool_round.tool_defs_changed:
                            tool_defs = tool_round.tool_defs
                        # Delivery-idle tool narrow（factory 交文件空转已关；
                        # 显式构造仍可能 latch；可复用 wind_down 白名单）。
                        if (
                            role == "worker"
                            and controller is not None
                            and controller.take_delivery_idle_narrow_apply()
                        ):
                            wind_down.apply_delivery_idle_narrow()
                        wind_down.inject_pending_breach_nudge(directive)

            applied = await apply_loop_directive(
                directive=directive,
                outcome=outcome,
                messages=messages,
                llm=llm,
                tools=tools,
                tool_context=tool_context,
                sink=sink,
                profile=profile,
                active_model=active_model,
                base_model=base_model,
                allowed_tool_names=_effective_allowed(),
                disabled_tools=disabled_tools,
                emit_content=emit_content,
                emit_reasoning=emit_reasoning,
                emit_reset=emit_reset,
                final_content=final_content,
                final_reasoning=final_reasoning,
                total_usage=total_usage,
                round_idx=round_idx,
                run_id=run_id,
                role=role,
                finish_override_sink=finish_override_sink,
                approval_gate=approval_gate,
                citation_sink=citation_sink,
                annotate_citations=annotate_citations,
                turn_evidence_ledger=turn_evidence_ledger,
                ledger_registrant=ledger_registrant,
                gate_escalation_sink=gate_escalation_sink,
                controller=controller,
                content_before_round=content_before_round,
                finish_guard_reworks=finish_guard_reworks,
                files_expected=files_expected,
                form_prose=form_prose,
            )
            if applied.action == "return":
                return _exit(
                    applied.content,
                    applied.reasoning,
                    applied.usage or total_usage,
                    applied.rounds,
                )
            final_content = applied.final_content
            final_reasoning = applied.final_reasoning
            if applied.total_usage is not None:
                total_usage = applied.total_usage
            finish_guard_reworks = applied.finish_guard_reworks
            if applied.tool_defs_changed:
                tool_defs = applied.tool_defs
            # Finalize-path govern may also latch delivery-idle narrow
            # (explicit construction only; factory 交文件空转已关).
            if (
                role == "worker"
                and controller is not None
                and controller.take_delivery_idle_narrow_apply()
            ):
                wind_down.apply_delivery_idle_narrow()
            # Close the post-TIMEOUT grace round so the next entry force-cancels.
            if role == "worker" and run_id:
                from agentcore.runtime.runs.timeout_hard import (
                    HardTimeoutPhase,
                    get_hard_timeout,
                )

                _guard = get_hard_timeout(run_id)
                if _guard is not None and _guard.phase is HardTimeoutPhase.GRACE:
                    _guard.end_grace_round()
            # Retrieval budget exhausted → enter wind-down early (don't wait for
            # wall-clock TIMEOUT while the worker can no longer search).
            # Still open → refresh the balance the worker plans against next round.
            rb = getattr(tool_context, "retrieval_budget", None)
            if (
                role == "worker"
                and not wind_down.wind_down_active
                and rb is not None
                and rb.limit > 0
                and rb.remaining <= 0
            ):
                wind_down.enter_wind_down("retrieval_budget", tokens=total_usage.total_tokens)
                logger.info(
                    "engine.retrieval_budget_wind_down",
                    run_id=run_id,
                    limit=rb.limit,
                    used=rb.used,
                )
            if role == "worker" and rb is not None and wind_down.wind_down_active:
                # 收尾窗口已禁检索：上一轮那条余额播报会跟「禁止再检索」自相矛盾。
                from agentcore.runtime.runs.retrieval_budget import (
                    drop_retrieval_budget_awareness,
                )

                drop_retrieval_budget_awareness(messages)
            elif role == "worker" and rb is not None:
                # 预算感知 (BATS): a worker that already spent slots sees its balance
                # + per-tool spend every round, else it searches blind. Never spent →
                # no injection (多数 worker 一次都不检索，注入是纯噪音). Critical (剩 ≤2)
                # rides the same single message.
                from agentcore.runtime.runs.retrieval_budget import (
                    sync_retrieval_budget_awareness,
                )

                awareness = sync_retrieval_budget_awareness(messages, rb)
                if awareness is not None:
                    if awareness.critical and not retrieval_critical_warned:
                        retrieval_critical_warned = True
                        logger.info(
                            "engine.retrieval_budget_critical",
                            run_id=run_id,
                            remaining=awareness.remaining,
                            limit=awareness.limit,
                            used=awareness.used,
                        )
                    spend = (awareness.searches, awareness.reads)
                    # Trajectory rows only when the split moved (a row per round would
                    # repeat itself); the ``final=True`` row at exit is the one that
                    # 分工具用量分布 aggregates on.
                    if spend != retrieval_spend_logged:
                        retrieval_spend_logged = spend
                        logger.info(
                            "engine.retrieval_budget_awareness",
                            run_id=run_id,
                            round=round_idx,
                            limit=awareness.limit,
                            used=awareness.used,
                            searches=awareness.searches,
                            reads=awareness.reads,
                            remaining=awareness.remaining,
                            critical=awareness.critical,
                            final=False,
                        )
            continue

        result = await ceiling_finalize(
            messages=messages,
            llm=llm,
            profile=profile,
            active_model=active_model,
            base_model=base_model,
            tools=tools,
            allowed_tool_names=_effective_allowed(),
            disabled_tools=disabled_tools,
            emit_content=emit_content,
            emit_reasoning=emit_reasoning,
            emit_reset=emit_reset,
            final_content=final_content,
            final_reasoning=final_reasoning,
            total_usage=total_usage,
            ceiling_reason=ceiling_reason,
            round_idx=round_idx,
            role=role,
            run_id=run_id,
            token_budget=token_budget,
            controller=controller,
            tool_context=tool_context,
            sink=sink,
            finish_override_sink=finish_override_sink,
            approval_gate=approval_gate,
            citation_sink=citation_sink,
            annotate_citations=annotate_citations,
            turn_evidence_ledger=turn_evidence_ledger,
            ledger_registrant=ledger_registrant,
            gate_escalation_sink=gate_escalation_sink,
            cutoff_reason_sink=cutoff_reason_sink,
            files_expected=files_expected,
            form_prose=form_prose,
        )
        return _exit(*result)
    finally:
        _export_terminal_state()
        # 分工具用量分布的落账行：轮尾埋点跑不到末轮（末轮搜完就交卷 / 被取消 / 撞硬顶），
        # 每个花过额度的 worker 在这里留一行最终分项，一行一 run 直接聚合。
        rb_exit = getattr(tool_context, "retrieval_budget", None)
        if role == "worker" and rb_exit is not None and rb_exit.used > 0:
            from agentcore.runtime.runs.retrieval_budget import (
                is_retrieval_budget_critical,
            )

            logger.info(
                "engine.retrieval_budget_awareness",
                run_id=run_id,
                round=round_idx,
                limit=rb_exit.limit,
                used=rb_exit.used,
                searches=rb_exit.searches_used,
                reads=rb_exit.reads_used,
                remaining=rb_exit.remaining,
                critical=is_retrieval_budget_critical(rb_exit.remaining, limit=rb_exit.limit),
                final=True,
            )
        if steer_cid:
            from agentcore.runtime.turn.runs import turn_runs
            from agentcore.runtime.turn.steer import (
                discard_leftovers_on_user_stop,
                end_accepting,
                promote_leftovers_to_queue,
            )

            leftovers = end_accepting(steer_cid)
            if leftovers:
                # Stop = silent: unread classic steers must not become a new turn.
                # User-initiated FIFO is untouched (Stop ≠ 取消排队).
                if turn_runs.is_user_stop(steer_cid) or turn_runs.is_superseded(steer_cid):
                    discard_leftovers_on_user_stop(leftovers)
                else:
                    promote_leftovers_to_queue(leftovers)
        if captain_token is not None:
            current_captain_loop.reset(captain_token)
