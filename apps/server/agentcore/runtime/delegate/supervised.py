"""受监督的波循环：scope 偏离 / replan 续跑。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.delegate.boundary import review_summary_text
from agentcore.runtime.runs.constants import DELEGATE_OUTPUT_LIMIT
from agentcore.tools.protocol import ToolResult

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.scheduler import BoundaryReason
    from agentcore.runtime.runs.types import RunSpec, RunState

DelegateTool = Any

logger = get_logger(__name__)


@dataclass
class SupervisedRun:
    """A delegate plan paused at a decision boundary, awaiting the CEO's ``replan`` (受监督
    的波循环).     Holds exactly what :meth:`DelegateTool.replan` needs to finalise / re-steer
    and resume the SAME DAG from where it yielded: the (mutable) plan, the completed-so-far
    seeds, the turn's execution id, the ``reason`` it
    yielded for (``SCOPE`` = the reactive arm: re-steer the
    tail after a 队员 deviation OR replan(add) a producer for a worker卡在缺输入·依赖缺口, §2.4),
    and the run_ids that triggered the yield (the
    deviating / dep-blocked node for SCOPE).
    """

    plan: RunPlan
    completed: dict[str, RunState]
    execution_id: str
    reason: BoundaryReason
    boundary_run_ids: list[str]


def _gap_fill_add_errors(
    adds: list,
    completed: dict[str, RunState],
) -> list[str]:
    """Hard-gate 补跑 adds that carry replaces / continue_from-of-gap semantics.

    - 无缺口却带 replaces / 对缺口 continue → 拒（禁止无缺口整团重开）
    - 条数 > min(缺口数, MAX_GAP_FILL_ADDS) → 拒（按缺口限流）
    - 无 replaces、且 continue 指向已成功节点的普通续派 / 首派补生产者 → 不走本闸
    """
    from agentcore.runtime.runs.constants import MAX_GAP_FILL_ADDS
    from agentcore.runtime.runs.types import RunPhase

    gap_ids = {
        rid
        for rid, st in completed.items()
        if st is not None and st.phase in (RunPhase.FAILED, RunPhase.SKIPPED)
    }

    gap_fill: list[tuple[int, dict[str, Any], str]] = []
    for i, item in enumerate(adds):
        if not isinstance(item, dict):
            continue
        replaces = str(item.get("replaces_run_id") or "").strip()
        continue_from = str(item.get("continue_from_run_id") or "").strip()
        if replaces:
            gap_fill.append((i, item, replaces))
        elif continue_from and continue_from in gap_ids:
            # 对失败/跳过节点的 continue = 补跑；对已成功节点的 continue = 正常续派，不闸。
            gap_fill.append((i, item, continue_from))
    if not gap_fill:
        return []

    if not gap_ids:
        return [
            "补跑拒绝：当前无失败/跳过缺口，禁止无缺口整团重开；"
            "请用 tell 给还没开始的人补一句，或 stop=true 收口，勿用 replaces/continue 重开全队"
        ]

    max_allowed = min(len(gap_ids), MAX_GAP_FILL_ADDS)
    if len(gap_fill) > max_allowed:
        return [
            f"补跑一次最多追加 {max_allowed} 个缺口点名节点"
            f"（缺口 {len(gap_ids)}，上限 {MAX_GAP_FILL_ADDS}，收到 {len(gap_fill)}）；"
            "请只点名最关键失败/跳过节点，分批 replan，勿整团重开"
        ]

    errors: list[str] = []
    for i, _item, target in gap_fill:
        if target not in gap_ids:
            errors.append(
                f"add[{i}]: replaces/continue_from `{target}` 不是当前失败/跳过缺口；"
                f"请点名缺口 run_id（{', '.join(sorted(gap_ids)[:8])}）"
            )
    return errors


async def apply_replan(
    tool: DelegateTool,
    plan: RunPlan,
    completed: dict[str, RunState],
    steers: list,
    adds: list | None = None,
) -> list[str]:
    """Validate then apply a replan's steers + adds to the paused plan in place.

    All-or-nothing: every op is validated first and a non-empty error list returns
    BEFORE any mutation, so a rejected replan leaves the paused plan untouched. ``adds``
    appends brand-new nodes (波边界追加节点, 设计 §7.1) — id 生成 / 依赖接线 / 拓扑校验 live
    in :func:`build_added_nodes`; here we just append the vetted specs and, because the
    graph grew, flip the plan origin to CAPTAIN and recompute fan-out awareness so any
    newly-parallel nodes see each other.

    When an active coordination session owns ``plan`` as its live graph, ``adds`` go
    through the same seat/artifact admit + ``declare_plan_artifacts`` path as append
    merge (auto-``replaces`` → ``transfer_all_from``).
    """
    from agentcore.runtime.runs import RunOrigin, build_added_nodes
    from agentcore.runtime.runs.builder import _apply_sibling_summaries

    adds_list = list(adds or [])
    gap_errors = _gap_fill_add_errors(adds_list, completed)
    if gap_errors:
        return gap_errors

    if adds_list:
        from agentcore.runtime.delegate.target_desktop import (
            bare_chat_local_scratch_write_ok,
            ensure_bare_chat_auto_cloud_desk,
            gate_bare_chat_requires_target,
            gate_conversation_id_is_not_folder,
        )

        ctx = getattr(tool, "_base_tool_context", None)
        await ensure_bare_chat_auto_cloud_desk(
            session_folder_id=getattr(tool, "_folder_id", None),
            tasks_raw=adds_list,
            default_target_folder_id=tool.effective_default_target_folder_id(),
            turn_target_desk=getattr(ctx, "turn_target_desk", None) if ctx else None,
            user_id=(getattr(ctx, "user_id", "") or "") if ctx else "",
            conversation_id=getattr(tool, "_conversation_id", None)
            or (getattr(ctx, "conversation_id", None) if ctx else None),
            tool_context=ctx,
            sink=getattr(tool, "_sink", None),
        )
        allow_scratch = bare_chat_local_scratch_write_ok(
            session_folder_id=getattr(tool, "_folder_id", None),
            backend=getattr(ctx, "backend", None) if ctx else None,
            turn_target_desk=getattr(ctx, "turn_target_desk", None) if ctx else None,
        )
        bare_gate = gate_bare_chat_requires_target(
            session_folder_id=getattr(tool, "_folder_id", None),
            tasks_raw=adds_list,
            default_target_folder_id=tool.effective_default_target_folder_id(),
            allow_local_scratch_write=allow_scratch,
        )
        if bare_gate:
            return [bare_gate]
        conv_gate = gate_conversation_id_is_not_folder(
            conversation_id=getattr(tool, "_conversation_id", None)
            or (getattr(ctx, "conversation_id", None) if ctx else None),
            tasks_raw=adds_list,
            default_target_folder_id=tool.effective_default_target_folder_id(),
        )
        if conv_gate:
            logger.info(
                "delegate.conversation_id_as_folder_rejected",
                conversation_id=getattr(tool, "_conversation_id", None),
            )
            return [conv_gate]

        # Bypass drive cold-open: replan adds resume via seed_completed and would
        # skip post_close-style gates; reject write-desk adds when channel is dead.
        from agentcore.runtime.delegate.channel_dead_gate import (
            channel_dead_write_tasks_error,
        )

        channel_dead_err = channel_dead_write_tasks_error(tool, adds_list)
        if channel_dead_err:
            return [channel_dead_err]

    from agentcore.runtime.delegate.task_models import (
        ensure_delegate_route_extras,
        inherit_model_from_tool,
        load_catalog_for_items,
        prepare_task_model_fields,
    )

    user_id = ""
    ctx = getattr(tool, "_base_tool_context", None)
    if ctx is not None:
        user_id = getattr(ctx, "user_id", "") or ""

    model_idents: list = []
    if adds_list:
        catalog, cat_err = await load_catalog_for_items(adds_list, user_id=user_id)
        if cat_err:
            return [cat_err]
        add_model_errors, add_idents = await prepare_task_model_fields(
            adds_list,
            user_id=user_id,
            where_prefix="add",
            catalog=catalog,
            inherit_model=lambda rid: inherit_model_from_tool(tool, rid),
        )
        if add_model_errors:
            return add_model_errors
        model_idents.extend(add_idents)

    if model_idents:
        await ensure_delegate_route_extras(
            tool._llm,
            model_idents,
            user_id=user_id or None,
        )

    valid_tools = {s.name for s in tool._tools.list_all()}
    errors: list[str] = []
    new_specs, add_errors = build_added_nodes(
        adds_list,
        plan,
        valid_tools=valid_tools,
        parent_run_id=tool._captain_run_id,
        depth=tool._depth + 1,
        default_target_folder_id=tool.effective_default_target_folder_id(),
    )
    errors.extend(add_errors)
    steer_ops: list[tuple[RunSpec, str]] = []
    for i, s in enumerate(steers):
        if not isinstance(s, dict):
            errors.append(f"tell[{i}] 必须是对象")
            continue
        rid = str(s.get("run_id") or "").strip()
        note = str(s.get("note") or "").strip()
        node = plan.by_id(rid) if rid else None
        if node is None:
            errors.append(f"tell[{i}]: run_id `{rid}` 不在当前计划")
            continue
        if rid in completed:
            errors.append(f"tell[{i}]: `{rid}` 已完成，无法补话")
            continue
        if not note:
            errors.append(f"tell[{i}]: 缺少 note")
            continue
        steer_ops.append((node, note))

    # Active coordination: replan.adds share append's seat/artifact admit before mutate.
    if new_specs and not errors:
        seat_reject = _admit_replan_adds_against_coordination(tool, plan, new_specs)
        if seat_reject is not None:
            errors.append(seat_reject)

    if errors:
        return errors
    for node, note in steer_ops:
        node.steer = f"{node.steer}\n- {note}" if node.steer else f"- {note}"
    if new_specs:
        for spec in new_specs:
            plan.add(spec)
        plan.origin = RunOrigin.CAPTAIN
        _apply_sibling_summaries(plan)
        # replaces_run_id rewrites may unblock cascade-skipped downstream — drop
        # their SKIPPED seeds so the resumed wave waits on the replacement.
        from agentcore.runtime.runs.plan import clear_revivable_skips

        clear_revivable_skips(plan, completed)
        _declare_replan_adds_on_coordination(tool, plan, new_specs)
    return []


def _replan_coordination_session(
    tool: DelegateTool, plan: RunPlan
) -> Any | None:
    """Return the active session only when ``plan`` is its live coordination graph."""
    from agentcore.runtime.coordination.session import active_coordination

    sup = getattr(tool, "_supervised", None)
    eid = ""
    if sup is not None:
        eid = str(getattr(sup, "execution_id", "") or "").strip()
    if not eid:
        ctx = getattr(tool, "_base_tool_context", None)
        eid = str(getattr(ctx, "execution_id", "") or "").strip()
    if not eid:
        return None
    session = active_coordination(eid)
    if session is None or not session.active:
        return None
    # Nested lead sub-plans must not be gated against the root live graph.
    if session.live_plan is not None and session.live_plan is not plan:
        return None
    return session


def _admit_replan_adds_against_coordination(
    tool: DelegateTool,
    plan: RunPlan,
    new_specs: list[RunSpec],
) -> str | None:
    """Seat/artifact admit for replan.adds; ``None`` when no session or admitted."""
    from agentcore.core.logging import get_logger
    from agentcore.runtime.coordination.append_guard import admit_added_nodes
    from agentcore.runtime.runs.plan import RunPlan as Plan

    session = _replan_coordination_session(tool, plan)
    if session is None:
        return None
    ownership = session.ensure_file_ownership()
    staging = Plan(nodes=list(new_specs))
    reject = admit_added_nodes(
        staging,
        plan,
        completed_run_ids=session.completed_run_ids,
        vacated_run_ids=session.vacated_run_ids,
        ownership=ownership,
        total_workers=session.total_workers,
        birth_desk_id=getattr(tool, "_folder_id", None),
    )
    if reject is not None:
        get_logger(__name__).info(
            "coordination.append_overlap_rejected",
            execution_id=session.execution_id,
            overlaps=1,
            completed=len(session.completed_run_ids),
            total=session.total_workers,
            via="replan",
        )
    return reject


def _declare_replan_adds_on_coordination(
    tool: DelegateTool,
    plan: RunPlan,
    new_specs: list[RunSpec],
) -> None:
    """Dispatch ownership for admitted replan.adds (replaces → transfer_all_from)."""
    from agentcore.runtime.coordination.append_guard import declare_plan_artifacts
    from agentcore.runtime.runs.executor.context import _ancestors_by_id

    session = _replan_coordination_session(tool, plan)
    if session is None:
        return
    if session.live_plan is None:
        session.live_plan = plan
    session.total_workers = len(plan.nodes)
    if not new_specs:
        return
    declare_plan_artifacts(
        plan,
        session.ensure_file_ownership(),
        only_run_ids={n.run_id for n in new_specs},
        ancestor_map=_ancestors_by_id(plan),
        completed_run_ids=session.completed_run_ids,
        birth_desk_id=getattr(tool, "_folder_id", None),
    )

async def finalize_stopped(
    tool: DelegateTool,
    plan: RunPlan,
    seed_completed: dict[str, RunState],
) -> ToolResult:
    """Wrap up a partial plan without running the tail (plan_review / replan stop)."""
    from agentcore.runtime.delegate.accumulate import (
        accumulate_usage,
        collect_citations,
        collect_ledger,
        register_sessions,
    )
    from agentcore.runtime.delegate.ceo_format import build_ceo_synthesis
    from agentcore.runtime.delegate.nesting import absorb_children
    from agentcore.runtime.events import run_skipped
    from agentcore.runtime.runs import RunPhase, RunState

    results: dict[str, RunState] = dict(seed_completed)
    for node in plan.nodes:
        if node.run_id in results:
            continue
        results[node.run_id] = RunState(phase=RunPhase.SKIPPED)
        # Graceful stop (replan stop / dispose): un-run tail → run_skipped(abort).
        agent_id = node.agent_id or node.run_id
        tool._sink.emit(run_skipped(node.run_id, agent_id, reason="abort"))
    # 交付状态（诚实对账）：主动收口（replan stop / dispose）也是收尾——已落盘的照实列、
    # 未执行的尾巴照实标缺口。开跑前直接停止（seed 为空、一步没跑）不发：用户主动叫停
    # 于开跑前，无「交付对账」可言。生产 base context 恒带本回合 execution_id（与 drive
    # 同值），空值只出现在裸测试装配——同样跳过。
    if seed_completed and getattr(tool._base_tool_context, "execution_id", ""):
        from agentcore.runtime.delegate.delivery_status import maybe_emit_delivery_status
        from agentcore.runtime.runs.disk_truth import stamp_results_disk_truth

        await stamp_results_disk_truth(
            results, tool._base_tool_context.backend
        )
        maybe_emit_delivery_status(
            tool._sink,
            plan,
            results,
            execution_id=tool._base_tool_context.execution_id,
            backend=tool._base_tool_context.backend,
            promotion_ledger=tool._base_tool_context.promotion_ledger,
        )
    accumulate_usage(tool, results)
    collect_ledger(tool, plan, results)
    collect_citations(tool, results)
    registered = register_sessions(tool, plan, results)
    if tool._session_saver is not None:
        for session in registered:
            await tool._session_saver(session)
    absorb_children(tool)
    output = build_ceo_synthesis(tool, plan, results).text
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        output_limit=DELEGATE_OUTPUT_LIMIT,
    )


def format_boundary_for_ceo(
    tool: DelegateTool,
    reason: BoundaryReason,
    plan: RunPlan,
    results: dict,
    nodes: list[RunSpec],
) -> str:
    """The CEO-facing「计划已让出」brief when a supervised plan YIELDs."""
    _ = reason
    return format_scope_boundary(plan, results, nodes)


def format_scope_boundary(plan: RunPlan, results: dict, nodes: list[RunSpec]) -> str:
    """Reactive-arm brief — a finished worker asked the CEO to look at the rest (adjust)."""
    from agentcore.runtime.runs import RunPhase

    lines = [
        "## 计划已让出（队员做完自己这份，请看后面的安排）",
        "下列【已完成】步骤用 escalate reason=adjust 请你看后面。"
        "请阅读产出和问题，再用 `replan` 续跑同一计划："
        "`tell` 给还没开始的人补一句，`add` 再加一个人。",
    ]
    for node in nodes:
        state = results.get(node.run_id)
        summary = review_summary_text(state)
        esc_lines: list[str] = []
        for e in state.escalations if state else []:
            if e.get("reason") != "adjust":
                continue
            question = str(e.get("question") or "").strip()
            assumption = str(e.get("assumption") or "").strip()
            esc_lines.append(f"  - {question or '（未写明）'}")
            if assumption:
                esc_lines.append(f"    暂定假设：{assumption}")
        lines.append(
            f"\n### 队员信号 · run_id: `{node.run_id}`（{node.role or node.run_id}）\n"
            f"产出：{summary or '（无产出）'}\n"
            "信号说明：\n" + ("\n".join(esc_lines) or "  - （未写明）")
        )
    pending = [n.run_id for n in plan.nodes if n.run_id not in results]
    done = sum(1 for s in results.values() if s and s.phase is RunPhase.COMPLETED)
    lines.append(
        "\n---\n请调用 `replan`：`tell=[{run_id, note}]` 给还没开始的人补一句；"
        "要再加一个人用 `add=[{role, task, depends_on}]`；确认无需改动可"
        "直接 `replan()` 续跑；确无需继续则 `replan(stop=true)`。\n"
        "校准前主动对一遍【拼图边】（语义边界对账）：这次信号很可能波及兄弟步骤——别只盯举手这块，"
        "查其它已完成步骤与它在共享点（接口 / 字段 / 数据格式）上是否还对得上，有冲突 / 缺口 / 重复"
        "就一并用 `tell` 给还没开始的人补一句、或用 `delegate`（`continue_from_run_id`）"
        "带现场续派已跑步骤对齐。\n"
        f"当前已完成 {done} 步；待跑：{('、'.join(f'`{p}`' for p in pending)) or '（无）'}。"
    )
    return "\n".join(lines)
