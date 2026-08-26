"""Drive-loop setup: note wall, executor, boundary hook, delegation grant."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.delegate.boundary import boundary_hook, checkpoint_active
from agentcore.runtime.events import team_note_posted
from agentcore.runtime.runs.types import RunSpec, RunState

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

type DelegateTool = Any

logger = get_logger(__name__)


def setup_note_wall(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    execution_id: str,
    coordination: str,
    seed_completed: dict[str, RunState] | None,
    seed_notes: list[dict[str, str]] | None,
) -> tuple[Any, bool]:
    """Create / inherit this batch's NoteWall; stash on tool for CEO finalize paths.

    Returns ``(note_wall | None, collaboration)``.
    """
    from agentcore.runtime.delegate.seed_notes import is_note_wall_batch, seed_note_wall
    from agentcore.runtime.runs.notewall import NoteWall

    # 团队便签墙 (§2.2 通 / §2.3 合·对账): own this batch's wall here so the CEO finalize can fold
    # its outstanding 决定 / 认领 into 语义边界对账. Passed into the executor (workers post / read /
    # amend on it) AND stashed on the tool so format_for_ceo reaches it on BOTH finalize paths
    # (normal 终态 below + replan(stop) finalize_stopped). One wall per drive call = per fan-out
    # batch, matching the wall's existing per-batch visibility scope.
    # 存在性由 CEO 的 coordination 声明（缺省 none）；light 隐含 none。collaboration 仍走既有开关。
    collaboration = is_note_wall_batch(len(plan.nodes), coordination)
    if not collaboration:
        tool._note_wall = None
        return None, False

    prev_wall = tool._note_wall
    note_wall = NoteWall()
    # 继承与 seed 无关：一个 replan 续跑 / 同回合追加批【必然】带 seed_completed，而那恰恰
    # 是最需要旧墙的批——续跑的 worker 要看见队友已广播的决定与认领，CEO 收尾也要拿这些
    # 便签做对账。此前把「有 seed」当成「新批」而换成空墙，等于每次续跑都把团队共识清零。
    # 存在 prev_wall 本身就意味着同一 CEO 回合的上一批（跨回合 / 耐久恢复走全新实例，
    # prev_wall 为 None，自然不继承）。
    if prev_wall is not None:
        inherited = note_wall.inherit(prev_wall.active_notes())
        for note in inherited:
            tool._sink.emit(
                team_note_posted(
                    execution_id=execution_id,
                    note_id=note.note_id,
                    run_id=note.run_id,
                    agent_id=note.agent_id,
                    role=note.role,
                    kind=note.kind,
                    text=note.text,
                    ts=note.ts,
                    source="inherited",
                )
            )
        if inherited:
            logger.info(
                "delegate.inherit_notes",
                count=len(inherited),
                execution_id=execution_id,
            )
    tool._note_wall = note_wall
    # 空 seed（None=全新批 / {}=开工卡耐久恢复，尚无 worker 完成、墙从未活过）才补种：
    # 开工卡挂起发生在本函数之前，CEO 预贴便签从未上墙，恢复必须补贴；非空 seed
    # （checkpoint 复核 / 跨回合追加 / retry）意味着原批已跑过，种子沿旧口径不重贴。
    if seed_notes and not seed_completed:
        seed_note_wall(
            note_wall,
            seed_notes,
            sink=tool._sink,
            execution_id=execution_id,
        )
    return note_wall, True


def resolve_worker_gate(tool: DelegateTool) -> Any:
    """Hand workers whatever gate this turn has — never predict away the card.

    Upstream used to guess「这批 worker 用不上逐次卡」from ``backend.location`` (plus a
    patched-in roster scan for desktop-touch / 恒确认 tools) and pass ``None``. That
    guess has to duplicate the sandbox→approval table, and every miss silently
    ungates: 恒确认 died here once, and ``file_write=ask`` on cloud never reached its
    implementation because a worker holding only file tools got ``None`` right here.

    ``None`` now means one thing only — this turn has nobody to ask. Whether a given
    call needs a card is decided at the single chokepoint (``tool_exec_gates``),
    which reads the same ``sandbox_approval`` table with the actual tool name, its
    arguments and the session axes in hand.
    """
    return tool._approval_gate


def build_drive_executor(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    execution_id: str,
    worker_gate: Any,
    note_wall: Any,
    collaboration: bool,
    session: Any,
) -> Callable[[RunSpec, dict], Awaitable[RunState]]:
    """Cold agent executor wrapped with continuation + optional coordination timeouts."""
    from agentcore.runtime.delegate.captain_recon import resolve_captain_recon_for_delegate
    from agentcore.runtime.runs import build_agent_executor
    from agentcore.runtime.suspension import turn_evidence_ledger as _turn_ledger_var

    captain_recon = resolve_captain_recon_for_delegate(depth=int(getattr(tool, "_depth", 0) or 0))
    if captain_recon:
        from agentcore.core.logging import get_logger

        get_logger(__name__).info(
            "delegate.captain_recon_injected",
            chars=len(captain_recon),
            depth=int(getattr(tool, "_depth", 0) or 0),
        )

    cold_executor = build_agent_executor(
        plan=plan,
        llm=tool._llm,
        tools=tool._tools,
        sink=tool._sink,
        base_tool_context=tool._base_tool_context,
        profile_set=tool._profile_set,
        system_prompt=tool._system_prompt,
        user_message=tool._user_message,
        execution_id=execution_id,
        approval_gate=worker_gate,
        delegate_factory=lambda captain_run_id, captain_depth: tool.spawn_lead_subteam(
            captain_run_id, captain_depth
        ),
        interaction_bridge=tool._registry,
        escalation_timeout=tool._checkpoint_timeout_seconds,
        escalation_armed=checkpoint_active(tool),
        note_wall=note_wall,
        collaboration=collaboration,
        team_brief=tool._team_brief,
        captain_recon=captain_recon or None,
        # 回合入口绑定的共享台账（与 CEO 同一对象）；辩论 executor 不经此路径。
        turn_evidence_ledger=_turn_ledger_var.get(),
        session_folder_id=getattr(tool, "_folder_id", None),
        local_root_claims=getattr(tool, "_local_root_claims", None),
        permission_axes_obj=getattr(tool, "_permission_axes", None),
    )

    async def continuation_aware_executor(spec: RunSpec, completed: dict) -> RunState:
        """带 continue_from_run_id 的节点走续写；其余冷开局。"""
        if spec.continue_from_run_id:
            from agentcore.runtime.delegate.continuation import run_continuation

            return await run_continuation(
                tool,
                spec,
                completed,
                execution_id=execution_id,
                approval_gate=worker_gate,
            )
        return await cold_executor(spec, completed)

    executor: Callable[[RunSpec, dict], Awaitable[RunState]] = continuation_aware_executor
    # Hard-timeout chain (warn → TIMEOUT → grace → force cancel). Coordination
    # sessions post CEO TIMEOUT + cancel_ids; nested depth>0 blocking drives use
    # the same registry without a session (timeout_s is no longer a dead field).
    from agentcore.runtime.coordination.bridge import wrap_executor_with_timeouts

    executor = wrap_executor_with_timeouts(executor, session)
    return executor


def resolve_on_boundary(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    complexity_hint: str,
    session: Any,
) -> Any:
    """Wave boundary hook (checkpoint / bind / coordination SCOPE)."""
    # light 与 depends_on / bind_after_deps / checkpoint_after 并存时忽略 light：
    # 不得据 light 关掉波边界（否则晚绑定节点会带占位 role/task 直接跑）。
    has_dag_boundary = any(
        n.bind_after_deps or n.depends_on or n.checkpoint_after for n in plan.nodes
    )
    if complexity_hint == "light" and not has_dag_boundary:
        on_boundary = None
    else:
        on_boundary = (
            boundary_hook(tool, plan)
            if (
                checkpoint_active(tool)
                or any(n.bind_after_deps for n in plan.nodes)
                or any(n.depends_on for n in plan.nodes)
            )
            else None
        )
    # Phase 3: under coordination, SCOPE/dep escalations → CEO event queue (PROCEED),
    # not wave-boundary YIELD. CHECKPOINT skips durable plan_review (boundary_hook →
    # ``_pending_boundary`` only); BIND still uses the base hook when present.
    if session is not None:
        from agentcore.runtime.coordination.bridge import coordination_boundary_hook

        # Always wire a hook so SCOPE can fire even when the plan has no depends_on /
        # checkpoint markers (parallel fan-out with escalate kind=scope).
        on_boundary = coordination_boundary_hook(session, on_boundary)
    return on_boundary


def apply_delegation_grant(
    tool: DelegateTool,
    *,
    execution_id: str,
    worker_gate: Any,
    seed_completed: dict[str, RunState] | None,
) -> bool:
    """Kickoff grant from resume / full_auto. Returns whether grant was started this call.

    ``True`` means this drive segment owns revoke-on-exit (unless a live coordination
    session keeps the grant for merge-rearm — see ``drive`` finally).
    """
    # Kickoff grant: issued by resume (continue/adjust) or full_auto auto-grant.
    # Hot-path ``request_delegation_authorization`` retired — capability auth lives
    # on the durable开工卡 (team_preview) or is silent under full_auto.
    if worker_gate is None:
        return False
    # Mid-plan resume already granted on the kickoff continue path; do not treat as
    # a fresh segment owner (avoids double-revoke bookkeeping). Still a no-op apply.
    if seed_completed is not None:
        return False
    from agentcore.core.types import DEFAULT_PERMISSION_AXES

    auto = bool(getattr(tool, "_auto_grant_pending", False))
    already = worker_gate.has_delegation_grant(execution_id)
    axes = getattr(tool, "_permission_axes", None) or DEFAULT_PERMISSION_AXES
    if auto or already or axes.auto_executes:
        if not already:
            worker_gate.grant_delegation(execution_id)
        tool._auto_grant_pending = False  # type: ignore[attr-defined]
        # already=True (e.g. merge-rearm after prior drive kept the grant): not a new
        # segment owner — caller must not revoke on exit.
        return not already
    return False
