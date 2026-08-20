"""Durable ask_user suspension (结构化挂起 2b)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.runtime.checkpoints import AskCheckpointIntent
from agentcore.runtime.suspension.capture import SuspensionCapture, persist_suspension_capture
from agentcore.tools.protocol import ToolContext

if TYPE_CHECKING:
    from agentcore.tools.builtin.ask_user.tool import AskUserTool


def can_persist_suspension(tool: AskUserTool) -> bool:
    """Whether this ask_user pause can be durably persisted (结构化挂起 2b).

    The turn's ``message_id`` + the persist closure must be wired (the live CEO
    path). Un-wired constructions (tests / standalone) return False — under D11
    that means the tool fails the turn (no in-memory wait fallback)."""
    return bool(tool.message_id and tool.suspension_saver is not None and tool.conversation_id)


async def persist_suspension(
    tool: AskUserTool,
    *,
    checkpoint_id: str,
    context: ToolContext,
    message: str,
    assumptions: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    required_event: Any,
    intent: AskCheckpointIntent,
    browser_login: bool = False,
) -> bool:
    """Capture + persist the durable suspension frame for this ask_user pause (2b).

    Reads the CEO transcript off the ``captain_transcript`` contextvar (published
    by the captain executor) — without it a faithful resume is impossible, so
    capture is skipped and this returns ``False``. Folds the about-to-emit
    ``checkpoint_required`` into the frame's journal so a resume replays the
    prompt+resolution as a pair.

    Returns ``True`` iff a durable frame was actually saved. The 挂起即收口 (②)
    finalize path keys its「end the turn now」decision on this so it NEVER finalizes a
    turn it could not later resume. Under D11 an un-wired / transcript-less /
    saver-failed construction returns ``False`` and the tool **explicitly fails** the
    turn (no in-memory timed wait / no auto-continue).
    """
    if not can_persist_suspension(tool):
        return False
    from agentcore.runtime.suspension import AskUserSuspension, claim_next_tool_call_id

    def build_frame(capture: SuspensionCapture) -> AskUserSuspension:
        return AskUserSuspension(
            message_id=tool.message_id or "",
            conversation_id=tool.conversation_id,
            user_id=context.user_id,
            captain_run_id=tool.captain_run_id or "",
            checkpoint_id=checkpoint_id,
            tool_call_id=claim_next_tool_call_id(
                tool.message_id or "", capture.transcript, "ask_user"
            ),
            base_system_prompt=tool.base_system_prompt,
            user_message=tool.user_message,
            folder_id=tool.folder_id,
            folder_binding_injected=bool(
                getattr(context, "folder_binding_injected", False)
            ),
            folder_local_root_id=getattr(context, "folder_local_root_id", None),
            folder_local_subpath=getattr(context, "folder_local_subpath", None),
            memory_enabled=tool.memory_enabled,
            conversation_history_access=tool.conversation_history_access,
            transcript=capture.transcript,
            history=capture.history,
            question=message,
            assumptions=assumptions,
            questions=questions,
            intent=intent,
            browser_login=browser_login,
            journal_entries=capture.journal_entries,
            citations=capture.citations,
            trace_id=capture.trace_id,
        )

    return await persist_suspension_capture(
        checkpoint_id=checkpoint_id,
        required_event=required_event,
        build_frame=build_frame,
        saver=tool.suspension_saver,  # type: ignore[arg-type]
        sink=tool.sink,
        suspension_kind="ask_user",
        message_id=tool.message_id or "",
    )


async def drop_suspension(tool: AskUserTool) -> None:
    """Delete the durable frame after a live in-process resolve (2b; rare hot path).

    Primary path is 挂起即收口 (②): frame stays until cold ``POST .../resume`` claims it.
    """
    if can_persist_suspension(tool) and tool.suspension_deleter is not None:
        await tool.suspension_deleter(tool.message_id or "")
