"""Conversation-level live subscription (云对话多端同权 B2 · P0-b).

An观察端 follows a **conversation**, not a single turn. Before this, ``GET
…/conversations/{id}/stream`` bound its lifetime to whatever run happened to be live:
idle conversation → ``204``, and every later run (FIFO drain / cold-resume wake /
plain send) opened a brand-new sink that the parked端 had no way to hear
about. A second device could only carry on by luck.

Here a端 registers a :class:`ConversationWatcher` once and is handed each new run's
:class:`~agentcore.runtime.events.sink.EventSink` as it registers in the
``TurnRunRegistry`` — the one funnel every turn start already goes through.

A watcher also carries a second, run-independent lane: **conversation signals**
(:meth:`ConversationStreamHub.publish_signal`). Queue-class短暂态 —
``turn_queued`` / ``turn_queue_cancelled`` / ``resume_deferred`` — belong to the
*conversation*, not to any one turn: they happen while another turn holds the slot, or
in the idle gap where no sink exists at all. Riding a run sink can therefore only reach
whoever happens to be tailing that run, which is why they used to be visible on the
发起端 alone. The signal lane reaches every端 following the conversation in either
phase, so 验收 5 (短暂态一致) holds without a new event type or a durable queue —
content authority stays ``GET …/queued-turns``.

Process-local, same single-worker posture as the turn registry and the IM chat hub.
Cross-worker fan-out swaps in behind this seam later: a run start is a *signal*, and
the端 catches up on facts from the journal, so best-effort delivery is enough.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque

from agentcore.core.logging import get_logger
from agentcore.observability.stream_timing import (
    current_http_req_id,
    elapsed_ms,
    mono_now,
    wall_now_iso,
)
from agentcore.runtime.events.sink import EventSink
from agentcore.runtime.events.types import SSEEvent

logger = get_logger(__name__)

# A watcher only ever needs the runs it has not consumed yet, and turns are serialized
# per conversation — a backlog this deep already means the端 is not draining. Drop the
# OLDEST then: a stale run start is worthless (that turn is over), the newest is not.
_RUN_QUEUE_MAXSIZE = 8

# Signals are EPHEMERAL「变了」pings whose content authority is a REST read, so a端 far
# enough behind to fill this has already lost nothing that a refresh won't restore.
# Bounded + drop-oldest per 云对话多端同权 B2 §6.1: one slow端 must never stall emit.
_SIGNAL_QUEUE_MAXSIZE = 32


class ConversationWatcher:
    """One端 parked on a conversation, waiting for whatever runs next."""

    __slots__ = (
        "_last_byte_mono",
        "_ready",
        "_runs",
        "_signals",
        "_started_at",
        "_started_mono",
        "_tailed_sink",
        "conversation_id",
    )

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self._runs: asyncio.Queue[EventSink] = asyncio.Queue(maxsize=_RUN_QUEUE_MAXSIZE)
        self._signals: deque[SSEEvent] = deque(maxlen=_SIGNAL_QUEUE_MAXSIZE)
        self._ready = asyncio.Event()
        self._tailed_sink: EventSink | None = None
        now = mono_now()
        self._started_mono = now
        self._last_byte_mono = now
        self._started_at = wall_now_iso()

    def note_byte(self) -> None:
        """Mark that this connection just sent a frame or ``: ping`` heartbeat."""
        self._last_byte_mono = mono_now()

    def stream_timing(self) -> tuple[str, int, int]:
        """``started_at``, age since watch, idle since last byte (ms)."""
        now = mono_now()
        return (
            self._started_at,
            elapsed_ms(self._started_mono, now_mono=now),
            elapsed_ms(self._last_byte_mono, now_mono=now),
        )

    def _offer(self, sink: EventSink) -> bool:
        """Hand a newly started run to this watcher; drop the oldest when full."""
        try:
            self._runs.put_nowait(sink)
            return True
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._runs.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._runs.put_nowait(sink)
            return False

    async def next_run(self) -> EventSink:
        """Await the conversation's next turn sink (idle端 just waits here)."""
        return await self._runs.get()

    @property
    def tailed_sink(self) -> EventSink | None:
        """The run sink this端 is subscribed to right now (``None`` between runs)."""
        return self._tailed_sink

    def mark_tailing(self, sink: EventSink | None) -> None:
        """Record / clear the run this端 is tailing — the fan-out de-dup key.

        Must be set in the same synchronous step as ``sink.subscribe`` (and cleared with
        ``unsubscribe``): :meth:`ConversationStreamHub.publish_signal` reads it to decide
        whether this connection is already getting the frame off that run's sink.
        """
        self._tailed_sink = sink

    def _offer_signal(self, event: SSEEvent) -> bool:
        """Queue one conversation signal; ``False`` → the oldest was shed to fit."""
        shed = len(self._signals) == _SIGNAL_QUEUE_MAXSIZE
        self._signals.append(event)
        self._ready.set()
        return not shed

    async def wait_signals(self) -> None:
        """Block until at least one signal is pending (cancel-safe — nothing dequeues)."""
        await self._ready.wait()

    def drain_signals(self) -> list[SSEEvent]:
        """Take every pending signal in arrival order (empty when there is none)."""
        if not self._signals:
            self._ready.clear()
            return []
        pending = list(self._signals)
        self._signals.clear()
        self._ready.clear()
        return pending


class ConversationStreamHub:
    """Process-wide registry of端 parked on conversations."""

    def __init__(self) -> None:
        self._watchers: dict[str, set[ConversationWatcher]] = {}

    def watch(
        self,
        conversation_id: str,
        *,
        message_id: str | None = None,
    ) -> ConversationWatcher:
        """Register a端 following ``conversation_id`` (N per conversation, all equal).

        Follow-only (``GET …/stream?follow=true``). Round-level attach never
        calls this — that path logs ``event_sink.attach`` with ``mode=attach``.
        """
        watcher = ConversationWatcher(conversation_id)
        self._watchers.setdefault(conversation_id, set()).add(watcher)
        logger.info(
            "conversation_stream.watch",
            conversation_id=conversation_id,
            message_id=message_id,
            watchers=len(self._watchers[conversation_id]),
            started_at=watcher._started_at,
            mode="follow",
            http_req_id=current_http_req_id(),
        )
        return watcher

    def unwatch(self, watcher: ConversationWatcher) -> None:
        """Deregister one端 (disconnect); the others keep following."""
        watchers = self._watchers.get(watcher.conversation_id)
        if watchers is None:
            return
        watchers.discard(watcher)
        if not watchers:
            self._watchers.pop(watcher.conversation_id, None)
        started_at, duration_ms, idle_ms = watcher.stream_timing()
        logger.info(
            "conversation_stream.unwatch",
            conversation_id=watcher.conversation_id,
            started_at=started_at,
            duration_ms=duration_ms,
            idle_ms=idle_ms,
            mode="follow",
            http_req_id=current_http_req_id(),
        )

    def watcher_count(self, conversation_id: str) -> int:
        """How many端 are currently following ``conversation_id``."""
        return len(self._watchers.get(conversation_id, ()))

    def publish_run(self, conversation_id: str, sink: EventSink) -> int:
        """Hand a starting run's sink to every parked端. Returns how many were notified.

        Synchronous (no await points) so the watcher set cannot change mid fan-out, and
        so a turn registering is never delayed by a slow观察端.
        """
        watchers = tuple(self._watchers.get(conversation_id, ()))
        if not watchers:
            return 0
        for watcher in watchers:
            if not watcher._offer(sink):
                logger.warning(
                    "conversation_stream.run_backlog_drop",
                    conversation_id=conversation_id,
                    message_id=sink.message_id,
                )
        logger.info(
            "conversation_stream.run_published",
            conversation_id=conversation_id,
            message_id=sink.message_id,
            watchers=len(watchers),
        )
        return len(watchers)

    def publish_signal(
        self,
        conversation_id: str,
        event: SSEEvent,
        *,
        already_on_sink: EventSink | None = None,
    ) -> int:
        """Fan one conversation-scoped signal to每个 parked端. Returns how many got it.

        ``already_on_sink`` is the run sink the caller ALSO emitted this very event on
        (the paths whose only观察端 may be a plain turn stream, e.g. 经典 steer 收口回落). A端
        tailing that sink receives the frame there, so it is skipped here — otherwise
        one connection would fold the same frame twice.

        Synchronous like :meth:`publish_run`: the watcher set cannot change mid fan-out
        and a slow观察端 never delays the action that produced the signal.
        """
        watchers = tuple(self._watchers.get(conversation_id, ()))
        if not watchers:
            return 0
        delivered = 0
        for watcher in watchers:
            if already_on_sink is not None and watcher.tailed_sink is already_on_sink:
                continue
            delivered += 1
            if not watcher._offer_signal(event):
                logger.warning(
                    "conversation_stream.signal_backlog_drop",
                    conversation_id=conversation_id,
                    type=event.type.value,
                )
        logger.info(
            "conversation_stream.signal_published",
            conversation_id=conversation_id,
            type=event.type.value,
            watchers=len(watchers),
            delivered=delivered,
        )
        return delivered


# Module-level singleton (single-worker posture, as ``turn_runs`` / ``turn_queue``).
conversation_streams = ConversationStreamHub()


def publish_conversation_signal(
    conversation_id: str,
    event: SSEEvent,
    *,
    already_on_sink: EventSink | None = None,
) -> int:
    """Best-effort :meth:`ConversationStreamHub.publish_signal` on the process singleton.

    Swallows failures: a观察端 must never break the queue mutation / route that produced
    the signal (same posture as ``turn_runs.register``'s publish).
    """
    try:
        return conversation_streams.publish_signal(
            conversation_id, event, already_on_sink=already_on_sink
        )
    except Exception:  # noqa: BLE001 — 观察端 must never break the producer
        logger.exception(
            "conversation_stream.signal_publish_failed",
            conversation_id=conversation_id,
            type=event.type.value,
        )
        return 0
