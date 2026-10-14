"""Bounded subscriber queues for EventSink. Settle frames stay; fluency sheds."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from agentcore.core.logging import get_logger
from agentcore.runtime.events.types import SSEEvent
from agentcore.runtime.terminal import is_live_settle_event

logger = get_logger(__name__)

# One观察端's live queue is capped here; a端 too slow to drain sheds its OLDEST
# *process* frame instead of growing without bound or stalling ``emit``. Settle
# frames (run close / message_end / error / execution_completed) stay unless the
# queue is already all settle frames. Sized like the IM firehose
# (``messaging/hub.py``) — a stuck client loses smoothness; close facts ride live.
_SUBSCRIBER_QUEUE_MAXSIZE = 1000


@dataclass(slots=True)
class _Frame:
    """One fanned-out event plus the emit-side task that backfills its journal ``seq``."""

    event: SSEEvent
    seq_ready: asyncio.Task[None] | None = None


async def _backfill_seq(event: SSEEvent, barrier: asyncio.Future[int | None]) -> None:
    """Write the allocated journal ``seq`` onto ``event`` once its write lands.

    Emit-side by construction (云对话多端同权 B2 · §6.1): ``seq`` used to ride a second
    queue that only paired up because ONE consumer dequeued events and barriers in
    lockstep. With N subscriber queues that invariant is gone —串号 would follow — so
    the barrier now resolves once per event, into the event object itself, and每个
    subscriber只是等它落定 (:meth:`SinkSubscription.get`).

    Never raises: a failed / cancelled journal write only means the frame ships without
    an ``id:`` line, which must not kill anybody's stream.
    """
    try:
        allocated = await barrier
    except asyncio.CancelledError:
        return
    except Exception as e:  # noqa: BLE001 — seq is best-effort transport metadata
        logger.warning("event_sink.seq_backfill_failed", type=event.type.value, error=str(e))
        return
    if allocated is not None:
        event.seq = allocated


def _bounded_put[T](
    queue: asyncio.Queue[T],
    item: T,
    *,
    keep: Callable[[T], bool],
) -> bool:
    """Enqueue ``item``. If full, shed the oldest non-keep item.

    Returns True if nothing was shed. False if a frame was discarded: oldest
    fluency, incoming fluency refused (cannot evict a keep frame), or
    pathological oldest keep when the queue is already all keepers.
    """
    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        pass
    drained: list[T] = []
    while True:
        try:
            drained.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    if not drained:
        queue.put_nowait(item)
        return True
    victim = next((i for i, existing in enumerate(drained) if not keep(existing)), None)
    if victim is not None:
        drained.pop(victim)
        drained.append(item)
    elif keep(item):
        drained.pop(0)
        drained.append(item)
    for existing in drained:
        queue.put_nowait(existing)
    return False


def _keep_live_settle_frame(item: _Frame | None) -> bool:
    if item is None:
        return True
    return is_live_settle_event(item.event.type)


def _keep_live_settle_event(item: SSEEvent | None) -> bool:
    if item is None:
        return True
    return is_live_settle_event(item.type)
