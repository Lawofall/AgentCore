"""CEO coordination tool: cancel_worker.

Telling a waiting worker (the old resolve path) lives on ``replan.tell`` and
calls :func:`settle_waiting_worker`.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.runtime.coordination.session import resolve_coordination_session
from agentcore.runtime.coordination.vacate import vacate_never_started_seat
from agentcore.runtime.interaction import default_interaction_registry
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema

logger = get_logger(__name__)


def _session_for_control(context: ToolContext):
    """Cancel looks up the observation graph.

    Cross-turn adopt leaves ``context.execution_id`` as this turn's mint (dispatch)
    while ``current_execution_id`` stays on the previous live graph. Fall back so
    control still finds that graph before this turn starts its own.
    """
    return resolve_coordination_session(context.execution_id)


class CancelWorkerTool:
    """Cancel one in-flight / queued worker during coordination (reuses cancel_run_ids)."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="cancel_worker",
            description="协调中终止一名在跑或排队未开的队员。",
            parameters={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "完整 run_id，或能唯一对应的角色名。",
                    },
                },
                "required": ["run_id"],
            },
            face=ToolFace.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        session = _session_for_control(context)
        raw = str(arguments.get("run_id") or "").strip()
        if not raw:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="cancel_worker 需要非空的 run_id。",
            )
        reason = str(arguments.get("reason") or "").strip()

        if session is None or not session.active:
            vacated = vacate_never_started_seat(
                session, context, raw=raw, reason=reason
            )
            if vacated is not None:
                return vacated
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="当前不在协调模式——仅在协调模式启动团队后可用（≥1 worker 默认；"
                "显式 coordinate=false 为阻塞路径）。",
            )

        # Resolve the CEO-supplied name (often a role / short name) to a live
        # worker's full run_id — the scheduler cancels by exact run_id, so an
        # unresolved short name would silently never cancel (fake success).
        resolution = session.resolve_cancel_target(raw)
        if resolution.run_id is None:
            # Already terminal for this session (completed / failed / skipped /
            # cancelled / handoff) → idempotent success (no tool red-error).
            # Truly unknown ids still fail below — never auto-retarget.
            ended = session.resolve_ended_worker(raw)
            if ended.run_id is not None:
                ended_id = ended.run_id
                logger.info(
                    "coordination.worker_cancel_already_ended",
                    execution_id=session.execution_id,
                    run_id=ended_id,
                    raw=raw,
                    match=ended.reason,
                )
                msg = f"worker {ended_id} 已结束，无需取消。"
                if ended_id != raw:
                    msg = (
                        f"worker {ended_id}（由「{raw}」解析）已结束，无需取消。"
                    )
                return ToolResult(tool_call_id="", success=True, output=msg)
            # Queued on live_plan but not yet running → formal withdraw (skipped /
            # vacated + cancel_ids so Wave will not launch). Never fake-success.
            pending = session.resolve_pending_worker(raw)
            if pending.run_id is not None:
                pending_id = pending.run_id
                session.vacate_pending_worker(pending_id)
                from agentcore.runtime.coordination.cancel_close import (
                    note_cancel_worker_success,
                )

                note_cancel_worker_success(session, pending_id, started=False)
                from agentcore.runtime.coordination.journal import (
                    record_coordination_snapshot,
                )

                record_coordination_snapshot(session)
                logger.info(
                    "coordination.worker_cancel_pending_withdrawn",
                    execution_id=session.execution_id,
                    run_id=pending_id,
                    raw=raw,
                    match=pending.reason,
                    reason=reason[:120] if reason else "",
                )
                msg = f"worker {pending_id} 已从队列撤出"
                if pending_id != raw:
                    msg += f"（由「{raw}」解析）"
                if reason:
                    msg += f"（原因：{reason}）"
                msg += "。"
                return ToolResult(tool_call_id="", success=True, output=msg)
            running = session.running_workers()
            if ended.reason == "ambiguous" or pending.reason == "ambiguous":
                amb = ended if ended.reason == "ambiguous" else pending
                listing = "；".join(amb.candidates) or "（无）"
                hint = (
                    f"「{raw}」同时匹配多个已结束或排队节点，无法确定目标。"
                    f"请改用完整 run_id。候选：{listing}。"
                )
            elif not running:
                hint = (
                    f"找不到匹配「{raw}」的在跑或排队 worker：当前没有可取消的目标"
                    "（可能都已完成或已被取消）。"
                )
            else:
                listing = "；".join(f"{rid}（{role}）" for rid, role in running)
                if resolution.reason == "ambiguous":
                    hint = (
                        f"「{raw}」同时匹配多个在跑 worker，无法确定取消目标。"
                        f"请改用完整 run_id。当前在跑（run_id｜角色）：{listing}。"
                    )
                else:
                    hint = (
                        f"找不到匹配「{raw}」的在跑或排队 worker。"
                        f"当前可取消（run_id｜角色）：{listing}。"
                    )
                # Hint-only: same live_plan role has a unique runner — CEO must
                # re-call; do not request_cancel the suggestion.
                suggestion = session.suggest_cancel_by_plan_role(raw)
                if suggestion is not None:
                    sid, srole = suggestion
                    hint += (
                        f" 你要取消的或许是 {sid}（{srole}）；"
                        "请确认后用该 run_id 重试（不会自动改目标）。"
                    )
            logger.info(
                "coordination.worker_cancel_unresolved",
                execution_id=session.execution_id,
                raw=raw,
                match=resolution.reason,
                candidates=list(resolution.candidates),
                running=len(running),
            )
            return ToolResult(tool_call_id="", success=False, output="", error=hint)

        run_id = resolution.run_id
        session.request_cancel(run_id)
        from agentcore.runtime.coordination.cancel_close import (
            note_cancel_worker_success,
            worker_was_started,
        )

        note_cancel_worker_success(
            session, run_id, started=worker_was_started(session, run_id)
        )
        from agentcore.runtime.coordination.journal import record_coordination_snapshot

        record_coordination_snapshot(session)
        logger.info(
            "coordination.worker_cancel_requested",
            execution_id=session.execution_id,
            run_id=run_id,
            raw=raw,
            match=resolution.reason,
            reason=reason[:120] if reason else "",
        )
        msg = f"已请求终止 worker {run_id}"
        if run_id != raw:
            msg += f"（由「{raw}」解析）"
        if reason:
            msg += f"（原因：{reason}）"
        msg += "。调度器将在下一轮取消该任务。"
        return ToolResult(tool_call_id="", success=True, output=msg)


def settle_waiting_worker(
    session: Any,
    *,
    run_id: str,
    answer: str,
    conversation_id: str = "",
) -> ToolResult:
    """Hand ``answer`` to a worker parked on escalate(wait) and let that run continue.

    ``via_user`` is taken from the session: this stretch already received an
    ``ask_user`` answer. The caller clears that flag after the whole tell batch.
    """
    if session is None or not getattr(session, "active", False):
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error="当前没有进行中的团队，没法把话送给停着的人。",
        )
    via_user = bool(getattr(session, "user_consulted", False))
    pending = session.get_arbitration(run_id)
    if pending is None:
        session.stash_resolution(run_id, answer=answer, via_user=via_user)
        from agentcore.runtime.coordination.journal import record_coordination_snapshot

        record_coordination_snapshot(session)
        logger.info(
            "coordination.escalation_stashed",
            execution_id=session.execution_id,
            run_id=run_id,
            via_user=via_user,
        )
        who = "（经用户）" if via_user else ""
        return ToolResult(
            tool_call_id="",
            success=True,
            output=f"已记下对 {run_id} 的话{who}；他恢复后会按这句继续。",
        )
    escalation_id = str(pending.get("escalation_id") or "")
    conv = str(pending.get("conversation_id") or conversation_id or "")
    registry = default_interaction_registry()
    settled = registry.resolve(
        escalation_id,
        {"answer": answer, "via_user": via_user},
        conversation_id=conv,
    )
    if not settled:
        session.stash_resolution(run_id, answer=answer, via_user=via_user)
        stashed = session.resolved_arbitrations.get(run_id)
        if stashed is not None and escalation_id:
            stashed["escalation_id"] = escalation_id
        from agentcore.runtime.coordination.journal import record_coordination_snapshot

        record_coordination_snapshot(session)
        logger.info(
            "coordination.escalation_stashed_after_miss",
            execution_id=session.execution_id,
            run_id=run_id,
            via_user=via_user,
        )
        who = "（经用户）" if via_user else ""
        return ToolResult(
            tool_call_id="",
            success=True,
            output=f"已记下对 {run_id} 的话{who}；他恢复后会按这句继续。",
        )
    session.clear_arbitration(run_id)
    from agentcore.runtime.coordination.journal import record_coordination_snapshot

    record_coordination_snapshot(session)
    logger.info(
        "coordination.escalation_resolved",
        execution_id=session.execution_id,
        run_id=run_id,
        via_user=via_user,
    )
    who = "（经用户）" if via_user else ""
    return ToolResult(
        tool_call_id="",
        success=True,
        output=f"已把话传给 {run_id}{who}，他会按这句继续。",
    )
