"""Durable plan_review suspension (结构化挂起 2b)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.runtime.suspension.capture import SuspensionCapture, persist_suspension_capture

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

DelegateTool = Any


def can_persist_suspension(tool: DelegateTool) -> bool:
    """Whether this checkpoint should be durably persisted (结构化挂起 2b)."""
    return bool(
        tool._depth == 0
        and tool._message_id
        and tool._suspension_saver is not None
        and tool._conversation_id
    )


async def persist_suspension(
    tool: DelegateTool,
    checkpoint_id,
    plan: RunPlan,
    completed,
    steps,
    pending,
    required_event,
    ceo_review=None,
) -> bool:
    """Capture + persist the durable suspension frame for this pause (2b).

    Returns ``True`` iff a durable frame was actually saved. The 挂起即收口 (②) finalize
    path keys its「end the turn now」decision on this so it NEVER finalizes a plan it could
    not later resume — a nested (depth>0) / un-wired / transcript-less delegate returns
    ``False`` (caller may PROCEED). Runtime saver failure raises
    :class:`~agentcore.runtime.suspension.capture.SuspensionPersistError`.
    """
    if not can_persist_suspension(tool):
        return False
    from agentcore.runtime.suspension import PlanReviewSuspension, find_tool_call_id

    review = dict(ceo_review) if isinstance(ceo_review, dict) else None

    def build_frame(capture: SuspensionCapture) -> PlanReviewSuspension:
        return PlanReviewSuspension(
            message_id=tool._message_id or "",
            conversation_id=tool._conversation_id or "",
            user_id=tool._base_tool_context.user_id,
            captain_run_id=tool._captain_run_id or "",
            checkpoint_id=checkpoint_id,
            tool_call_id=find_tool_call_id(capture.transcript, "delegate"),
            base_system_prompt=tool._system_prompt,
            user_message=tool._user_message,
            folder_id=tool._folder_id,
            folder_binding_injected=bool(
                getattr(tool._base_tool_context, "folder_binding_injected", False)
            ),
            folder_local_root_id=getattr(
                tool._base_tool_context, "folder_local_root_id", None
            ),
            folder_local_subpath=getattr(
                tool._base_tool_context, "folder_local_subpath", None
            ),
            transcript=capture.transcript,
            history=capture.history,
            plan=plan,
            completed=dict(completed),
            journal_entries=capture.journal_entries,
            steps=steps,
            pending=pending,
            # 批次协作参数随帧走：耐久恢复换新工具实例（_coordination 缺省 none），
            # 不回灌则复核后续跑的波次 worker 被剥便签三件套。
            coordination=tool._coordination,
            team_brief=tool._team_brief,
            ceo_review=review,
            citations=capture.citations,
            trace_id=capture.trace_id,
        )

    return await persist_suspension_capture(
        checkpoint_id=checkpoint_id,
        required_event=required_event,
        build_frame=build_frame,
        saver=tool._suspension_saver,  # type: ignore[arg-type]
        sink=tool._sink,
        suspension_kind="plan_review",
    )


async def drop_suspension(tool: DelegateTool) -> None:
    """Delete the durable frame after a live in-process resolve / timeout (2b)."""
    if can_persist_suspension(tool) and tool._suspension_deleter is not None:
        await tool._suspension_deleter(tool._message_id or "")
