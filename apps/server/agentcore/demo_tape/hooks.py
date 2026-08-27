"""Entry hooks that divert stream_chat / resume_chat into demo tape playback."""

from __future__ import annotations

from agentcore.core.logging import get_logger
from agentcore.demo_tape.binding import resolve_binding
from agentcore.demo_tape.player import continue_tape_turn, play_tape_turn
from agentcore.demo_tape.schema import is_demo_tape_frame
from agentcore.runtime.checkpoints import CheckpointResponse
from agentcore.runtime.events import EventSink
from agentcore.runtime.suspension import (
    AskUserSuspension,
    PlanReviewSuspension,
    TurnSuspension,
)

logger = get_logger(__name__)

_TAPE_RESUME_KINDS = (AskUserSuspension, PlanReviewSuspension)


def try_resolve_tape_binding(conversation_id: str):
    """Return a TapeBinding when demo replay is enabled and this conversation is bound."""
    return resolve_binding(conversation_id)


async def run_tape_turn_if_bound(
    *,
    conversation_id: str,
    sink: EventSink,
    message_id: str,
    user_id: str,
    user_message: str,
    folder_id: str | None,
    trace_id: str | None,
) -> dict | None:
    """If bound, play the tape and return a pipeline-shaped result; else None."""
    binding = resolve_binding(conversation_id)
    if binding is None:
        return None
    logger.info(
        "demo_tape.turn_divert",
        conversation_id=conversation_id,
        message_id=message_id,
        tape=str(binding.tape_path),
        turn_index=binding.turn_index,
        speed=binding.speed,
        max_gap_ms=binding.max_gap_ms,
    )
    return await play_tape_turn(
        binding=binding,
        sink=sink,
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        user_message=user_message,
        folder_id=folder_id,
        trace_id=trace_id,
    )


async def run_tape_resume_if_marked(
    *,
    suspension: TurnSuspension,
    response: CheckpointResponse,
    sink: EventSink,
    folder_id: str | None,
    trace_id: str | None,
) -> dict | None:
    """If the claimed frame is a demo-tape pause, continue the tape; else None."""
    if not is_demo_tape_frame(suspension):
        return None
    if not isinstance(suspension, _TAPE_RESUME_KINDS):
        logger.warning(
            "demo_tape.resume_skipped_unsupported_kind",
            pause_type=type(suspension).__name__,
        )
        return None
    logger.info(
        "demo_tape.resume_divert",
        kind=getattr(suspension, "kind", None),
        conversation_id=getattr(suspension, "conversation_id", None),
        message_id=getattr(suspension, "message_id", None),
    )
    return await continue_tape_turn(
        suspension=suspension,
        response=response,
        sink=sink,
        folder_id=folder_id,
        trace_id=trace_id,
    )
