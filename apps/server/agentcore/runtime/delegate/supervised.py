"""受监督的波循环：晚绑定 / scope 偏离 / replan 续跑。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.delegate.boundary import review_summary_text
from agentcore.runtime.runs.builder import _parse_deliverable
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
    yielded for (``BIND`` = late-bind a placeholder, ``SCOPE`` = the reactive arm: re-steer the
    tail after a 队员 deviation OR replan(add) a producer for a worker卡在缺输入·依赖缺口, §2.4
    — gates ``replan``'s required-field check), and the run_ids that triggered the yield (the
    late-bound node for BIND, the deviating / dep-blocked node for SCOPE).
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
            "请 steers 改未跑步骤，或 stop=true 收口，勿用 replaces/continue 重开全队"
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
    binds: list,
    steers: list,
    adds: list | None = None,
) -> list[str]:
    """Validate then apply a replan's binds + steers + adds to the paused plan in place.

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

    locked = bool(getattr(plan, "topology_lock", False)) or bool(
        getattr(tool, "_topology_lock", False)
    )
    if locked and adds:
        return [
            "当前为工作流拓扑锁：禁止新增步骤；可用 steers 改未跑步骤说明，或 stop=true 收口"
        ]

    adds_list = list(adds or [])
    gap_errors = _gap_fill_add_errors(adds_list, completed)
    if gap_errors:
        return gap_errors

    if adds_list:
        from agentcore.runtime.delegate.target_desktop import (
            ensure_bare_chat_auto_cloud_desk,
            gate_bare_chat_requires_target,
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
        bare_gate = gate_bare_chat_requires_target(
            session_folder_id=getattr(tool, "_folder_id", None),
            tasks_raw=adds_list,
            default_target_folder_id=tool.effective_default_target_folder_id(),
        )
        if bare_gate:
            return [bare_gate]

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
        prepare_task_model_fields,
    )

    user_id = ""
    ctx = getattr(tool, "_base_tool_context", None)
    if ctx is not None:
        user_id = getattr(ctx, "user_id", "") or ""

    model_idents: list = []
    if adds_list:
        add_model_errors, add_idents = await prepare_task_model_fields(
            adds_list,
            user_id=user_id,
            where_prefix="add",
            inherit_model=lambda rid: inherit_model_from_tool(tool, rid),
        )
        if add_model_errors:
            return add_model_errors
        model_idents.extend(add_idents)

    if binds:
        bind_model_errors, bind_idents = await prepare_task_model_fields(
            binds,
            user_id=user_id,
            where_prefix="binds",
        )
        if bind_model_errors:
            return bind_model_errors
        model_idents.extend(bind_idents)

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
    ctx = getattr(tool, "_base_tool_context", None)
    if ctx is not None and new_specs:
        from agentcore.workspace.project_shell import rewrite_deliverable_shell

        for spec in new_specs:
            await rewrite_deliverable_shell(getattr(spec, "deliverable", None), ctx)
    bind_ops: list[tuple[RunSpec, dict[str, Any]]] = []
    for i, b in enumerate(binds):
        if not isinstance(b, dict):
            errors.append(f"binds[{i}] 必须是对象")
            continue
        rid = str(b.get("run_id") or "").strip()
        node = plan.by_id(rid) if rid else None
        if node is None:
            errors.append(f"binds[{i}]: run_id `{rid}` 不在当前计划")
            continue
        if not node.bind_after_deps:
            errors.append(f"binds[{i}]: `{rid}` 不是待定稿（晚绑定）步骤")
            continue
        if rid in completed:
            errors.append(f"binds[{i}]: `{rid}` 已完成")
            continue
        role = b.get("role")
        task = b.get("task")
        final_role = role.strip() if isinstance(role, str) and role.strip() else node.role
        final_task = task.strip() if isinstance(task, str) and task.strip() else node.task
        if not final_role:
            errors.append(f"binds[{i}]: `{rid}` 定稿需要 role")
            continue
        if not final_task:
            errors.append(f"binds[{i}]: `{rid}` 定稿需要 task")
            continue
        fields: dict[str, Any] = {"role": final_role, "task": final_task}
        raw_deliverable = b.get("deliverable")
        if isinstance(raw_deliverable, dict):
            parsed = _parse_deliverable({"deliverable": raw_deliverable})
            if parsed is not None:
                fields["deliverable"] = parsed
        # Per-worker 模型：prepare 已把合法三元组编成路由键写入 b["model"]。
        model_raw = b.get("model")
        if isinstance(model_raw, str) and model_raw.strip():
            fields["model"] = model_raw.strip()
        bind_ops.append((node, fields))

    steer_ops: list[tuple[RunSpec, str]] = []
    for i, s in enumerate(steers):
        if not isinstance(s, dict):
            errors.append(f"steers[{i}] 必须是对象")
            continue
        rid = str(s.get("run_id") or "").strip()
        note = str(s.get("note") or "").strip()
        node = plan.by_id(rid) if rid else None
        if node is None:
            errors.append(f"steers[{i}]: run_id `{rid}` 不在当前计划")
            continue
        if rid in completed:
            errors.append(f"steers[{i}]: `{rid}` 已完成，无法操舵")
            continue
        if not note:
            errors.append(f"steers[{i}]: 缺少 note")
            continue
        steer_ops.append((node, note))

    if ctx is not None:
        from agentcore.workspace.project_shell import rewrite_deliverable_shell

        for _node, fields in bind_ops:
            deliverable = fields.get("deliverable")
            if deliverable is not None:
                await rewrite_deliverable_shell(deliverable, ctx)

    # Active coordination: replan.adds share append's seat/artifact admit before mutate.
    if new_specs and not errors:
        seat_reject = _admit_replan_adds_against_coordination(tool, plan, new_specs)
        if seat_reject is not None:
            errors.append(seat_reject)

    if errors:
        return errors
    for node, fields in bind_ops:
        for key, value in fields.items():
            setattr(node, key, value)
        node.bind_after_deps = False
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
    from agentcore.runtime.delegate.force_scopes import GATE_SEAT_OVERLAP, force_allows
    from agentcore.runtime.runs.plan import RunPlan as Plan

    session = _replan_coordination_session(tool, plan)
    if session is None:
        return None
    # replan 在入口重解析自己的 force（见 DelegateTool.replan）——不继承上一次 delegate。
    force = force_allows(tool, GATE_SEAT_OVERLAP)
    ownership = session.ensure_file_ownership()
    staging = Plan(nodes=list(new_specs))
    reject = admit_added_nodes(
        staging,
        plan,
        completed_run_ids=session.completed_run_ids,
        vacated_run_ids=session.vacated_run_ids,
        ownership=ownership,
        force=force,
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
    from agentcore.runtime.delegate.force_scopes import GATE_SEAT_OVERLAP, force_allows
    from agentcore.runtime.runs.executor.context import _ancestors_by_id

    session = _replan_coordination_session(tool, plan)
    if session is None:
        return
    if session.live_plan is None:
        session.live_plan = plan
    session.total_workers = len(plan.nodes)
    if not new_specs:
        return
    force = force_allows(tool, GATE_SEAT_OVERLAP)
    declare_plan_artifacts(
        plan,
        session.ensure_file_ownership(),
        force=force,
        only_run_ids={n.run_id for n in new_specs},
        ancestor_map=_ancestors_by_id(plan),
        completed_run_ids=session.completed_run_ids,
        birth_desk_id=getattr(tool, "_folder_id", None),
    )

async def finalize_stopped(
    tool: DelegateTool,
    plan: RunPlan,
    seed_completed: dict[str, RunState],
    *,
    kickoff_cancelled: bool = False,
    kickoff_timeout: bool = False,
    kickoff_adjusted: bool = False,
    note: str = "",
) -> ToolResult:
    """Wrap up a partial plan without running the tail.

    ``kickoff_cancelled`` marks team_preview STOP (drive_preview / resume_plan with
    ``apply_kickoff_grant``). ``kickoff_timeout`` marks team_preview TIMEOUT on
    the same grant path — no grant, no drive; copy aligns with ask timeout.
    ``kickoff_adjusted`` marks team_preview ADJUST — same no-grant path, but
    revise-and-resubmit guidance (not cancel「宜先问」). Those paths replace
    ``format_for_ceo`` with soft guidance — plan_review / replan stop keep the
    normal CEO brief.
    """
    from agentcore.runtime.delegate.accumulate import (
        accumulate_usage,
        collect_citations,
        collect_ledger,
        register_sessions,
    )
    from agentcore.runtime.delegate.ceo_format import format_for_ceo
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
    # 未执行的尾巴照实标缺口。开工卡上直接停止（seed 为空、一步没跑）不发：用户主动叫停
    # 于开工前，无「交付对账」可言。生产 base context 恒带本回合 execution_id（与 drive
    # 同值），空值只出现在裸测试装配——同样跳过。
    if seed_completed and getattr(tool._base_tool_context, "execution_id", ""):
        from agentcore.runtime.delegate.delivery_status import maybe_emit_delivery_status

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
    if kickoff_timeout:
        from agentcore.runtime.kickoff.cancel_guidance import format_kickoff_timeout_result

        output = format_kickoff_timeout_result(primitive="delegate", note=note)
    elif kickoff_adjusted:
        from agentcore.runtime.kickoff.adjust_guidance import format_kickoff_adjust_result

        output = format_kickoff_adjust_result(primitive="delegate", note=note)
    elif kickoff_cancelled:
        from agentcore.runtime.kickoff.cancel_guidance import format_kickoff_cancel_result

        output = format_kickoff_cancel_result(primitive="delegate", note=note)
    else:
        output = format_for_ceo(tool, plan, results)
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
    from agentcore.runtime.runs import BoundaryReason

    if reason is BoundaryReason.SCOPE:
        return format_scope_boundary(plan, results, nodes)
    if reason is BoundaryReason.CHECKPOINT:
        return format_checkpoint_boundary(plan, results, nodes)
    return format_bind_boundary(plan, results, nodes)


def format_checkpoint_boundary(plan: RunPlan, results: dict, nodes: list[RunSpec]) -> str:
    """CHECKPOINT-arm brief (协调态波边界：事件而非回合暂停)."""
    from agentcore.runtime.runs import RunPhase

    lines = [
        "## 计划已让出（checkpoint_after 波边界）",
        "下列步骤已完成并声明了检查点。协调模式下**不挂起回合**——"
        "若需用户拍板请用 `ask_user`；若可继续请 `replan` 放行下游。",
    ]
    for node in nodes:
        state = results.get(node.run_id)
        summary = review_summary_text(state)
        lines.append(
            f"\n### 已完成 · run_id: `{node.run_id}`"
            f"（{node.role or node.run_id}）\n"
            f"产出摘要：{summary or '（无产出）'}"
        )
    pending = [n.run_id for n in plan.nodes if n.run_id not in results]
    done = sum(1 for s in results.values() if s and s.phase is RunPhase.COMPLETED)
    lines.append(
        "\n---\n"
        f"当前已完成 {done} 步；待跑：{('、'.join(f'`{p}`' for p in pending)) or '（无）'}。"
    )
    return "\n".join(lines)


def format_bind_boundary(plan: RunPlan, results: dict, nodes: list[RunSpec]) -> str:
    """BIND-arm brief (晚绑定)."""
    from agentcore.runtime.runs import RunPhase

    lines = [
        "## 计划已让出（请定稿待绑定步骤后续跑）",
        "下列步骤声明了「依赖完成后再定稿」(bind_after_deps)：其上游已就位，现在由你"
        "依据上游产出把它们的职责 / 任务定稿，然后用 `replan` 续跑同一计划。",
    ]
    for node in nodes:
        dep_lines: list[str] = []
        for dep_id in node.depends_on:
            state = results.get(dep_id)
            summary = review_summary_text(state)
            dep = plan.by_id(dep_id)
            dep_role = (dep.role if dep else dep_id) or dep_id
            dep_lines.append(f"  - 上游 `{dep_id}`（{dep_role}）：{summary or '（无产出）'}")
        lines.append(
            f"\n### 待定稿 · run_id: `{node.run_id}`"
            f"（占位角色：{node.role or '未填'}）\n"
            f"占位任务：{node.task or '（未填）'}\n"
            "依赖产出：\n" + ("\n".join(dep_lines) or "  - （无上游）")
        )
    pending = [n.run_id for n in plan.nodes if n.run_id not in results]
    done = sum(1 for s in results.values() if s and s.phase is RunPhase.COMPLETED)
    lines.append(
        "\n---\n请调用 `replan` 定稿上述步骤："
        "`binds=[{run_id, role, task, …}]`（定稿后该步即可运行）；可选 "
        "`steers=[{run_id, note}]` 操舵其它未跑步骤；确无需继续则 `replan(stop=true)`。\n"
        "定稿前先对一下上游这几块的【拼图边】（语义边界对账）：彼此对同一共享点"
        "（接口 / 字段 / 数据格式）的假设是否一致、有没有缺口或重复——据此把待定稿步骤定准；"
        "若某已完成步骤与上游对不上，用 `delegate` 设 `continue_from_run_id` "
        "带现场续派对齐，别让下游接着错下去。\n"
        f"当前已完成 {done} 步；待跑：{('、'.join(f'`{p}`' for p in pending)) or '（无）'}。"
    )
    return "\n".join(lines)


def format_scope_boundary(plan: RunPlan, results: dict, nodes: list[RunSpec]) -> str:
    """Reactive-arm brief — 职责偏离 (kind=scope) AND/OR 依赖缺口·卡在缺输入 (kind=dep, §2.4).

    Both kinds ride the SAME reactive boundary (``BoundaryReason.SCOPE``); this brief tells the
    captain which is which so it picks the right ``replan`` lever — ``steers`` to re-aim an
    un-run step for a scope deviation, ``add`` to append a producer / wire a dependency edge for
    a worker卡在缺输入."""
    from agentcore.runtime.runs import RunPhase

    # Does any surfaced node carry a dep (依赖缺口) signal? Tailor the header / closing guidance
    # so a pure-scope yield reads exactly as before, while a dep yield steers toward replan(add).
    has_dep = any(
        e.get("kind") == "dep"
        for n in nodes
        for e in (results.get(n.run_id).escalations if results.get(n.run_id) else [])
    )
    headline = (
        "队员报告职责偏离 / 卡在缺输入" if has_dep else "队员报告职责偏离"
    )
    lines = [
        f"## 计划已让出（{headline}，请校准未跑步骤）",
        "下列【已完成】步骤报告了「职责/范围偏离」(escalate kind=scope) 或「卡在缺输入·依赖缺口」"
        "(escalate kind=dep)：前者发现真正要做的与初始计划不符，后者缺一个还不存在的输入 / 依赖"
        "（没人产出过、计划也没安排）才能做好。请阅读它们的产出与信号说明，再用 `replan` 续跑同一"
        "计划——偏离用 `steers` 操舵未跑步骤，缺输入用 `add` 追加一个产出它的步骤 / 接一条依赖边。",
    ]
    for node in nodes:
        state = results.get(node.run_id)
        summary = review_summary_text(state)
        esc_lines: list[str] = []
        for e in state.escalations if state else []:
            kind = e.get("kind")
            if kind not in ("scope", "dep"):
                continue
            question = str(e.get("question") or "").strip()
            assumption = str(e.get("assumption") or "").strip()
            tag = "缺输入" if kind == "dep" else "偏离"
            esc_lines.append(f"  - {tag}：{question or '（未写明）'}")
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
        "\n---\n请调用 `replan` 校准未跑步骤：`steers=[{run_id, note}]` 操舵尚未运行的下游"
        "（运行前注入指令）；有队员【卡在缺输入】时用 `add=[{role, task, depends_on}]` 追加一个"
        "产出它的步骤 / 接一条依赖边；若某步是『待定稿』可一并 `binds=[…]` 定稿；确认无需改动可"
        "直接 `replan()` 续跑；确无需继续则 `replan(stop=true)`。\n"
        "校准前主动对一遍【拼图边】（语义边界对账）：这次信号很可能波及兄弟步骤——别只盯举手这块，"
        "查其它已完成步骤与它在共享点（接口 / 字段 / 数据格式）上是否还对得上，有冲突 / 缺口 / 重复"
        "就一并用 `steers` 操舵未跑步骤、或用 `delegate`（`continue_from_run_id`）"
        "带现场续派已跑步骤对齐。\n"
        f"当前已完成 {done} 步；待跑：{('、'.join(f'`{p}`' for p in pending)) or '（无）'}。"
    )
    return "\n".join(lines)
