"""CEO coordination tools: wait + update_synthesis + cancel_worker
+ resolve_escalation + queue_user_message.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.coordination.session import resolve_coordination_session
from agentcore.runtime.coordination.vacate import vacate_never_started_seat
from agentcore.runtime.events import team_synthesis_preview
from agentcore.runtime.interaction import default_interaction_registry
from agentcore.runtime.resolve.ceo_surface import COORDINATION_PERIOD_HINT
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema

logger = get_logger(__name__)


def _session_for_control(context: ToolContext):
    """Wait / cancel / synthesis look up the observation graph.

    Cross-turn adopt leaves ``context.execution_id`` as this turn's mint (dispatch)
    while ``current_execution_id`` stays on the previous live graph. Fall back so
    CEO wait still finds that graph before this turn starts its own.
    """
    return resolve_coordination_session(context.execution_id)


class WaitTool:
    """No-op exit for coordination rounds that need no disposition.

    Models often feel compelled to emit a tool call even when the brief says
    「无需处置」; without this primitive they re-call ``delegate`` and hit the
    isomorphic guard (``status=error`` + wasted LLM round). Calling ``wait`` is
    an explicit, side-effect-free acknowledgement that the captain stays in
    listen mode until the next team event.
    """

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="wait",
            description=(
                "协调中无需处置时调用：确认继续等团队事件。"
                f"{COORDINATION_PERIOD_HINT}"
                "勿用 delegate / update_synthesis 占位等待。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "可选：为何无需处置（仅记日志，用户不可见）。",
                    },
                },
                "required": [],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        session = _session_for_control(context)
        if session is None or not session.active:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="当前不在协调模式——仅在协调模式启动团队后可用。",
            )
        from agentcore.runtime.interaction_orphan import (
            format_hot_pending_hold_line,
            has_hot_user_pending,
        )

        conversation_id = (
            getattr(session, "conversation_id", None) or context.conversation_id or ""
        )
        if has_hot_user_pending(conversation_id):
            hold = format_hot_pending_hold_line(conversation_id)
            logger.info(
                "coordination.wait",
                execution_id=session.execution_id,
                completed=len(session.completed_run_ids),
                total=session.total_workers,
                reason="hot_pending_listen",
            )
            return ToolResult(
                tool_call_id="",
                success=True,
                output=(
                    f"{hold}\n"
                    "本 wait 视为听团，不是推进。请先向用户报告阻塞（等你允许）；"
                    "队还在，勿整队收场。"
                ),
            )
        reason = str(arguments.get("reason") or "").strip()
        logger.info(
            "coordination.wait",
            execution_id=session.execution_id,
            completed=len(session.completed_run_ids),
            total=session.total_workers,
            reason=reason[:120] if reason else "",
        )
        return ToolResult(
            tool_call_id="",
            success=True,
            output=(
                "已确认等待团队事件（无需处置）。继续静默听团；"
                "勿再为等待而调用 delegate / update_synthesis。"
            ),
        )


class UpdateSynthesisTool:
    """Update the progressive CEO synthesis draft and push ``team_synthesis_preview``."""

    def __init__(self, *, sink: Any) -> None:
        self._sink = sink

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="update_synthesis",
            description=(
                "协调中更新合成草稿（用户可见预览，非终稿）。"
                "只在里程碑：新结论、冲突、方向修正、阶段收束。"
                "例行完成不要调；终稿用正文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "draft": {
                        "type": "string",
                        "description": "合成草稿全文（覆盖上一版）。须含新结论或方向变化。",
                    },
                },
                "required": ["draft"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        session = _session_for_control(context)
        if session is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="当前不在协调模式——仅在协调模式启动团队后可用（≥1 worker 默认；"
                "显式 coordinate=false 为阻塞路径）。",
            )
        if not session.active:
            # Team finished and session closed — soft tip, not error (avoids burning a
            # CEO retry round). Distinct from「从未开团」(session is None above).
            return ToolResult(
                tool_call_id="",
                success=True,
                output=(
                    "团队已全部完成，协调会话已收口。请直接用正文写出最终合成"
                    "（content_delta），不必再调 update_synthesis。"
                ),
            )
        draft = str(arguments.get("draft") or "").strip()
        if not draft:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="update_synthesis 需要非空的 draft。",
            )
        # 合成预览跟对话成稿同一口径：已登记号（含 search-only）保留；不剥正文。
        session.update_draft(draft)
        done = len(session.completed_run_ids)
        total = session.total_workers
        headline = f"合成草稿更新 · 已完成 {done}/{total}"
        self._sink.emit(
            team_synthesis_preview(
                execution_id=session.execution_id,
                completed=done,
                total=total,
                headline=headline,
                text=draft,
                workers=[],
                in_progress=True,
            )
        )
        # Persist coordination state into the turn journal for ask_user / resume.
        from agentcore.runtime.coordination.journal import record_coordination_snapshot

        record_coordination_snapshot(session)
        logger.info(
            "coordination.synthesis_updated",
            execution_id=session.execution_id,
            draft_chars=len(draft),
            completed=done,
            total=total,
        )
        # 插话 addressed 由编排循环在 CEO 工具步汇合点统一标记
        # （update_synthesis / delegate / cancel_worker），勿在各工具里逐个补。
        return ToolResult(
            tool_call_id="",
            success=True,
            output=f"已更新合成草稿（{len(draft)} 字），用户可见「进展中」预览。",
        )


class CancelWorkerTool:
    """Cancel one in-flight / queued worker during coordination (reuses cancel_run_ids)."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="cancel_worker",
            description=(
                "协调中终止一名在跑或排队未开的队员。"
                "追加全新队员用 delegate；波边界让出后用 replan。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "完整 run_id，或能唯一对应的角色名。",
                    },
                    "reason": {
                        "type": "string",
                        "description": "可选：终止原因（记入协调日志）。",
                    },
                },
                "required": ["run_id"],
            },
            category=ToolCategory.ORCHESTRATION,
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


class ResolveEscalationTool:
    """CEO arbitration: settle a worker's blocking escalate parked for the CEO (D1)."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="resolve_escalation",
            description=(
                "协调中兑现队员阻塞升级。技术/范围直接答；"
                "偏好、授权、花钱先 ask_user 再调，并 via_user=true。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "挂起等待仲裁的 worker run_id。",
                    },
                    "answer": {
                        "type": "string",
                        "description": "裁决正文（worker 将据此继续，优先于其暂定假设）。",
                    },
                    "via_user": {
                        "type": "boolean",
                        "description": (
                            "可选，默认 false。true=本裁决经 ask_user 征询用户后作出"
                            "（偏好/授权/费用类必须如此）。"
                        ),
                    },
                },
                "required": ["run_id", "answer"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        session = _session_for_control(context)
        if session is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="当前不在协调模式——仅在协调模式启动团队后可用（≥1 worker 默认；"
                "显式 coordinate=false 为阻塞路径）。",
            )
        if not session.active:
            # Team finished and session closed — soft tip, not error (avoids burning a
            # CEO retry round on a now-idempotent late arbitration). Distinct from
            # 「从未开团」(session is None above); mirrors UpdateSynthesisTool's stance.
            return ToolResult(
                tool_call_id="",
                success=True,
                output=(
                    "团队已全部完成，协调会话已收口，升级仲裁无需再兑现。"
                    "请直接用正文写出最终答复（content_delta）。"
                ),
            )
        run_id = str(arguments.get("run_id") or "").strip()
        answer = str(arguments.get("answer") or "").strip()
        via_user = bool(arguments.get("via_user"))
        if not run_id:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="resolve_escalation 需要非空的 run_id。",
            )
        if not answer:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="resolve_escalation 需要非空的 answer（你的裁决）。",
            )

        pending = session.get_arbitration(run_id)
        transfer_note = ""

        if pending is None:
            # Worker may already have been cancelled (ask_user soft-stop); stash for
            # the re-armed worker's next escalate(blocking=true).
            session.stash_resolution(run_id, answer=answer, via_user=via_user)
            from agentcore.runtime.coordination.journal import record_coordination_snapshot

            record_coordination_snapshot(session)
            logger.info(
                "coordination.escalation_stashed",
                execution_id=session.execution_id,
                run_id=run_id,
                via_user=via_user,
            )
            return ToolResult(
                tool_call_id="",
                success=True,
                output=(
                    f"已记录对 {run_id} 的裁决"
                    f"{'（经用户）' if via_user else ''}"
                    f"{transfer_note}；"
                    "该队员恢复后将收到裁决并继续。"
                ),
            )
        escalation_id = str(pending.get("escalation_id") or "")
        conversation_id = str(pending.get("conversation_id") or context.conversation_id or "")
        registry = default_interaction_registry()
        settled = registry.resolve(
            escalation_id,
            {"answer": answer, "via_user": via_user},
            conversation_id=conversation_id,
        )
        if not settled:
            # Live Future gone — stash for re-armed pickup.
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
            return ToolResult(
                tool_call_id="",
                success=True,
                output=(
                    f"已记录对 {run_id} 的裁决"
                    f"{'（经用户）' if via_user else ''}"
                    f"{transfer_note}；"
                    "挂起已解除或队员正重入，裁决将在其恢复时送达。"
                ),
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
        return ToolResult(
            tool_call_id="",
            success=True,
            output=(
                f"已将裁决回传给 worker {run_id}"
                f"{'（经用户征询）' if via_user else ''}"
                f"{transfer_note}，队员将据此继续。"
            ),
        )


class QueueUserMessageTool:
    """Defer an unrelated mid-flight user interjection to the conversation turn queue."""

    def __init__(self, *, sink: Any) -> None:
        self._sink = sink

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="queue_user_message",
            description=(
                "把与当前团队无关的插话排到下一回合。相关插话图内处置。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "interjection_id": {
                        "type": "string",
                        "description": "协调事件里的 interjection_id。",
                    },
                    "reason": {
                        "type": "string",
                        "description": "可选：为何转入排队（用户可见）。",
                    },
                },
                "required": ["interjection_id"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        from agentcore.runtime.coordination.interjections import (
            enqueue_interjection_to_fifo,
            final_answer_covers,
            mark_interjection_addressed,
            mark_interjection_failed,
        )

        session = _session_for_control(context)
        iid = str(arguments.get("interjection_id") or "").strip()
        if not iid:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="queue_user_message 需要非空的 interjection_id。",
            )
        if session is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="找不到协调会话，无法处理该插话。请在下一条用户消息中再说一次。",
            )
        # Already terminal (close promote / prior queue / addressed) — idempotent OK.
        if iid in session.dispositioned_interjections and session.get_interjection(iid) is None:
            return ToolResult(
                tool_call_id="",
                success=True,
                output="该插话已转入下回合排队或已在本回合消化，无需再调。",
            )
        stashed = session.take_interjection(iid)
        if stashed is None:
            # Race: close already promoted, or bad id.
            if iid in session.dispositioned_interjections:
                return ToolResult(
                    tool_call_id="",
                    success=True,
                    output="该插话已转入下回合排队或已在本回合消化，无需再调。",
                )
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    f"找不到插话 {iid}（已转排队、已失效，或 id 有误）。"
                    "请核对协调事件里的 interjection_id。"
                ),
            )
        reason = str(arguments.get("reason") or "").strip()
        ok, msg, status = enqueue_interjection_to_fifo(
            session,
            iid,
            stashed,
            sink=self._sink,
            reason=reason or None,
        )
        if ok:
            pos = getattr(status, "position", 1)
            depth = getattr(status, "queue_depth", 1)
            return ToolResult(
                tool_call_id="",
                success=True,
                output=(
                    f"已将插话转入对话级排队（位置 {pos}/{depth}）。"
                    "当前回合结束后自动起新回合处理。"
                ),
            )
        # True enqueue failure: 终局已答 → addressed；否则 failed（禁止假绿）.
        if final_answer_covers(session):
            mark_interjection_addressed(
                session,
                iid,
                stashed,
                sink=self._sink,
                note="排队未果，但终局已回应",
            )
            return ToolResult(
                tool_call_id="",
                success=True,
                output="排队通道异常，但终局正文已覆盖该插话，已标为已消化。",
            )
        mark_interjection_failed(
            session,
            iid,
            stashed,
            sink=self._sink,
            note=msg or "未能排队，请重试或再说一次",
        )
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error=msg or "转入对话级排队失败。",
        )
