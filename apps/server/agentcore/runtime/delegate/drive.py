"""WaveScheduler drive loop for a delegate plan.

Public entry points (:func:`drive`, :func:`drive_coordinated`) stay here so
external imports remain stable. Phase helpers live in sibling ``drive_*`` modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.runtime.delegate.drive_finalize import finalize_drive
from agentcore.runtime.delegate.drive_preview import team_preview_before_workers
from agentcore.runtime.delegate.drive_redirect import RedirectController
from agentcore.runtime.delegate.drive_setup import (
    apply_delegation_grant,
    build_drive_executor,
    resolve_on_boundary,
    resolve_worker_gate,
)
from agentcore.runtime.delegate.drive_terminal import post_session_all_completed
from agentcore.runtime.events import run_skipped
from agentcore.runtime.runs.drive_reach import register_drive, unregister_drive
from agentcore.runtime.runs.types import RunPhase, RunState
from agentcore.tools.protocol import ToolResult

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

# Drive host is the tools-side DelegateTool instance (duck-typed; no tools import).
type DelegateTool = Any

# Re-export for tests / callers that imported private helpers from this module.
_team_preview_before_workers = team_preview_before_workers
_post_session_all_completed = post_session_all_completed


def _materialise_turn_token_budget_skips(
    tool: DelegateTool,
    plan: RunPlan,
    results: dict[str, RunState],
) -> None:
    """After turn-ceiling / auth-dead soft-stop: mark un-run tail SKIPPED.

    Not a resume substrate. Un-run nodes become SKIPPED with a budget warning.
    """
    from agentcore.core.logging import get_logger
    from agentcore.llm.turn_auth_dead import (
        REASON_TURN_AUTH_DEAD,
        credential_source_from_llm,
        is_turn_auth_dead,
    )
    from agentcore.runtime.turn.token_budget import (
        REASON_TURN_TOKEN_BUDGET,
        budget_skip_warning_for_active_scope,
        current_turn_tokens,
        resolve_turn_token_ceiling,
    )

    logger = get_logger(__name__)
    payer = credential_source_from_llm(getattr(tool, "_llm", None))
    warning = budget_skip_warning_for_active_scope(credential_source=payer)
    skip_reason = (
        REASON_TURN_AUTH_DEAD if is_turn_auth_dead(payer) else REASON_TURN_TOKEN_BUDGET
    )
    skipped_ids: list[str] = []
    for node in plan.nodes:
        if node.run_id in results:
            continue
        gaps = [
            {
                "description": warning,
                "reason": skip_reason,
            },
        ]
        results[node.run_id] = RunState(
            phase=RunPhase.SKIPPED,
            warnings=[warning],
            delivery_gaps=gaps,
        )
        agent_id = (node.agent_id if node.agent_id else "") or node.run_id
        tool._sink.emit(run_skipped(node.run_id, agent_id, reason=skip_reason))
        skipped_ids.append(node.run_id)
    if skipped_ids:
        fields = {
            "skipped": len(skipped_ids),
            "spent": current_turn_tokens(),
            "ceiling": resolve_turn_token_ceiling(),
            "depth": getattr(tool, "_depth", None),
        }
        if skip_reason == REASON_TURN_TOKEN_BUDGET:
            logger.info("delegate.turn_token_ceiling_skip", **fields)
        else:
            logger.info("delegate.turn_auth_dead_skip", **fields)


async def drive(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    complexity_hint: str = "standard",
    call_idx: int | None = None,
    coordinate: bool = True,
    session: Any = None,
) -> ToolResult:
    """Run ``plan`` through the WaveScheduler and fold workers' products into a CEO ToolResult.

    When ``coordinate`` is true (default) and the gate passes (≥1 worker, root CEO),
    starts a background scheduler and returns immediately. Pass
    ``coordinate=False`` for classic blocking. Pass ``session`` only from the
    background task (:func:`drive_coordinated`).

    ``team_preview_before_workers`` still runs on the CEO path before the coordinate
    fork (silent grant / MLR keep). It no longer durable-pauses for a new card.
    """
    tool._pending_boundary = None
    tool._pending_pause = False
    # 本批次定格的委派序号（见 DelegateTool.execute）：同回合并发委派共享 tool._calls，完成侧
    # 日志必须用调用时定格的值而非活动计数器。resume / checkpoint 重跑时只有单个委派在飞，回退
    # 到活动计数器即可。
    call_idx = call_idx if call_idx is not None else tool._calls

    from agentcore.llm.turn_auth_dead import (
        credential_source_from_llm,
        is_turn_auth_dead,
        turn_auth_dead_reject_message,
    )
    from agentcore.runtime.turn.token_budget import (
        current_turn_tokens,
        is_turn_token_ceiling_hit,
        resolve_turn_token_ceiling,
        turn_token_ceiling_reject_message,
    )

    depth = int(getattr(tool, "_depth", 0) or 0)
    payer = credential_source_from_llm(getattr(tool, "_llm", None))

    # Nested lead: pause the parent's hard-timeout for the whole drive, including
    # turn-ceiling early finalize/reject (those paths still await work on the
    # parent stack). Root CEO (depth=0) is unchanged.
    nested_parent = (
        str(getattr(tool, "_captain_run_id", None) or "") if depth > 0 else ""
    )
    if nested_parent:
        from agentcore.runtime.runs.run_phase_emit import emit_run_phase
        from agentcore.runtime.runs.timeout_hard import mark_waiting_children

        mark_waiting_children(nested_parent, True)
        emit_run_phase(
            getattr(tool, "_sink", None),
            nested_parent,
            str(getattr(tool, "agent_id", "") or nested_parent),
            "waiting_children",
        )
    try:
        # Turn 顶已触：新开批拒绝；resume 续跑则跳过未跑尾并 finalize（不 cancel 已完成）。
        if is_turn_token_ceiling_hit():
            from agentcore.core.logging import get_logger

            get_logger(__name__).info(
                "delegate.turn_token_ceiling_rejected",
                spent=current_turn_tokens(),
                ceiling=resolve_turn_token_ceiling(),
                via="drive",
                has_seed=bool(seed_completed),
                depth=depth,
            )
            if seed_completed:
                results = dict(seed_completed)
                _materialise_turn_token_budget_skips(tool, plan, results)
                return await finalize_drive(
                    tool,
                    plan,
                    results,
                    execution_id=execution_id,
                    seed_completed=seed_completed,
                    session=session,
                    call_idx=call_idx,
                    complexity_hint=complexity_hint,
                    batch_metrics=[],
                )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=turn_token_ceiling_reject_message(),
                contract_failure=True,
            )

        if is_turn_auth_dead(payer):
            from agentcore.core.logging import get_logger

            get_logger(__name__).info(
                "delegate.turn_auth_dead_rejected",
                via="drive",
                has_seed=bool(seed_completed),
                depth=depth,
            )
            if seed_completed:
                results = dict(seed_completed)
                _materialise_turn_token_budget_skips(tool, plan, results)
                return await finalize_drive(
                    tool,
                    plan,
                    results,
                    execution_id=execution_id,
                    seed_completed=seed_completed,
                    session=session,
                    call_idx=call_idx,
                    complexity_hint=complexity_hint,
                    batch_metrics=[],
                )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=turn_auth_dead_reject_message(payer),
                contract_failure=True,
            )

        return await _drive_body(
            tool,
            plan,
            execution_id=execution_id,
            seed_completed=seed_completed,
            complexity_hint=complexity_hint,
            call_idx=call_idx,
            coordinate=coordinate,
            session=session,
        )
    finally:
        if nested_parent:
            from agentcore.runtime.runs.run_phase_emit import emit_run_phase
            from agentcore.runtime.runs.timeout_hard import mark_waiting_children

            mark_waiting_children(nested_parent, False)
            emit_run_phase(
                getattr(tool, "_sink", None),
                nested_parent,
                str(getattr(tool, "agent_id", "") or nested_parent),
                "thinking",
            )


async def _drive_body(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    complexity_hint: str,
    call_idx: int,
    coordinate: bool,
    session: Any,
) -> ToolResult:
    """Inner drive after budget admission."""
    from agentcore.llm.turn_auth_dead import credential_source_from_llm
    from agentcore.runtime.turn.token_budget import (
        resolve_wave_budget_hooks,
        should_materialise_turn_token_budget_skips,
    )

    payer = credential_source_from_llm(getattr(tool, "_llm", None))

    # 团队预审：仍在 coordinate fork 之前（CEO 主路径）跑 silent grant / MLR keep；
    # 不再为新 team_preview 挂起。后台 drive_coordinated 带 session，跳过本闸。
    # 增量委派（合并进活跃协调）同样不再挂开工卡。
    merging_into_active = False
    if session is None and seed_completed is None:
        from agentcore.runtime.coordination.session import active_coordination

        existing_coord = active_coordination(execution_id)
        merging_into_active = (
            existing_coord is not None
            and existing_coord.active
            and tool._depth == 0
        )
        if (
            merging_into_active
            and existing_coord is not None
            and (
                getattr(existing_coord.live_plan, "topology_lock", False)
                or getattr(tool, "_topology_lock", False)
            )
        ):
            from agentcore.core.types import ToolEffect
            from agentcore.runtime.delegate.batch_shape import annotate_batch_meta
            from agentcore.tools.protocol import ToolResult

            return annotate_batch_meta(
                ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=(
                        "当前为工作流拓扑锁：禁止再委派追加队员；"
                        "可用 replan(steers=…) 改未跑步骤说明。"
                    ),
                    effect=ToolEffect.CONTINUE,
                    contract_failure=True,
                ),
                node_count=0,
                has_deps=False,
            )

    # 收口后冷开整团重派硬闸（与同图 replan 补跑闸分轨；共用 MAX_GAP_FILL_ADDS）。
    # 须在 team_preview 之前拒，避免开工卡先弹出。append / 并入活跃图不走本闸。
    # 后台 drive_coordinated 带 session：前台 try_start 已过本闸，跳过（同 team_preview）。
    # session 路径 merging_into_active 恒 False、seed_completed 常为 None，且此时
    # active session 是刚建的空名册，重入会把已准入的 replaces/continue 误拒。
    # 同队续派走 continue_from_run_id，判定在闸内。
    if session is None and not merging_into_active and seed_completed is None:
        from agentcore.core.logging import get_logger
        from agentcore.core.types import ToolEffect
        from agentcore.runtime.delegate.batch_shape import annotate_batch_meta
        from agentcore.runtime.delegate.post_close_gate import (
            POST_CLOSE_REJECT_GAP_FILL,
            post_close_reject,
        )
        from agentcore.tools.protocol import ToolResult

        post_close = post_close_reject(tool, plan)
        if post_close is not None:
            # 事件名必须是字面量：sync_log_event_registry 静态扫参数，条件表达式会丢名。
            _post_close_fields = {
                "execution_id": execution_id,
                "nodes": len(plan.nodes),
                "call": call_idx,
                "kind": post_close.kind,
                "error": post_close.message,
            }
            if post_close.kind == POST_CLOSE_REJECT_GAP_FILL:
                get_logger(__name__).info(
                    "delegate.post_close_gap_fill_rejected",
                    **_post_close_fields,
                )
            else:
                get_logger(__name__).info(
                    "delegate.post_close_redelegation_rejected",
                    **_post_close_fields,
                )
            return annotate_batch_meta(
                ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=post_close.message,
                    effect=ToolEffect.CONTINUE,
                    contract_failure=True,
                ),
                node_count=0,
                has_deps=False,
            )

    # Sticky channel-dead + write-desk 硬拒（与 post_close 同层；并入/续跑未完成写盘节点也拒）。
    # prose / 无写盘需求批次放行；不扫 task 自由文。通道死是能力缺失，不是收口策略。
    from agentcore.core.logging import get_logger
    from agentcore.core.types import ToolEffect
    from agentcore.runtime.delegate.batch_shape import annotate_batch_meta
    from agentcore.runtime.delegate.channel_dead_gate import (
        channel_dead_write_desk_error,
    )
    from agentcore.tools.protocol import ToolResult

    channel_dead_err = channel_dead_write_desk_error(
        tool,
        plan,
        session=session,
        skip_run_ids=set(seed_completed or ()),
    )
    if channel_dead_err is not None:
        get_logger(__name__).info(
            "delegate.channel_dead_write_desk_rejected",
            execution_id=execution_id,
            nodes=len(plan.nodes),
            call=call_idx,
        )
        return annotate_batch_meta(
            ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=channel_dead_err,
                effect=ToolEffect.CONTINUE,
                contract_failure=True,
            ),
            node_count=0,
            has_deps=False,
        )

    if session is None:
        preview_early = await team_preview_before_workers(
            tool,
            plan,
            complexity_hint=complexity_hint,
            seed_completed=seed_completed,
            call_idx=call_idx,
        )
        if preview_early is not None:
            return preview_early

    # 同构再委派护栏：活跃协调上角色+任务高度同构 → 结构化拒绝。
    # 触顶换马甲护栏：近期 thrashing worker + 相似 task/artifacts → 拒冷派
    # （continue_from 不是冷派，闸内跳过）。与 isomorphic 同层；不挪用已退役的
    # completion-gap streak。
    from agentcore.core.logging import get_logger
    from agentcore.core.types import ToolEffect
    from agentcore.runtime.coordination.thrash import (
        find_thrash_collision,
        recent_thrash_records,
        thrash_reject_message,
    )
    from agentcore.runtime.delegate.batch_shape import annotate_batch_meta
    from agentcore.tools.protocol import ToolResult

    cid = str(getattr(tool, "_conversation_id", None) or "")
    thrash_hit = find_thrash_collision(plan, recent_thrash_records(cid))
    if thrash_hit is not None:
        _node, rec = thrash_hit
        get_logger(__name__).info(
            "delegate.thrash_rebrand_rejected",
            execution_id=execution_id,
            thrash_run_id=rec.run_id,
            nodes=len(plan.nodes),
            call=call_idx,
        )
        return annotate_batch_meta(
            ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=thrash_reject_message(rec),
                effect=ToolEffect.CONTINUE,
                contract_failure=True,
            ),
            node_count=0,
            has_deps=False,
        )

    if merging_into_active:
        from agentcore.core.types import ToolEffect
        from agentcore.runtime.coordination.isomorphic import (
            is_isomorphic_redelegation,
            isomorphic_reject_message,
        )
        from agentcore.runtime.coordination.session import active_coordination
        from agentcore.runtime.delegate.batch_shape import annotate_batch_meta
        from agentcore.tools.protocol import ToolResult

        existing = active_coordination(execution_id)
        if existing is not None and is_isomorphic_redelegation(
            plan,
            existing.live_plan,
            completed_run_ids=existing.completed_run_ids,
        ):
            from agentcore.core.logging import get_logger

            get_logger(__name__).info(
                "delegate.isomorphic_rejected",
                execution_id=execution_id,
                nodes=len(plan.nodes),
                completed=len(existing.completed_run_ids),
                total=existing.total_workers,
                call=call_idx,
            )
            msg = isomorphic_reject_message(
                plan,
                completed=len(existing.completed_run_ids),
                total=existing.total_workers,
            )
            return annotate_batch_meta(
                ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=msg,
                    effect=ToolEffect.CONTINUE,
                    # 同构重派是零成本可自纠的契约拒绝——勿进熔断（CEO 连试会误禁用）。
                    contract_failure=True,
                ),
                node_count=0,
                has_deps=False,
            )

    # CEO 协调模式：默认非阻塞臂（depth>0 / 显式 false / checkpoint_after 由 gate 拦下）。
    # 已有活跃协调会话时必须走 try_start（内部 merge），即使本批 coordinate=false。
    if session is None and (coordinate or merging_into_active):
        from agentcore.runtime.coordination.host import try_start_coordination

        started = try_start_coordination(
            tool,
            plan,
            execution_id=execution_id,
            seed_completed=seed_completed,
            complexity_hint=complexity_hint,
            call_idx=call_idx,
            coordinate=coordinate,
        )
        if started is not None:
            return started

    # Nested depth≥1 never enters coordination declare — hand off lead-declared
    # paths to child artifacts before workers write (C3 ownership · parent_run_id).
    from agentcore.runtime.coordination.append_guard import declare_nested_drive_artifacts
    from agentcore.runtime.runs import BatchMetrics, WaveScheduler, resolve_max_parallel

    declare_nested_drive_artifacts(tool, plan, execution_id=execution_id)

    # 跨文件夹 · 多 local 认领簿（C0 允许多根；仅登记，不拒第二本地根）。
    if getattr(tool, "_local_root_claims", None) is None:
        from agentcore.runtime.delegate.target_desktop import LocalRootClaimBook

        tool._local_root_claims = LocalRootClaimBook()
    claims = tool._local_root_claims
    backend = getattr(getattr(tool, "_base_tool_context", None), "backend", None)
    if backend is not None and claims is not None:
        await claims.seed_from_backend(backend)

    worker_gate = resolve_worker_gate(tool)
    executor = build_drive_executor(
        tool,
        plan,
        execution_id=execution_id,
        worker_gate=worker_gate,
        session=session,
    )

    # 跑一半改方向：单人 cancel + 热优先 continue_run / 冷诚实 _redir 接手
    redirects = RedirectController(
        tool=tool,
        plan=plan,
        execution_id=execution_id,
        worker_gate=worker_gate,
        session=session,
        total=len(plan.nodes),
        _coord_seen=set(seed_completed or ()),
    )
    on_boundary = resolve_on_boundary(
        tool, plan, complexity_hint=complexity_hint, session=session
    )
    batch_metrics: list[BatchMetrics] = []

    delegation_started = apply_delegation_grant(
        tool,
        execution_id=execution_id,
        worker_gate=worker_gate,
        seed_completed=seed_completed,
    )

    def _on_skipped(rid: str, aid: str, reason: str) -> None:
        tool._sink.emit(run_skipped(rid, aid, reason=reason))

    should_stop = resolve_wave_budget_hooks(
        credential_source=payer,
    )
    # 嵌套满额：depth≥1 子团不继承父层 12//N 切开份额，按满额并行派发（单 lead 仍受
    # MAX_WORKER_SUBDELEGATIONS=4；depth≤2）。根 depth0 保持分而不乘。
    from agentcore.runtime.runs.concurrency import (
        reseed_nested_delegation_budget,
        reset_budget,
    )

    nested_budget_token = reseed_nested_delegation_budget(
        int(getattr(tool, "_depth", 0) or 0)
    )
    # 从这里到 post-wave drain 之间，本循环会按波排干 stop / redirect 队列——「引擎够得着
    # 这个 run」的窗口正是这一段，登记给 run-stop / run-redirect 路由据实回话。
    reach_token = register_drive(execution_id, plan)
    try:
        results = await WaveScheduler(tool._max_parallel or resolve_max_parallel()).run(
            plan,
            executor,
            seed_completed=seed_completed,
            cancel_run_ids=redirects.cancel_run_ids,
            stop_run_ids=redirects.stop_run_ids,
            timeout_run_ids=redirects.timeout_run_ids,
            on_progress=redirects.on_progress,
            on_boundary=on_boundary,
            on_skipped=_on_skipped,
            metrics_sink=batch_metrics,
            # 触顶后禁新波：在飞 drain，不 cancel。
            should_stop=should_stop,
        )

        # soft should_stop 默认把未跑尾留给 resume；turn 顶是硬停，物化为 SKIPPED。
        if should_materialise_turn_token_budget_skips(credential_source=payer):
            _materialise_turn_token_budget_skips(tool, plan, results)

        results = await redirects.drain_post_wave(
            results,
            executor=executor,
            max_parallel=tool._max_parallel or resolve_max_parallel(),
            on_skipped=_on_skipped,
        )
        redirects.audit_ignored_redirects()
    finally:
        unregister_drive(execution_id, reach_token)
        if nested_budget_token is not None:
            reset_budget(nested_budget_token)
        # Revoke AFTER waves + post-wave drain so late workers still see the grant
        # (工具审批 A+B · B). Keep grant while coordination session is live (merge-rearm).
        if delegation_started and worker_gate is not None:
            from agentcore.runtime.coordination.session import active_coordination

            live = active_coordination(execution_id)
            if live is None or not live.active:
                worker_gate.revoke_delegation(execution_id)

    return await finalize_drive(
        tool,
        plan,
        results,
        execution_id=execution_id,
        seed_completed=seed_completed,
        session=session,
        call_idx=call_idx,
        complexity_hint=complexity_hint,
        batch_metrics=batch_metrics,
    )


async def drive_coordinated(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    complexity_hint: str = "standard",
    call_idx: int | None = None,
    session: Any,
) -> ToolResult:
    """Background entry: same as ``drive`` but with an active coordination session."""
    return await drive(
        tool,
        plan,
        execution_id=execution_id,
        seed_completed=seed_completed,
        complexity_hint=complexity_hint,
        call_idx=call_idx,
        coordinate=False,
        session=session,
    )
