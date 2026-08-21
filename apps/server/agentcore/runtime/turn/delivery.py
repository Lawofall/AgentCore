"""Process-local in-flight delivery (steer / queue).

HTTP ``POST …/messages`` and sidecar ``deliverMessage`` share this kernel so a
live occupant is never treated as an idle new turn. Idle open-turn still belongs
only to HTTP ``stream_chat`` — this module returns ``None`` when nothing is live.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from agentcore.core.types import new_id
from agentcore.runtime.events import EventSink, publish_conversation_signal, turn_queue_cancelled
from agentcore.runtime.turn.queue import QueuedTurn, new_queued_turn, turn_queue
from agentcore.runtime.turn.runs import turn_runs
from agentcore.workspace.attachments import interjection_attachment_meta

DeliveryMode = Literal["steer", "queue"]
DeliveryStatus = Literal["received", "queued"]

PersistAttachmentsFn = Callable[..., Awaitable[list[dict[str, Any]]]]


class DeliveryBlockedError(Exception):
    """Hot-path pending interaction blocks steer/queue (same as HTTP 409)."""

    def __init__(self, pending_kinds: list[str]) -> None:
        self.code = "pending_interactions_awaiting"
        self.pending_kinds = list(pending_kinds)
        super().__init__(self.code)


class NoLiveTurnError(Exception):
    """Sidecar ``deliverMessage`` when this process has no occupying run."""

    code = "no_live_turn"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(slots=True)
class InFlightDelivery:
    """Kernel result for an occupying run (never a new idle turn)."""

    status: DeliveryStatus
    conversation_id: str
    content: str
    interjection_id: str | None = None
    execution_id: str | None = None
    attachments_meta: list[dict[str, Any]] = field(default_factory=list)
    agent_mentions: list[dict[str, Any]] = field(default_factory=list)
    queue_id: str | None = None
    position: int | None = None
    queue_depth: int | None = None
    degraded_from: str | None = None
    started: asyncio.Future[Any] | None = None
    confirm_reason: str = "steer_confirm"


def pending_interaction_kinds(conversation_id: str) -> list[str]:
    """Hot pending kinds that block a new user message (HTTP 409 / RPC error)."""
    from agentcore.runtime.interaction import InteractionKind, default_interaction_registry

    hot = frozenset({InteractionKind.APPROVAL, InteractionKind.ESCALATION})
    pending = [
        r
        for r in default_interaction_registry().list_pending(conversation_id)
        if r.kind in hot
        and not (
            r.kind is InteractionKind.ESCALATION and (r.payload or {}).get("awaiting") == "ceo"
        )
    ]
    return sorted({r.kind.value for r in pending})


def raise_if_delivery_blocked(conversation_id: str) -> None:
    kinds = pending_interaction_kinds(conversation_id)
    if kinds:
        raise DeliveryBlockedError(kinds)


def delivery_json_ack(result: InFlightDelivery) -> dict[str, Any]:
    """Sidecar JSON-RPC ack (camelCase). Interjection is already on the live sink."""
    if result.status == "received":
        return {
            "status": "received",
            "interjectionId": result.interjection_id,
            "executionId": result.execution_id,
            "delivery": "steer",
        }
    ack: dict[str, Any] = {
        "status": "queued",
        "queueId": result.queue_id,
        "position": result.position,
        "queueDepth": result.queue_depth,
        "delivery": "queue",
    }
    if result.degraded_from:
        ack["degradedFrom"] = result.degraded_from
    return ack


def queued_turns_json(conversation_id: str) -> list[dict[str, Any]]:
    """``listQueuedTurns`` wire items (camelCase; FIFO, 1-based position)."""
    items: list[dict[str, Any]] = []
    for idx, item in enumerate(turn_queue.list_pending(conversation_id), start=1):
        row: dict[str, Any] = {
            "queueId": item.queue_id,
            "content": item.content,
            "position": idx,
            "interjectionId": item.interjection_id,
            "attachments": list(item.attachments),
            "agentMentions": list(item.agent_mentions),
        }
        items.append(row)
    return items


def list_queued_items(conversation_id: str) -> list[QueuedTurn]:
    return turn_queue.list_pending(conversation_id)


def cancel_queued_item(conversation_id: str, queue_id: str) -> QueuedTurn | None:
    """Withdraw one FIFO item; emit ``turn_queue_cancelled`` on the live sink.

    Stop does not clear the queue. Missing / already started → ``None``.
    """
    item = turn_queue.cancel(conversation_id, queue_id)
    if item is None:
        return None
    fut = item.started
    if fut is not None and not fut.done():
        fut.cancel()
    event = turn_queue_cancelled(queue_id=queue_id, conversation_id=conversation_id)
    run = turn_runs.get(conversation_id)
    live_sink = run.sink if run is not None and not run.task.done() else None
    if live_sink is not None:
        live_sink.emit(event)
    publish_conversation_signal(conversation_id, event, already_on_sink=live_sink)
    return item


def _emit_received_once(
    sink: EventSink,
    *,
    interjection_id: str,
    execution_id: str,
    content: str,
    attachments_meta: list[dict[str, Any]],
    agent_mentions: list[dict[str, Any]],
) -> None:
    from agentcore.runtime.events import user_interjection

    sink.emit(
        user_interjection(
            interjection_id=interjection_id,
            execution_id=execution_id,
            content=content,
            status="received",
            attachments=attachments_meta or None,
            agent_mentions=agent_mentions or None,
        )
    )


async def deliver_in_flight(
    *,
    conversation_id: str,
    content: str,
    delivery: DeliveryMode,
    user_id: str,
    attachments: list[dict[str, Any]] | None = None,
    agent_mentions: list[dict[str, Any]] | None = None,
    requires_tools: bool = False,
    x_client_platform: str | None = None,
    origin_device_id: str | None = None,
    llm_credentials: Any = None,
    llm_supports_tools: bool | None = None,
    persist_attachments_fn: PersistAttachmentsFn | None = None,
    wait_for_start: bool = False,
    require_live: bool = False,
    user_message_id: str | None = None,
    message_id: str | None = None,
    trace_id: str | None = None,
) -> InFlightDelivery | None:
    """Steer or enqueue against the occupying run.

    Returns ``None`` when the slot is idle (HTTP then opens a new turn). Sidecar
    passes ``require_live=True`` so idle raises :class:`NoLiveTurnError` instead of
    succeeding or implying a cloud POST.

    Interjection is emitted **once** on the live sink (sidecar pump already
    forwards ``turn/event``). Callers that need a short HTTP confirm stream build
    their own sse-only sink from the returned ids.
    """
    existing = turn_runs.get(conversation_id)
    if existing is None or existing.task.done():
        if require_live:
            raise NoLiveTurnError()
        return None

    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
        active_coordination_for_conversation,
    )
    from agentcore.runtime.turn.steer import try_enqueue as try_enqueue_steer

    raw_attachments = list(attachments or [])
    raw_agent_mentions = list(agent_mentions or [])
    coord = active_coordination_for_conversation(conversation_id)
    coord_active = coord is not None and coord.active
    try_interject = delivery == "steer" and coord_active
    degraded_from: str | None = None

    if try_interject:
        assert coord is not None
        interjection_id = new_id()
        persisted = raw_attachments
        if persist_attachments_fn is not None and raw_attachments:
            persisted = await persist_attachments_fn(
                conversation_id=conversation_id,
                user_id=user_id,
                attachments=raw_attachments,
                sink=existing.sink,
            )
        att_meta = interjection_attachment_meta(persisted)
        coord.stash_interjection(
            interjection_id,
            {
                "content": content,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "attachments": persisted,
                "agent_mentions": raw_agent_mentions,
                "requires_tools": requires_tools,
                "x_client_platform": x_client_platform,
                "origin_device_id": origin_device_id,
                "llm_credentials": llm_credentials,
                "llm_supports_tools": llm_supports_tools,
                "user_message_id": user_message_id,
                "message_id": message_id,
                "trace_id": trace_id,
            },
        )
        posted = coord.post(
            CoordinationEvent(
                kind=CoordinationEventKind.USER_INTERJECTION,
                payload={
                    "interjection_id": interjection_id,
                    "content": content,
                    **({"attachments": att_meta} if att_meta else {}),
                    **({"agent_mentions": raw_agent_mentions} if raw_agent_mentions else {}),
                },
            )
        )
        if not posted:
            coord.take_interjection(interjection_id)
        else:
            _emit_received_once(
                existing.sink,
                interjection_id=interjection_id,
                execution_id=coord.execution_id,
                content=content,
                attachments_meta=att_meta,
                agent_mentions=raw_agent_mentions,
            )
            return InFlightDelivery(
                status="received",
                conversation_id=conversation_id,
                content=content,
                interjection_id=interjection_id,
                execution_id=coord.execution_id,
                attachments_meta=att_meta,
                agent_mentions=raw_agent_mentions,
                confirm_reason="interjection_confirm",
            )

    elif delivery == "steer":
        parked = try_enqueue_steer(
            conversation_id=conversation_id,
            content=content,
            user_id=user_id,
            attachments=raw_attachments,
            agent_mentions=raw_agent_mentions,
            requires_tools=requires_tools,
            x_client_platform=x_client_platform,
            origin_device_id=origin_device_id,
            llm_credentials=llm_credentials,
            llm_supports_tools=llm_supports_tools,
            user_message_id=user_message_id,
            message_id=message_id,
            trace_id=trace_id,
        )
        if parked is not None:
            att_meta = interjection_attachment_meta(parked.attachments)
            _emit_received_once(
                existing.sink,
                interjection_id=parked.interjection_id,
                execution_id=parked.execution_id,
                content=content,
                attachments_meta=att_meta,
                agent_mentions=raw_agent_mentions,
            )
            return InFlightDelivery(
                status="received",
                conversation_id=conversation_id,
                content=content,
                interjection_id=parked.interjection_id,
                execution_id=parked.execution_id,
                attachments_meta=att_meta,
                agent_mentions=raw_agent_mentions,
                confirm_reason="steer_confirm",
            )
        degraded_from = "steer"

    started: asyncio.Future[Any] | None = None
    if wait_for_start:
        started = asyncio.get_running_loop().create_future()
    status = turn_queue.enqueue_and_ensure_drain(
        conversation_id,
        new_queued_turn(
            content=content,
            user_id=user_id,
            attachments=raw_attachments,
            agent_mentions=raw_agent_mentions,
            requires_tools=requires_tools,
            x_client_platform=x_client_platform,
            origin_device_id=origin_device_id,
            llm_credentials=llm_credentials,
            llm_supports_tools=llm_supports_tools,
            started=started,
            user_message_id=user_message_id,
            message_id=message_id,
            trace_id=trace_id,
        ),
    )
    return InFlightDelivery(
        status="queued",
        conversation_id=conversation_id,
        content=content,
        queue_id=status.queue_id,
        position=status.position,
        queue_depth=status.queue_depth,
        degraded_from=degraded_from,
        started=started,
    )
