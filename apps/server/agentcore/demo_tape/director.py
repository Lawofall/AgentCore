"""Director-console control operations (dev-only).

Pause / speed are in-process transport mutations. Forward seek arms a burst on
the live metronome (and auto-resumes a durable wired pause when crossing it).
Backward seek is restart-style: stop the turn, clear transcript, re-run the
tape from the top with burst armed to the target.

This module stays free of ``api.routes`` imports — the route layer runs LLM
preflight and passes credentials in.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentcore.conversation.service import resume_chat, stream_chat
from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import MessageRepository
from agentcore.demo_tape.binding import resolve_binding
from agentcore.demo_tape.chapters import (
    TapeChapter,
    build_chapters,
    chapter_by_id,
    snap_to_event_index,
)
from agentcore.demo_tape.export import load_tape, tape_turns
from agentcore.demo_tape.launch import require_replay_enabled
from agentcore.demo_tape.schema import (
    TAPE_INTERACTIVE_PAUSE_KINDS,
    event_type,
    is_demo_tape_frame,
)
from agentcore.demo_tape.transport import (
    PlaybackState,
    PlaybackTransport,
    transport_registry,
)
from agentcore.llm.resolve import LLMCredentials
from agentcore.runtime.approvals import ApprovalDecision
from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import InteractionKind, default_interaction_registry
from agentcore.runtime.settlement import prewrite_cold_resume_settlement
from agentcore.runtime.suspension import (
    AskUserSuspension,
    PlanReviewSuspension,
)
from agentcore.runtime.suspension.persistence import (
    claim_paused_turn,
    delete_paused_turn,
    list_paused_turns,
    load_paused_turn,
)
from agentcore.runtime.turn.runs import turn_runs

logger = get_logger(__name__)

SinkSetup = Callable[[EventSink], None]

_TAPE_DURABLE_KINDS = (AskUserSuspension, PlanReviewSuspension)


def _tape_id_for(path: Path) -> str:
    return path.stem


def _load_act_events(tape_path: Path, turn_index: int = 0) -> list[dict[str, Any]]:
    """Events for one act (director stays within the current act this period)."""
    doc = load_tape(tape_path)
    acts = tape_turns(doc)
    if not acts:
        return []
    idx = max(0, min(int(turn_index), len(acts) - 1))
    return list(acts[idx].get("events") or [])


def _act_user_prompt(tape_path: Path, turn_index: int = 0) -> str:
    doc = load_tape(tape_path)
    acts = tape_turns(doc)
    if acts:
        idx = max(0, min(int(turn_index), len(acts) - 1))
        prompt = str(acts[idx].get("user_prompt") or "").strip()
        if prompt:
            return prompt
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    return str(meta.get("user_prompt") or "").strip()


def _duration_ms(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    return max(int(ev.get("t_ms") or 0) for ev in events)


def ensure_transport(conversation_id: str) -> PlaybackTransport:
    """Return a live transport, bootstrapping from the binding if needed."""
    require_replay_enabled()
    existing = transport_registry.get(conversation_id)
    if existing is not None:
        return existing
    binding = resolve_binding(conversation_id)
    if binding is None:
        raise NotFoundError("会话未绑定演示磁带或回放未开启")
    events = _load_act_events(binding.tape_path, binding.turn_index)
    return transport_registry.attach(
        conversation_id=conversation_id,
        tape_path=binding.tape_path,
        speed=binding.speed,
        max_gap_ms=binding.max_gap_ms,
        event_count=len(events),
        duration_ms=_duration_ms(events),
        tape_id=_tape_id_for(binding.tape_path),
    )


def chapters_for_conversation(conversation_id: str) -> list[TapeChapter]:
    transport = ensure_transport(conversation_id)
    binding = resolve_binding(conversation_id)
    turn_index = binding.turn_index if binding is not None else 0
    return build_chapters(_load_act_events(transport.tape_path, turn_index))


def status_for_conversation(conversation_id: str) -> dict[str, Any]:
    transport = ensure_transport(conversation_id)
    snap = transport.snapshot()
    binding = resolve_binding(conversation_id)
    turn_index = binding.turn_index if binding is not None else 0
    chapters = build_chapters(_load_act_events(transport.tape_path, turn_index))
    current_label = ""
    for ch in chapters:
        if ch.event_index <= transport.event_index:
            current_label = ch.label
        else:
            break
    snap["chapter_label"] = current_label
    snap["live"] = turn_runs.get(conversation_id) is not None
    return snap


def set_speed(conversation_id: str, speed: float) -> dict[str, Any]:
    transport = ensure_transport(conversation_id)
    if speed < 0.5 or speed > 8.0:
        raise ValidationError("倍速须在 0.5–8 之间")
    transport.set_speed(speed)
    logger.info(
        "demo_tape.director_speed",
        conversation_id=conversation_id,
        speed=transport.speed,
    )
    return status_for_conversation(conversation_id)


def pause(conversation_id: str) -> dict[str, Any]:
    transport = ensure_transport(conversation_id)
    transport.pause()
    logger.info("demo_tape.director_pause", conversation_id=conversation_id)
    return status_for_conversation(conversation_id)


def resume_soft(conversation_id: str) -> dict[str, Any]:
    """Resume director soft-pause only (does not settle a durable pause card)."""
    transport = ensure_transport(conversation_id)
    transport.resume()
    logger.info("demo_tape.director_resume", conversation_id=conversation_id)
    return status_for_conversation(conversation_id)


async def _auto_resume_durable_pause(
    *,
    conversation_id: str,
    user_id: str,
    llm_credentials: LLMCredentials | None,
    llm_supports_tools: bool | None,
    setup_sink: SinkSetup | None,
) -> bool:
    """If a durable tape pause is waiting, claim + continue with CONTINUE."""
    frames = await list_paused_turns(conversation_id)
    candidates = [f for f in frames if isinstance(f, _TAPE_DURABLE_KINDS)]
    if not candidates:
        return False

    frame = next((f for f in candidates if is_demo_tape_frame(f)), candidates[0])
    peeked = await load_paused_turn(frame.message_id, conversation_id=conversation_id)
    if peeked is None:
        return False

    if not await turn_runs.drain(conversation_id):
        raise ValidationError("会话仍有在途回合，无法代确认授权卡")

    await prewrite_cold_resume_settlement(
        peeked,
        decision=CheckpointDecision.CONTINUE.value,
        note="demo_tape.director_auto_resolve",
        selected=[],
    )
    suspension = await claim_paused_turn(
        frame.message_id,
        conversation_id=conversation_id,
        decision=CheckpointDecision.CONTINUE.value,
        settled_by="demo_tape_director",
    )
    if suspension is None:
        return False

    sink = EventSink()
    if setup_sink is not None:
        setup_sink(sink)
    task = asyncio.create_task(
        resume_chat(
            suspension=suspension,
            response=CheckpointResponse(
                decision=CheckpointDecision.CONTINUE,
                note="demo_tape.director_auto_resolve",
            ),
            sink=sink,
            llm_credentials=llm_credentials,
            llm_supports_tools=llm_supports_tools,
        )
    )
    turn_runs.register(
        conversation_id=conversation_id, task=task, sink=sink, user_id=user_id
    )
    logger.info(
        "demo_tape.director_auto_resume",
        conversation_id=conversation_id,
        message_id=frame.message_id,
        kind=getattr(frame, "kind", None),
        user_id=user_id,
    )
    return True


# Back-compat alias (tests / older call sites).
_auto_resume_team_preview = _auto_resume_durable_pause


def _auto_resolve_hot_approvals(conversation_id: str) -> bool:
    """Settle pending hot-path approvals so a seek-past can wake the player.

    Same decision semantics as burst auto-confirm: APPROVE, then the player
    re-emits ``approval_resolved`` and continues the recorded stream.
    """
    registry = default_interaction_registry()
    settled = False
    for req in list(registry.list_pending(conversation_id)):
        if req.kind is not InteractionKind.APPROVAL:
            continue
        if registry.resolve(
            req.id, ApprovalDecision.APPROVE, conversation_id=conversation_id
        ):
            settled = True
            logger.info(
                "demo_tape.director_auto_resolve_approval",
                conversation_id=conversation_id,
                approval_id=req.id,
            )
    return settled


async def _rewind_and_burst(
    *,
    conversation_id: str,
    target_index: int,
    user_id: str,
    llm_credentials: LLMCredentials | None,
    llm_supports_tools: bool | None,
    setup_sink: SinkSetup | None,
) -> dict[str, Any]:
    """Restart-style seek backward: clear transcript, replay with burst."""
    transport = ensure_transport(conversation_id)
    binding = resolve_binding(conversation_id)
    if binding is None:
        raise NotFoundError("会话未绑定演示磁带")

    transport.arm_burst(target_index, auto_resolve=True)
    transport.resume()

    await turn_runs.stop_and_drain(conversation_id, timeout=30.0)

    for frame in await list_paused_turns(conversation_id):
        mid = getattr(frame, "message_id", None)
        if mid:
            await delete_paused_turn(str(mid))

    async with async_session_factory() as wipe_session:
        msgs = await MessageRepository(wipe_session).list_all_for_conversation(
            conversation_id
        )
        for msg in list(msgs):
            await MessageRepository(wipe_session).delete_by_id(
                msg.id, conversation_id=conversation_id
            )

    user_prompt = _act_user_prompt(binding.tape_path, binding.turn_index) or (
        "（导演倒带）重开磁带回放"
    )

    events = _load_act_events(binding.tape_path, binding.turn_index)
    transport_registry.attach(
        conversation_id=conversation_id,
        tape_path=binding.tape_path,
        speed=transport.speed,
        max_gap_ms=binding.max_gap_ms,
        event_count=len(events),
        duration_ms=_duration_ms(events),
        tape_id=_tape_id_for(binding.tape_path),
    )
    armed = ensure_transport(conversation_id)
    armed.arm_burst(target_index, auto_resolve=True)
    armed.set_speed(transport.speed)

    sink = EventSink()
    if setup_sink is not None:
        setup_sink(sink)
    task = asyncio.create_task(
        stream_chat(
            conversation_id=conversation_id,
            user_message=user_prompt,
            user_id=user_id,
            sink=sink,
            attachments=[],
            llm_credentials=llm_credentials,
            llm_supports_tools=llm_supports_tools,
        )
    )
    turn_runs.register(
        conversation_id=conversation_id, task=task, sink=sink, user_id=user_id
    )
    logger.info(
        "demo_tape.director_rewind",
        conversation_id=conversation_id,
        target_index=target_index,
    )
    return status_for_conversation(conversation_id)


def _has_pause_before(
    events: list[dict[str, Any]], start: int, target: int
) -> bool:
    for i in range(max(0, start), min(target, len(events))):
        if event_type(events[i]) in TAPE_INTERACTIVE_PAUSE_KINDS:
            return True
    return False


async def seek(
    *,
    conversation_id: str,
    user_id: str,
    llm_credentials: LLMCredentials | None = None,
    llm_supports_tools: bool | None = None,
    setup_sink: SinkSetup | None = None,
    t_ms: int | None = None,
    event_index: int | None = None,
    chapter_id: str | None = None,
) -> dict[str, Any]:
    """Seek to a snapped event index (forward burst or restart rewind)."""
    transport = ensure_transport(conversation_id)
    binding = resolve_binding(conversation_id)
    turn_index = binding.turn_index if binding is not None else 0
    events = _load_act_events(transport.tape_path, turn_index)
    if not events:
        raise ValidationError("磁带无事件")

    if chapter_id:
        ch = chapter_by_id(build_chapters(events), chapter_id)
        if ch is None:
            raise NotFoundError(f"未知章节: {chapter_id}")
        target = ch.event_index
    elif event_index is not None:
        target = max(0, min(int(event_index), len(events) - 1))
    elif t_ms is not None:
        target = snap_to_event_index(events, int(t_ms))
    else:
        raise ValidationError("须提供 t_ms / event_index / chapter_id 之一")

    current = transport.event_index

    if target < current or (
        transport.state in (PlaybackState.FINISHED, PlaybackState.ERROR)
        and target < len(events) - 1
    ):
        return await _rewind_and_burst(
            conversation_id=conversation_id,
            target_index=target,
            user_id=user_id,
            llm_credentials=llm_credentials,
            llm_supports_tools=llm_supports_tools,
            setup_sink=setup_sink,
        )

    was_awaiting = transport.state == PlaybackState.AWAITING_INTERACTION
    will_cross_pause = _has_pause_before(events, current, target) or (
        was_awaiting and target > current
    )
    transport.arm_burst(target, auto_resolve=will_cross_pause)
    transport.resume()

    # Hot approval keeps the turn running — wake via registry. Cold durable
    # cards need claim+resume. Try hot first; fall through to cold.
    if was_awaiting and target > current and not _auto_resolve_hot_approvals(conversation_id):
        await _auto_resume_durable_pause(
            conversation_id=conversation_id,
            user_id=user_id,
            llm_credentials=llm_credentials,
            llm_supports_tools=llm_supports_tools,
            setup_sink=setup_sink,
        )

    logger.info(
        "demo_tape.director_seek",
        conversation_id=conversation_id,
        target_index=target,
        current_index=current,
        auto_resolve=transport.auto_resolve_pauses,
    )
    return status_for_conversation(conversation_id)


def list_sessions() -> list[dict[str, Any]]:
    require_replay_enabled()
    out: list[dict[str, Any]] = []
    for t in transport_registry.list_active():
        snap = t.snapshot()
        snap["live"] = turn_runs.get(t.conversation_id) is not None
        out.append(snap)
    return out
