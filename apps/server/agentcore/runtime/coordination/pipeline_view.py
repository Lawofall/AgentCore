"""Pipeline progress view for CEO coordination injections.

Surfaces wave progress + running / dependency-blocked node overview so idle
wakes do not read as「闲着了该干点什么」when the DAG is advancing normally.

Progress blocks are **incremental**: completed workers are not re-listed by name
every inject (that roster grows and poisons CEO context). New completions are
named once; critical decision signals (running / blocked / failed / pending)
stay named every time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcore.runtime.coordination.session import CoordinationSession
    from agentcore.runtime.runs.plan import RunPlan


def _node_label(node: Any) -> str:
    return str(
        getattr(node, "role", None)
        or getattr(node, "agent_name", None)
        or getattr(node, "run_id", "")
        or "?"
    )


def _classify_nodes(
    session: CoordinationSession,
) -> tuple[list[Any], list[Any], list[Any], list[Any], list[Any]]:
    """Split live nodes into completed / failed / running / dep_blocked / pending.

    ``pending`` = deps satisfied but not yet running (between-wave dispatch lag).
    Failed ids come from ``session.failed_run_ids`` when present.
    """
    live: RunPlan | None = getattr(session, "live_plan", None)
    if live is None or not live.nodes:
        return [], [], [], [], []

    done = set(session.completed_run_ids)
    failed = set(getattr(session, "failed_run_ids", None) or ())
    running_ids = {rid for rid, _ in session.running_workers()}

    completed: list[Any] = []
    failed_nodes: list[Any] = []
    running: list[Any] = []
    dep_blocked: list[Any] = []
    pending: list[Any] = []

    for node in live.nodes:
        rid = node.run_id
        if rid in failed:
            failed_nodes.append(node)
            continue
        if rid in done:
            completed.append(node)
            continue
        if rid in running_ids:
            running.append(node)
            continue
        deps = list(getattr(node, "depends_on", None) or [])
        # Dep may name short id or full run_id — treat satisfied if every dep is
        # in completed_run_ids OR any completed id endswith _{dep} / equals dep.
        if deps and not all(_dep_satisfied(dep, done) for dep in deps):
            dep_blocked.append(node)
        else:
            pending.append(node)
    return completed, failed_nodes, running, dep_blocked, pending


def _dep_satisfied(dep: str, done: set[str]) -> bool:
    if dep in done:
        return True
    suffix = f"_{dep}"
    return any(rid == dep or rid.endswith(suffix) for rid in done)


def is_pipeline_healthy(session: CoordinationSession) -> bool:
    """True when work is advancing: someone running, rest only waiting on deps, no fails.

    Between-wave ``pending`` (deps met, not yet armed) is still treated as healthy
    as long as at least one node is running and nothing has failed.
    """
    _completed, failed, running, dep_blocked, pending = _classify_nodes(session)
    if failed:
        return False
    if not running and not session.has_inflight_work():
        return False
    # Incomplete nodes must be running, dep-blocked, or briefly pending dispatch.
    # (No "mystery" incomplete class beyond these.)
    live = getattr(session, "live_plan", None)
    if live is None:
        # No plan pointer — fall back to busy workers only.
        return session.has_inflight_work() or bool(session.running_workers())
    incomplete = [
        n for n in live.nodes if n.run_id not in session.completed_run_ids
    ]
    accounted = {n.run_id for n in (*running, *dep_blocked, *pending)}
    if any(n.run_id not in accounted for n in incomplete):
        return False
    # Must have active progress (running or in-flight LLM/tool).
    if not running and not session.has_inflight_work():
        return False
    # If everything incomplete is pending with nobody running — not healthy.
    return not (not running and pending and not dep_blocked)


def format_pipeline_progress(
    session: CoordinationSession,
    *,
    newly_completed: set[str] | None = None,
    consume_delta: bool = True,
) -> str:
    """Human-readable wave / node progress block for CEO injection.

    Incremental by default: completed workers are counted, not re-named every
    inject. Pass ``newly_completed`` to name a delta once; when omitted and
    ``consume_delta`` is True, takes the session's unreported completion cursor.
    """
    live: RunPlan | None = getattr(session, "live_plan", None)
    done_n = len(session.completed_run_ids)
    total = session.total_workers or (len(live.nodes) if live else 0)
    head = f"【流水线进度】已完成 {done_n}/{total}"

    if newly_completed is None and consume_delta:
        newly = session.take_progress_delta()
    else:
        newly = set(newly_completed or ())
        if consume_delta and newly:
            session.progress_reported_completed |= newly

    lines: list[str] = [head]
    if live is None or not live.nodes:
        summary = session.worker_progress_summary()
        if newly:
            labels = _labels_for_ids(session, newly)
            if labels:
                lines.append(f"  本轮新完成：{'、'.join(labels)}")
        lines.append(summary)
        return "\n".join(lines)

    _completed, failed, running, dep_blocked, pending = _classify_nodes(session)

    if newly:
        labels = _labels_for_ids(session, newly)
        if labels:
            lines.append(f"  本轮新完成：{'、'.join(labels)}")

    try:
        waves = live.waves()
    except Exception:  # noqa: BLE001 — never break inject on bad topology
        waves = [list(live.nodes)]

    done = set(session.completed_run_ids)
    failed_ids = set(getattr(session, "failed_run_ids", None) or ())
    running_ids = {rid for rid, _ in session.running_workers()}

    for i, wave in enumerate(waves):
        bits: list[str] = []
        done_in_wave = 0
        for n in wave:
            label = _node_label(n)
            if n.run_id in failed_ids:
                bits.append(f"{label}=失败")
            elif n.run_id in newly:
                bits.append(f"{label}=新完成")
            elif n.run_id in done:
                done_in_wave += 1
            elif n.run_id in running_ids:
                bits.append(f"{label}=在跑")
            elif n in dep_blocked:
                bits.append(f"{label}=依赖阻塞")
            elif n in pending:
                bits.append(f"{label}=待调度")
            else:
                bits.append(f"{label}=未启动")
        if done_in_wave:
            bits.insert(0, f"已完成×{done_in_wave}")
        lines.append(f"  Wave {i}：{'；'.join(bits) if bits else '（空）'}")

    if running:
        names = "、".join(_node_label(n) for n in running)
        lines.append(f"  在跑：{names}")
    if dep_blocked:
        names = "、".join(_node_label(n) for n in dep_blocked[:8])
        extra = f" 等{len(dep_blocked)}个" if len(dep_blocked) > 8 else ""
        lines.append(f"  依赖阻塞：{names}{extra}")
    if pending:
        names = "、".join(_node_label(n) for n in pending[:6])
        lines.append(f"  待调度：{names}")
    if failed:
        names = "、".join(_node_label(n) for n in failed)
        lines.append(f"  失败：{names}")

    # Attach busy detail when useful.
    if session.running_workers():
        lines.append(session.worker_progress_summary())

    return "\n".join(lines)


def _labels_for_ids(session: CoordinationSession, run_ids: set[str]) -> list[str]:
    """Stable role/run labels for a set of run_ids (plan first, else bare id)."""
    live = getattr(session, "live_plan", None)
    by_id: dict[str, str] = {}
    if live is not None:
        for n in live.nodes:
            by_id[n.run_id] = _node_label(n)
    labels = [by_id.get(rid, rid) for rid in sorted(run_ids)]
    return [lb for lb in labels if lb]


def format_idle_yield_brief(session: CoordinationSession) -> str:
    """CEO brief when idle-yield wakes with workers still in flight."""
    progress = format_pipeline_progress(session)
    healthy = is_pipeline_healthy(session)
    lines = ["【团队协调·空转让出】", progress, ""]
    from agentcore.workspace.limits import capability_dead_inject_lines

    lines.extend(
        capability_dead_inject_lines(
            workspace_channel_dead=bool(
                getattr(session, "workspace_channel_dead", False)
            ),
            exec_env_dead=bool(getattr(session, "exec_env_dead", False)),
        )
    )
    from agentcore.runtime.interaction_orphan import (
        format_hot_pending_hold_line,
        has_hot_user_pending,
    )

    conversation_id = getattr(session, "conversation_id", None) or ""
    if has_hot_user_pending(conversation_id):
        hold = format_hot_pending_hold_line(conversation_id)
        lines.append(
            f"流水线状态：{hold}"
            "对用户开口属【报告阻塞】：须说明有队员在等你允许、处理完后会继续；"
            "禁止只回「保持等待/保持静默」而无下文；禁止用进度旁白代替对审批等待的说明。"
            "报告阻塞后可 wait 听团（队还在）；勿 delegate 与现有计划重叠的队员。"
        )
    elif healthy:
        lines.append(
            "流水线状态：正常推进（有队员在跑，其余节点仅因依赖未就绪而阻塞，无失败）。"
            "这是预期中的等待，不是空闲——【无需追加动作】："
            "不要 delegate 再派与现有计划重叠的队员；不要为「好像闲着」重复派文案/前端/QA。"
            "图在转、无新结论时【可静默】——禁止用用户可见正文复述「谁还在跑/仍在检索」"
            "（协作图是进度真相）；对用户开口仅三选一：请示用户 / 报告阻塞与选项 / "
            "宣布阶段结论（非纯进度）。禁止只回「保持等待/保持静默」而无下文。"
            "无需对用户开口时可空响应；等 worker_completed / 波次前进事件再处置；"
            "仅当出现失败、升级仲裁、老板插话或明确缺口时再出手。"
            "cancel_worker 可用。"
        )
    else:
        lines.append(
            "协调等待窗口到期且仍有在途工作。【可静默】听团；"
            "禁止用用户可见正文复述进度；对用户开口仅三选一："
            "请示用户 / 报告阻塞与选项 / 宣布阶段结论。"
            "若队员疑似卡死再用 cancel_worker；发现计划缺口且职责/文件目标不与在图节点重叠时"
            "才可 delegate 追加。勿与未完成流水线节点抢同一交付物。"
        )
    return "\n".join(lines)
