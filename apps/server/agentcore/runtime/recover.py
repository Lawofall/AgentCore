"""Single recover primitive: journal projection → seed WaveScheduler → settle / redrive.

Resume (plan_review / team_preview / ask_user) and crash redrive both route through
:func:`recover_turn`. The journal remains the唯一事实源;
:class:`~agentcore.runtime.turn.state.TurnState` is the sole projection entry.

This module owns the user-driven resume side. The sweeper-driven crash entry
(:func:`recover_expired_lease` — lease identity, badge, salvage) lives in
:mod:`agentcore.runtime.recover_lease` and is re-exported here for callers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolEffect
from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
from agentcore.runtime.events import (
    EventSink,
    checkpoint_resolved,
    plan_review_resolved,
    team_preview_resolved,
)
from agentcore.runtime.recover_lease import bind_recovered_turn, recover_expired_lease
from agentcore.runtime.suspension import (
    AskUserSuspension,
    PlanReviewSuspension,
    TeamPreviewSuspension,
    TurnSuspension,
)
from agentcore.runtime.turn.state import TurnState
from agentcore.tools.builtin.ask_user.schema import option_label

if TYPE_CHECKING:
    from agentcore.tools.builtin.debate import DebateTool
    from agentcore.tools.builtin.delegate import DelegateTool

logger = get_logger(__name__)

__all__ = ["SettledSuspension", "recover_expired_lease", "recover_turn"]


class SettledSuspension(NamedTuple):
    """Outcome of recover/settle — tool result text (+ optional terminal reply).

    ``effect`` mirrors the settled tool's :class:`ToolEffect` so the cold resume
    path can honor re-entrant SUSPEND (downstream checkpoint while ``resume_plan``
    runs) the same way the live engine does — PAUSED, no CEO continuation.
    ``terminal_text`` is set only when settle returns a terminal ``INTERACT``
    effect (in-band closing without another CEO round). First ask_user /
    team_preview STOP feeds CONTINUE like timeout / kickoff cancel; a second
    consecutive STOP in the same turn upgrades to ``INTERACT`` (no CEO round).
    team_preview ``ADJUST`` shares the no-grant feed-CEO path but never upgrades.
    """

    output: str
    terminal_text: str | None
    effect: ToolEffect = ToolEffect.CONTINUE


async def recover_turn(
    *,
    state: TurnState,
    sink: EventSink,
    delegate_tool: DelegateTool,
    execution_id: str,
    suspension: TurnSuspension | None = None,
    decision: CheckpointDecision | None = None,
    note: str = "",
    selected: list[str] | None = None,
    debate_tool: DebateTool | None = None,
    excluded_run_ids: list[str] | None = None,
    write_capability_overrides: list[dict[str, str]] | None = None,
    model_overrides: dict[str, dict[str, str]] | None = None,
) -> SettledSuspension:
    """Settle a resume decision or CONTINUE-redrive unfinished DAG from ``state``.

    - With ``suspension`` + ``decision``: resume three kinds (behaviour-equivalent to
      the former ``settle_resumed_suspension``).
    - Without suspension (crash): ``decision`` defaults to CONTINUE; redrives unfinished
      plan nodes with ``seed_completed=state.completed`` (completed nodes skipped).
    - ``debate_tool`` is required when settling a ``team_preview`` with
      ``primitive=debate``.
    - ``excluded_run_ids`` / ``write_capability_overrides`` apply only to delegate
      ``team_preview`` continue (开工组队有限否决).
    - ``model_overrides`` apply to delegate continue (人盖队员) **and** debate
      continue (人盖辩手 / 主持人 → debate_arguments)；其它 kind / stop ignore.
    """
    if suspension is not None:
        if decision is None:
            raise ValueError("recover_turn resume requires decision")
        return await _settle_resume(
            suspension,
            state=state,
            decision=decision,
            note=note,
            selected=selected or [],
            sink=sink,
            delegate_tool=delegate_tool,
            debate_tool=debate_tool,
            execution_id=execution_id,
            excluded_run_ids=excluded_run_ids or [],
            write_capability_overrides=write_capability_overrides or [],
            model_overrides=model_overrides or {},
        )

    decision = decision or CheckpointDecision.CONTINUE
    plan = state.plan
    if plan is None:
        raise ValueError("recover_turn crash redrive requires a plan projection")
    eid = state.execution_id or execution_id
    seed = dict(state.completed)
    # Crash mid-flight teams were typically wall-coordinated (≥1 worker default). Align the
    # redrive with that product default — resume_plan's coordinate default is False
    # (classic / plan_review), which would silently strip note-wall semantics.
    logger.info(
        "recover.crash_redrive",
        execution_id=eid,
        completed=len(seed),
        unfinished=len(state.unfinished_run_ids),
        decision=decision.value,
        coordinate=True,
        coordination="wall",
    )
    delegate_result = await delegate_tool.resume_plan(
        plan,
        seed,
        decision=decision,
        note=note,
        checkpoint_run_ids=set(),
        execution_id=eid,
        coordinate=True,
        coordination="wall",
    )
    bind_recovered_turn(eid, sink)
    return SettledSuspension(delegate_result.output, None, delegate_result.effect)


async def _settle_resume(
    suspension: TurnSuspension,
    *,
    state: TurnState,
    decision: CheckpointDecision,
    note: str,
    selected: list[str],
    sink: EventSink,
    delegate_tool: DelegateTool,
    debate_tool: DebateTool | None,
    execution_id: str,
    excluded_run_ids: list[str] | None = None,
    write_capability_overrides: list[dict[str, str]] | None = None,
    model_overrides: dict[str, dict[str, str]] | None = None,
) -> SettledSuspension:
    """Kind-specific resume settle, projecting exclusively via ``state``."""
    from agentcore.runtime.checkpoint_stop_streak import (
        compose_repeated_stop_closing,
        consecutive_checkpoint_stops,
        is_repeated_checkpoint_stop,
    )

    # research_first 仅辩论开工卡合法；其它挂起点降级为 STOP，不得静默 continue。
    if decision is CheckpointDecision.RESEARCH_FIRST and not (
        isinstance(suspension, TeamPreviewSuspension) and suspension.primitive == "debate"
    ):
        logger.warning(
            "team_preview.research_first_rejected",
            kind=getattr(suspension.kind, "value", suspension.kind),
            primitive=getattr(suspension, "primitive", None),
        )
        decision = CheckpointDecision.STOP

    # Same-turn consecutive STOP → terminal INTERACT (no further CEO round).
    # Streak is journal-derived so it survives suspend/resume; first STOP unchanged.
    # ADJUST is excluded inside the helper (team_preview revise can repeat).
    # Ignore the card being settled — cold-path prewrite of this checkpoint_id
    # must not count as a prior STOP (first cancel would otherwise INTERACT).
    force_close = is_repeated_checkpoint_stop(
        suspension.journal_entries,
        decision,
        ignore_checkpoint_id=suspension.checkpoint_id,
    )
    prior_stops = (
        consecutive_checkpoint_stops(
            suspension.journal_entries,
            ignore_checkpoint_id=suspension.checkpoint_id,
        )
        if force_close
        else 0
    )

    def _after_settle(settled: SettledSuspension) -> SettledSuspension:
        if not force_close or settled.effect is not ToolEffect.CONTINUE:
            return settled
        closing = compose_repeated_stop_closing(note=note)
        logger.info(
            "checkpoint.repeated_stop_close",
            checkpoint_id=suspension.checkpoint_id,
            kind=getattr(suspension.kind, "value", suspension.kind),
            prior_stops=prior_stops,
        )
        return SettledSuspension(settled.output, closing, ToolEffect.INTERACT)

    if isinstance(suspension, AskUserSuspension):
        response = CheckpointResponse(
            decision=decision,
            note=note,
            selected=list(selected),
        )
        allowed = {option_label(o) for q in suspension.questions for o in q.get("options", [])}
        response.selected = [s for s in response.selected if s in allowed]
        if (
            suspension.intent == "organize_plan"
            and response.decision is CheckpointDecision.CONTINUE
        ):
            from agentcore.tools.builtin.ask_user.card import option_to_organize_op
            from agentcore.workspace.organize_plan_store import register_plan

            kept: list[dict] = []
            for q in suspension.questions:
                for o in q.get("options") or []:
                    if not isinstance(o, dict):
                        continue
                    if option_label(o) not in response.selected:
                        continue
                    op = option_to_organize_op(o)
                    if op:
                        kept.append(op)
            register_plan(
                plan_id=suspension.checkpoint_id,
                conversation_id=suspension.conversation_id,
                operations=kept,
            )
        daily_review_apply = None
        if suspension.intent == "daily_review" and response.decision is CheckpointDecision.CONTINUE:
            from agentcore.standing_tasks.review_apply import (
                apply_daily_review_selections,
            )

            daily_review_apply = await apply_daily_review_selections(
                user_id=suspension.user_id,
                folder_id=suspension.folder_id or "",
                conversation_id=suspension.conversation_id,
                questions=list(suspension.questions or []),
                selected_labels=list(response.selected),
            )
        sink.emit(
            checkpoint_resolved(
                checkpoint_id=suspension.checkpoint_id,
                decision=response.decision.value,
                note=response.note,
                selected=response.selected,
            )
        )
        # CONTINUE only: rebuild the mid-flight team from journal (P0-B).
        # STOP / TIMEOUT must not try_start_coordination or set_active_coordination —
        # attaching a session here would detach unfinished workers to background
        # on turn_released.
        if response.decision is CheckpointDecision.CONTINUE:
            from agentcore.runtime.coordination.session import (
                CoordinationSession,
                current_execution_id,
                set_active_coordination,
            )

            snap = state.coordination
            if snap is not None and snap.active:
                session = CoordinationSession.from_snapshot(snap)
                plan = state.plan
                seed = dict(state.completed)
                for rid in seed:
                    session.mark_worker_completed(rid)
                unfinished = plan is not None and any(
                    n.run_id not in seed for n in plan.nodes
                )
                if snap.execution_id:
                    current_execution_id.set(snap.execution_id)
                    base_ctx = getattr(delegate_tool, "_base_tool_context", None)
                    if base_ctx is not None:
                        base_ctx.execution_id = snap.execution_id
                if unfinished and plan is not None:
                    from agentcore.runtime.coordination.host import try_start_coordination

                    try_start_coordination(
                        delegate_tool,
                        plan,
                        execution_id=snap.execution_id,
                        seed_completed=seed,
                        seed_notes=None,
                        complexity_hint="standard",
                        # Prefer the in-process mode from the live tool; missing (process
                        # restart) → wall so mid-flight teams keep the prior default.
                        coordination=getattr(delegate_tool, "_coordination", None) or "wall",
                        call_idx=0,
                        coordinate=True,
                        session=session,
                    )
                else:
                    set_active_coordination(session)
        from agentcore.tools.builtin.ask_user import ask_user_tool_result
        from agentcore.tools.builtin.ask_user.result import (
            ask_user_daily_review_result,
            ask_user_organize_plan_result,
        )

        if suspension.intent == "organize_plan":
            from agentcore.workspace.organize_plan_store import get_plan

            org_plan = get_plan(suspension.checkpoint_id)
            kept_n = len(org_plan.operations) if org_plan else 0
            result = ask_user_organize_plan_result(
                response,
                plan_id=suspension.checkpoint_id,
                kept_count=kept_n,
            )
        elif suspension.intent == "daily_review":
            applied = daily_review_apply.applied if daily_review_apply else 0
            skipped = daily_review_apply.skipped if daily_review_apply else 0
            errors = daily_review_apply.errors if daily_review_apply else ()
            result = ask_user_daily_review_result(
                response,
                applied=applied,
                skipped=skipped,
                errors=errors,
            )
        else:
            result = ask_user_tool_result(
                response,
                questions=list(suspension.questions or []),
                assumptions=list(suspension.assumptions or []),
            )
            # 场面账（style / presentation_format / automation_delivery）已拆除：
            # resume 不再 record_*；DESIGN 默认风格由 design_prompt_block 软注入。
        terminal = result.final_text if result.effect is ToolEffect.INTERACT else None
        return _after_settle(SettledSuspension(result.output, terminal, result.effect))

    if isinstance(suspension, PlanReviewSuspension):
        sink.emit(
            plan_review_resolved(
                checkpoint_id=suspension.checkpoint_id,
                decision=decision.value,
                note=note,
            )
        )
        logger.info(
            "plan_review.resolved",
            checkpoint_id=suspension.checkpoint_id,
            decision=decision.value,
        )
        seed_completed = dict(state.completed) or suspension.completed
        plan = state.plan or suspension.plan
        eid = state.execution_id or execution_id
        delegate_result = await delegate_tool.resume_plan(
            plan,
            seed_completed,
            decision=decision,
            note=note,
            checkpoint_run_ids=suspension.checkpoint_run_ids,
            execution_id=eid,
            # 帧回灌批次协作参数：恢复用全新 DelegateTool（_coordination 缺省 none），
            # 不回灌则复核后续波次的 worker 被剥便签三件套。
            coordination=suspension.coordination,
            team_brief=suspension.team_brief,
            # CONTINUE 时读帧上 ceo_review → llm 压缩注入 gate_notes（deterministic 不下发）。
            ceo_review=suspension.ceo_review,
        )
        return _after_settle(
            SettledSuspension(delegate_result.output, None, delegate_result.effect)
        )

    if isinstance(suspension, TeamPreviewSuspension):
        from agentcore.runtime.kickoff.team_veto import (
            apply_debate_model_overrides,
            apply_team_preview_veto,
            should_apply_debate_model_overrides,
            should_apply_team_veto,
            validate_debate_model_overrides,
            validate_team_preview_veto,
            veto_summary_for_resolved,
        )

        excl_for_event: list[str] | None = None
        overrides_for_event: list[dict[str, str]] | None = None
        model_for_event: dict[str, dict[str, str]] | None = None
        apply_veto = should_apply_team_veto(suspension, decision)
        apply_debate_models = should_apply_debate_model_overrides(suspension, decision)
        seed_completed = dict(state.completed) or suspension.completed
        plan = state.plan or suspension.plan
        # 开工组队有限否决 + 人盖模型：validate+apply 必须在 emit resolved 之前——
        # 非法修正不得先落事件。冷启动 explore≥2 闸只在 delegate.execute，不挡本卡剪枝后的 resume。
        if apply_veto:
            validate_team_preview_veto(
                plan,
                excluded_run_ids=excluded_run_ids,
                write_capability_overrides=write_capability_overrides,
                model_overrides=model_overrides,
            )
            apply_team_preview_veto(
                plan,
                excluded_run_ids=excluded_run_ids,
                write_capability_overrides=write_capability_overrides,
                model_overrides=model_overrides,
                seed_completed=seed_completed,
            )
            excl_for_event, overrides_for_event, model_for_event = veto_summary_for_resolved(
                excluded_run_ids=excluded_run_ids,
                write_capability_overrides=write_capability_overrides,
                model_overrides=model_overrides,
            )
            excl_for_event = excl_for_event or None
            overrides_for_event = overrides_for_event or None
            model_for_event = model_for_event or None
            # 冷 resume 重建 router 只挂 Worker 槽：plan 上 CEO 已写 / 人盖后的
            # 路由键都须补 extras，否则云端 in-process 跨 origin 软丢进 default。
            # Sidecar proxy 按请求自解析，不依赖此。
            from agentcore.runtime.debate.models import identity_from_route_key
            from agentcore.runtime.delegate.task_models import (
                ensure_delegate_route_extras,
            )

            idents = []
            for node in plan.nodes:
                raw = str(getattr(node, "model", "") or "").strip()
                if not raw:
                    continue
                ident = identity_from_route_key(raw)
                if not ident.is_empty() and ident.origin:
                    idents.append(ident)
            if idents:
                ctx = getattr(delegate_tool, "_base_tool_context", None)
                uid = str(getattr(ctx, "user_id", "") or "") or None
                await ensure_delegate_route_extras(
                    delegate_tool._llm,
                    idents,
                    user_id=uid,
                )
        elif apply_debate_models:
            validate_debate_model_overrides(
                suspension.sides,
                debate_arguments=suspension.debate_arguments,
                model_overrides=model_overrides,
            )
            model_for_event = apply_debate_model_overrides(
                suspension.debate_arguments,
                model_overrides,
                sides=suspension.sides,
            ) or None

        sink.emit(
            team_preview_resolved(
                checkpoint_id=suspension.checkpoint_id,
                decision=decision.value,
                note=note,
                excluded_run_ids=excl_for_event,
                write_capability_overrides=overrides_for_event,
                model_overrides=model_for_event,
            )
        )
        logger.info(
            "team_preview.resolved",
            checkpoint_id=suspension.checkpoint_id,
            decision=decision.value,
            primitive=suspension.primitive,
            excluded=len(excl_for_event or []),
            write_overrides=len(overrides_for_event or []),
            model_overrides=len(model_for_event or {}),
        )
        if suspension.primitive == "debate":
            if debate_tool is None:
                raise ValueError("recover_turn debate kickoff requires debate_tool")
            debate_result = await debate_tool.resume_after_kickoff(
                decision=decision,
                note=note,
                arguments=dict(suspension.debate_arguments),
            )
            # STOP / RESEARCH_FIRST / ADJUST：tool result 回灌 CEO 续跑（terminal_text=None）；
            # 同回合连续第二次 STOP 由 ``_after_settle`` 升格 INTERACT；ADJUST 不计入。
            return _after_settle(
                SettledSuspension(debate_result.output, None, debate_result.effect)
            )

        eid = state.execution_id or execution_id
        # Preview hung before the coordinate fork; CONTINUE must arm the
        # background scheduler (product default for ≥1 worker).
        # ADJUST / STOP / TIMEOUT：不 grant、不 drive。RESEARCH_FIRST 已在入口降级为 STOP。
        delegate_result = await delegate_tool.resume_plan(
            plan,
            seed_completed,
            decision=decision,
            note=note,
            checkpoint_run_ids=suspension.checkpoint_run_ids,
            execution_id=eid,
            coordinate=True,
            apply_kickoff_grant=True,
            # 帧回灌批次协作参数：开工卡挂在 setup_note_wall 之前，coordination /
            # team_brief / seed_notes 只存在帧里；不回灌则 wall 批降级 none（worker
            # 无便签三件套、CEO 预贴便签丢失）——2026-07-20 P2 手驱真跑抓获。
            coordination=suspension.coordination,
            team_brief=suspension.team_brief,
            seed_notes=list(suspension.seed_notes),
        )
        return _after_settle(
            SettledSuspension(delegate_result.output, None, delegate_result.effect)
        )

    raise ValueError(f"unknown suspension kind: {suspension.kind!r}")
