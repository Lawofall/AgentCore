"""Conversation-level turn queue (同会话并行发消息 → 显式串行).

When a turn is already in-flight for a conversation, subsequent ``POST …/messages``
requests enqueue here instead of overlapping ``turn_runs`` slots / dual sinks.
The active turn's done-callback drains the queue FIFO and starts the next turn —
unless a cold-resume deferred waiter owns the next slot (see ``turn.runs``;
deferred finishes first, then FIFO).

发送即有流 (D9): the enqueueing POST keeps an SSE open — it immediately emits
``turn_queued``, then when drain starts that entry the **same connection** becomes
the primary observer of the new turn's sink (reuse attach / detach policy). Drain
emits ``turn_queue_started`` as that sink's first frame (before ``message_start``).
If the waiting client disconnects mid-queue, the turn still starts detached (existing
attach/recovery path); no new mechanism.

Process-local deque (same posture as :mod:`.runs`), mirrored to
``turn_queue_items`` when a store is installed. A restart reloads unstarted
items in order and resolves credentials again; it does not keep the enqueue-time
key. A lease still held by crash recovery blocks that drain.

多端 (云对话多端同权 B2 · 验收 5): the enqueueing POST is not the only观察端 any more —
every enqueue also signals ``turn_queued`` to端 following the conversation (see
:func:`broadcast_turn_queued`), a「变了」ping for whoever is watching this turn.

The *content* goes somewhere else: every mutation pushes that conversation's
queue to the user's online devices over their fulfill channel
(:func:`~agentcore.fulfill.user_signal.push_queue_snapshot`). Connect seed is
one account-level :func:`~agentcore.fulfill.user_signal.queue_account_snapshot_frame`
(empty table included). A queue belongs to the account, and its holder is
usually looking at another conversation — or at another machine — so the
display stream cannot be where it is learned. That push is why there is no
client-side queue reconciliation any more: the snapshot lands on its own,
positions already renumbered. Unstarted items also sit in ``turn_queue_items``
so a process restart can reload them.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agentcore.core.logging import get_logger
from agentcore.core.types import new_id

_TRACE_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")

logger = get_logger(__name__)

QueueStarter = Callable[[str, "QueuedTurn"], Awaitable[None]]
# Sidecar installs a local pipeline+outbox starter. Default remains TurnDriver.start_turn.
_queue_starter: QueueStarter | None = None


def set_queue_starter(starter: QueueStarter | None) -> None:
    """Install the FIFO drain starter for this process (sidecar live → local pipeline)."""
    global _queue_starter
    _queue_starter = starter


def reset_queue_starter() -> None:
    """Restore the default TurnDriver drain (tests / sidecar shutdown)."""
    set_queue_starter(None)


@dataclass(slots=True)
class QueuedTurn:
    """One user message waiting for the conversation's in-flight turn to finish."""

    queue_id: str
    content: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    agent_mentions: list[dict[str, Any]] = field(default_factory=list)
    table_selection: list[str] = field(default_factory=list)
    requires_tools: bool = False
    x_client_platform: str | None = None
    # Which device sent this message. Captured at enqueue because the drain runs
    # from the *previous* turn's done-callback: without the snapshot the queued
    # turn would inherit that turn's device (see fulfill/origin.py).
    origin_device_id: str | None = None
    user_id: str = ""
    # Live drain uses the enqueue-time snapshot. A restored item leaves this empty
    # and sets ``credentials_pending`` so start resolves the current key instead.
    llm_credentials: Any = None
    llm_supports_tools: bool | None = None
    # Set when a classic steer that missed the next step was promoted onto the
    # FIFO. Plain ``delivery=queue`` leaves it None. Coordination interjections
    # no longer write this field; older rows may still carry it.
    interjection_id: str | None = None
    # Set by the enqueueing SSE when it opens: drain resolves with the live turn sink
    # so the waiting connection can continue on the same stream. None → no waiter
    # (tests / detached-only start) → sink starts detached as before.
    started: asyncio.Future[Any] | None = field(default=None, repr=False)
    # Desktop-minted identity for sidecar FIFO drain (cloud HTTP leaves these None).
    user_message_id: str | None = None
    message_id: str | None = None
    trace_id: str | None = None
    # Set only when the item was reloaded after a restart. Drain re-resolves
    # credentials and must win the durable-row claim before starting.
    credentials_pending: bool = False
    durable_claim: bool = False


@dataclass(frozen=True, slots=True)
class QueueStatus:
    """Visible queue state mirrored on the ``turn_queued`` SSE payload."""

    queue_id: str
    position: int  # 1-based index in the conversation queue
    queue_depth: int  # total pending after this enqueue


def broadcast_turn_queued(
    *,
    conversation_id: str,
    queue_id: str,
    position: int,
    queue_depth: int,
    degraded_from: str | None = None,
    on_live_sink: bool = False,
) -> bool:
    """Signal「队列变了」to every端 following this conversation. Returns whether the
    live turn sink also carried it.

    Two legs, deliberately not the same one (云对话多端同权 B2 · 验收 5):

    - **conversation signal lane** — always. It is the only path that reaches a端
      parked on an idle conversation, and the only one that survives「入队时宿主刚好
      收口了」(no sink to emit on at all).
    - **live turn sink** (``on_live_sink=True``) — for enqueues whose originating
      request has NO stream of its own to say it on: a classic steer leftover
      promoted at turn close happens after that POST returned its short confirm,
      so the发起端 can only learn about it through the turn it is currently watching. Classic FIFO
      passes ``False``: its own queued POST already yields the frame, and re-emitting on
      the live sink would show it twice on the same device (that device is usually
      watching the very turn it queued behind).

    A端 that is tailing the sink we just emitted on is skipped on the signal lane, so no
    connection folds the frame twice either way.
    """
    from agentcore.runtime.events import publish_conversation_signal, turn_queued

    from .runs import turn_runs

    event = turn_queued(
        queue_id=queue_id,
        position=position,
        queue_depth=queue_depth,
        conversation_id=conversation_id,
        degraded_from=degraded_from,
    )
    live_sink = None
    if on_live_sink:
        run = turn_runs.get(conversation_id)
        if run is not None and not run.task.done():
            live_sink = run.sink
            live_sink.emit(event)
    publish_conversation_signal(conversation_id, event, already_on_sink=live_sink)
    return live_sink is not None


class TurnQueue:
    """FIFO pending turns keyed by ``conversation_id``."""

    def __init__(self) -> None:
        self._queues: dict[str, deque[QueuedTurn]] = defaultdict(deque)
        self._drain_scheduled: set[str] = set()
        # Edit, cancel, and drain share this so a save cannot commit the user row
        # while pop_next is copying the previous text into the next turn.
        self._mutation_locks: dict[str, asyncio.Lock] = {}

    def enqueue(self, conversation_id: str, item: QueuedTurn) -> QueueStatus:
        q = self._queues[conversation_id]
        q.append(item)
        depth = len(q)
        logger.info(
            "turn_queue.enqueued",
            conversation_id=conversation_id,
            queue_id=item.queue_id,
            position=depth,
            queue_depth=depth,
        )
        self.push_snapshot(conversation_id, user_id=item.user_id)
        return QueueStatus(queue_id=item.queue_id, position=depth, queue_depth=depth)

    def push_snapshot(self, conversation_id: str, *, user_id: str) -> None:
        """Send the queue's current content to this user's online devices.

        Called after every change, emptying included —「队里已经没有了」is a fact a
        client cannot infer from silence. ``user_id`` comes from the item that was
        just added or removed: an empty queue no longer knows whose it was.
        """
        from agentcore.fulfill.user_signal import push_queue_snapshot

        push_queue_snapshot(
            user_id=user_id,
            conversation_id=conversation_id,
            items=self._items_of(conversation_id),
        )

    def account_snapshot_frame(self, user_id: str) -> dict[str, Any]:
        """One connect-time fulfill frame for every non-empty queue this user owns.

        Always a single ``turn_queue_account_snapshot`` — including ``queues: []``.
        A device that was offline while the queue changed has no other way to
        learn it; the client replaces its cloud table from this frame. Ownership
        is per item, so one process serving many accounts never hands one user's
        queue to another's device. Incremental mutations still push per-
        conversation ``turn_queue_snapshot``.
        """
        from agentcore.fulfill.user_signal import queue_account_snapshot_frame

        queues: list[dict[str, Any]] = []
        for conversation_id, q in self._queues.items():
            if not q or q[0].user_id != user_id:
                continue
            queues.append(
                {
                    "conversation_id": conversation_id,
                    "items": self._items_of(conversation_id),
                }
            )
        return queue_account_snapshot_frame(queues)

    def _items_of(self, conversation_id: str) -> list[dict[str, Any]]:
        """Pending entries as wire dicts, FIFO, 1-based ``position`` recomputed.

        Same fields as ``GET …/queued-turns`` ``QueuedTurnItem`` so a fulfill
        replace can show attachments / ``@`` chips without a second GET.
        Empty ``attachments`` / ``agent_mentions`` are omitted
        (GET empty lists are equivalent on the client).
        """
        rows: list[dict[str, Any]] = []
        for idx, item in enumerate(self.list_pending(conversation_id), start=1):
            row: dict[str, Any] = {
                "queue_id": item.queue_id,
                "content": item.content,
                "position": idx,
                "interjection_id": item.interjection_id,
            }
            if item.user_message_id:
                row["user_message_id"] = item.user_message_id
            if item.attachments:
                row["attachments"] = item.attachments
            if item.agent_mentions:
                row["agent_mentions"] = item.agent_mentions
            rows.append(row)
        return rows

    def enqueue_and_ensure_drain(
        self,
        conversation_id: str,
        item: QueuedTurn,
        *,
        on_live_sink: bool = False,
        degraded_from: str | None = None,
        signal_watchers: bool = True,
    ) -> QueueStatus:
        """Enqueue, then close the「宿主已结束、drain 已 no-op」race window.

        The send route may await between its in-flight check and this enqueue (e.g.
        协调 fall-through 的附件落盘). If the host turn finished inside that window, its
        done-callback ran ``schedule_drain`` against a then-empty queue and disarmed —
        nobody would ever start this item (排队项搁浅、等待端卡 await). Re-checking the
        slot AFTER the append closes the window: either a turn is still live (its
        done-callback will drain), or the slot is free/finished and we arm the drain
        ourselves. ``schedule_drain`` is idempotent and ``_drain`` re-checks the slot,
        so double-arming is harmless.

        Every enqueue signals ``turn_queued`` to the conversation's观察端 (see
        :func:`broadcast_turn_queued` for why the live-sink leg is opt-in via
        ``on_live_sink``). ``signal_watchers=False`` is for the one caller that must
        order the signal itself: 经典 steer 收口 leftover sends ``user_interjection
        (queued)`` first and only then the degraded ``turn_queued`` (双发次序是契约,
        见 conformance ``single_agent_user_interjection_steer_queued``).
        """
        status = self.enqueue(conversation_id, item)
        if signal_watchers:
            reached_live_sink = broadcast_turn_queued(
                conversation_id=conversation_id,
                queue_id=status.queue_id,
                position=status.position,
                queue_depth=status.queue_depth,
                degraded_from=degraded_from,
                on_live_sink=on_live_sink,
            )
            if on_live_sink and not reached_live_sink:
                logger.info(
                    "turn_queue.enqueued_no_live_sink",
                    conversation_id=conversation_id,
                    queue_id=status.queue_id,
                    position=status.position,
                    queue_depth=status.queue_depth,
                    degraded_from=degraded_from,
                )
        from .runs import turn_runs

        existing = turn_runs.get(conversation_id)
        if existing is None or existing.task.done():
            self.schedule_drain(conversation_id)
        from agentcore.runtime.turn.durable import schedule_turn_queue_mirror

        schedule_turn_queue_mirror(conversation_id, item.queue_id)
        return status

    def depth(self, conversation_id: str) -> int:
        return len(self._queues.get(conversation_id) or ())

    def list_pending(self, conversation_id: str) -> list[QueuedTurn]:
        """FIFO snapshot of pending turns currently in this process."""
        q = self._queues.get(conversation_id)
        if not q:
            return []
        return list(q)

    def clear(self, conversation_id: str) -> int:
        """Drop all pending turns (e.g. conversation deleted). Returns count dropped.

        Not a Stop side-effect — ``POST …/stop`` must leave queued items intact.
        """
        q = self._queues.pop(conversation_id, None)
        self._drain_scheduled.discard(conversation_id)
        n = len(q) if q else 0
        if n:
            logger.info(
                "turn_queue.cleared",
                conversation_id=conversation_id,
                dropped=n,
            )
            assert q is not None
            self.push_snapshot(conversation_id, user_id=q[0].user_id)
        return n

    def cancel(self, conversation_id: str, queue_id: str) -> QueuedTurn | None:
        """Remove one pending turn by ``queue_id`` before drain. Returns the item or None.

        Already-started / unknown id → None (route maps to 404). Does not affect
        the in-flight turn or other queue entries.
        """
        q = self._queues.get(conversation_id)
        if not q:
            return None
        for idx, item in enumerate(q):
            if item.queue_id != queue_id:
                continue
            del q[idx]
            if not q:
                self._queues.pop(conversation_id, None)
            logger.info(
                "turn_queue.cancelled",
                conversation_id=conversation_id,
                queue_id=queue_id,
                remaining=len(q),
            )
            self.push_snapshot(conversation_id, user_id=item.user_id)
            return item
        return None

    def reorder(self, conversation_id: str, queue_ids: list[str]) -> bool:
        """Permute the pending FIFO. The id list must be exactly the current set.

        Same order is a no-op success. A partial list, duplicate, or unknown id
        leaves the queue untouched and returns False.
        """
        q = self._queues.get(conversation_id)
        if not q:
            return False
        current = [item.queue_id for item in q]
        if len(queue_ids) != len(current) or len(set(queue_ids)) != len(queue_ids):
            return False
        if set(queue_ids) != set(current):
            return False
        if queue_ids == current:
            return True
        by_id = {item.queue_id: item for item in q}
        ordered = deque(by_id[qid] for qid in queue_ids)
        self._queues[conversation_id] = ordered
        logger.info(
            "turn_queue.reordered",
            conversation_id=conversation_id,
            queue_depth=len(ordered),
        )
        self.push_snapshot(conversation_id, user_id=ordered[0].user_id)
        return True

    def mutation_lock(self, conversation_id: str) -> asyncio.Lock:
        """Per-conversation lock for edit, withdraw, and drain pop."""
        lock = self._mutation_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._mutation_locks[conversation_id] = lock
        return lock

    def find_pending(self, conversation_id: str, queue_id: str) -> QueuedTurn | None:
        q = self._queues.get(conversation_id)
        if not q:
            return None
        for item in q:
            if item.queue_id == queue_id:
                return item
        return None

    def apply_edit(
        self,
        conversation_id: str,
        queue_id: str,
        *,
        content: str,
        attachments: list[dict[str, Any]],
        agent_mentions: list[dict[str, Any]],
    ) -> QueuedTurn | None:
        """Replace one pending item's payload. Identity, order, and credentials stay.

        Missing / already popped → None. Caller holds ``mutation_lock``.
        """
        item = self.find_pending(conversation_id, queue_id)
        if item is None:
            return None
        item.content = content
        item.attachments = list(attachments)
        item.agent_mentions = list(agent_mentions)
        logger.info(
            "turn_queue.edited",
            conversation_id=conversation_id,
            queue_id=queue_id,
            attachment_count=len(item.attachments),
            mention_count=len(item.agent_mentions),
        )
        self.push_snapshot(conversation_id, user_id=item.user_id)
        return item

    def move_to_front(self, conversation_id: str, queue_id: str) -> QueuedTurn | None:
        """Place one pending item at position 1. Missing id → None."""
        q = self._queues.get(conversation_id)
        if not q:
            return None
        ids = [item.queue_id for item in q]
        if queue_id not in ids:
            return None
        if ids[0] == queue_id:
            return q[0]
        ordered = [queue_id, *[qid for qid in ids if qid != queue_id]]
        if not self.reorder(conversation_id, ordered):
            return None
        front = self._queues.get(conversation_id)
        if not front:
            return None
        return front[0]

    def pop_next(self, conversation_id: str) -> QueuedTurn | None:
        q = self._queues.get(conversation_id)
        if not q:
            self._queues.pop(conversation_id, None)
            return None
        item = q.popleft()
        if not q:
            self._queues.pop(conversation_id, None)
        self.push_snapshot(conversation_id, user_id=item.user_id)
        return item

    def schedule_drain(self, conversation_id: str) -> None:
        """Arm a one-shot drain after the active turn ends (idempotent per idle gap)."""
        if conversation_id in self._drain_scheduled:
            return
        if not self._queues.get(conversation_id):
            return
        from .runs import turn_runs

        # Cold resume deferred owns the next free slot — do not steal it for FIFO.
        if turn_runs.has_resume_deferred(conversation_id):
            return
        self._drain_scheduled.add(conversation_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._drain_scheduled.discard(conversation_id)
            return
        loop.call_soon(lambda: asyncio.create_task(self._drain(conversation_id)))

    async def _drain(self, conversation_id: str) -> None:
        self._drain_scheduled.discard(conversation_id)
        from .runs import turn_runs

        # If another turn already claimed the slot, wait for its done-callback.
        existing = turn_runs.get(conversation_id)
        if existing is not None and not existing.task.done():
            return
        # Deferred cold resume has priority over FIFO.
        if turn_runs.has_resume_deferred(conversation_id):
            return

        from agentcore.runtime.turn.durable import claim_durable_start, drain_blocked

        async with self.mutation_lock(conversation_id):
            if await drain_blocked(conversation_id):
                return
            item = self.pop_next(conversation_id)
        if item is None:
            return
        if not await claim_durable_start(item):
            self.schedule_drain(conversation_id)
            return

        logger.info(
            "turn_queue.starting",
            conversation_id=conversation_id,
            queue_id=item.queue_id,
            remaining=self.depth(conversation_id),
        )
        try:
            starter = _queue_starter or _start_queued_turn
            await starter(conversation_id, item)
        except Exception:  # noqa: BLE001 — never strand the rest of the queue
            logger.exception(
                "turn_queue.start_failed",
                conversation_id=conversation_id,
                queue_id=item.queue_id,
            )
            # Continue draining remaining items.
            self.schedule_drain(conversation_id)


def _waiter_still_alive(item: QueuedTurn) -> bool:
    """True when the enqueueing SSE is still waiting to receive the live sink."""
    fut = item.started
    return fut is not None and not fut.done()


async def _start_queued_turn(conversation_id: str, item: QueuedTurn) -> None:
    """Spawn the turn; hand the sink to a waiting SSE if still connected.

    Emits ``turn_queue_started`` as the new sink's first frame (before ``start_turn``),
    carrying the queued item's ``content`` (and attachments / mentions when present).
    """
    import asyncio

    from agentcore.fulfill.origin import origin_device
    from agentcore.runtime.events import EventSink, turn_queue_started
    from agentcore.runtime.turn.driver import get_turn_driver

    from .runs import turn_runs

    if item.credentials_pending and item.user_id:
        from agentcore.runtime.turn.durable import resolve_drain_credentials

        credentials, supports = await resolve_drain_credentials(
            item.user_id,
            conversation_id,
            needs_tools=item.requires_tools,
        )
        item.llm_credentials = credentials
        item.llm_supports_tools = supports

    sink = EventSink()
    remaining_depth = turn_queue.depth(conversation_id)
    sink.emit(
        turn_queue_started(
            queue_id=item.queue_id,
            conversation_id=conversation_id,
            remaining_depth=remaining_depth,
            content=item.content,
            user_message_id=item.user_message_id,
            attachments=item.attachments or None,
            agent_mentions=item.agent_mentions or None,
        )
    )
    if _waiter_still_alive(item):
        # Waiting POST is still open — it becomes the primary SSE consumer (no detach).
        assert item.started is not None
        item.started.set_result(sink)
    else:
        # No waiter / disconnected mid-queue → nobody subscribed; the turn runs detached
        # and端 catch up via attach / 对话级订阅 (the hub hands them this sink on register).
        sink.note_no_consumer(reason="queue_drain_no_waiter")

    # Bind before create_task: the new task copies this context, so the queued
    # message's own device — not the drain's ambient one — owns its CLIENT_TOOLs.
    with origin_device(item.origin_device_id):
        task = asyncio.create_task(
            get_turn_driver().start_turn(
                conversation_id=conversation_id,
                user_message=item.content,
                user_id=item.user_id,
                sink=sink,
                attachments=item.attachments,
                llm_credentials=item.llm_credentials,
                llm_supports_tools=item.llm_supports_tools,
                x_client_platform=item.x_client_platform,
                agent_mentions=item.agent_mentions,
                table_selection=item.table_selection or None,
                existing_user_message_id=item.user_message_id,
            )
        )
    turn_runs.register(
        conversation_id=conversation_id,
        task=task,
        sink=sink,
        user_id=item.user_id,
    )


def new_queued_turn(
    *,
    content: str,
    user_id: str,
    attachments: list[dict[str, Any]] | None = None,
    agent_mentions: list[dict[str, Any]] | None = None,
    table_selection: list[str] | None = None,
    requires_tools: bool = False,
    x_client_platform: str | None = None,
    origin_device_id: str | None = None,
    llm_credentials: Any = None,
    llm_supports_tools: bool | None = None,
    interjection_id: str | None = None,
    started: asyncio.Future[Any] | None = None,
    user_message_id: str | None = None,
    message_id: str | None = None,
    trace_id: str | None = None,
) -> QueuedTurn:
    return QueuedTurn(
        queue_id=new_id(),
        content=content,
        attachments=list(attachments or []),
        agent_mentions=list(agent_mentions or []),
        table_selection=list(table_selection or []),
        requires_tools=requires_tools,
        x_client_platform=x_client_platform,
        origin_device_id=origin_device_id,
        user_id=user_id,
        llm_credentials=llm_credentials,
        llm_supports_tools=llm_supports_tools,
        interjection_id=interjection_id,
        started=started,
        user_message_id=user_message_id,
        message_id=message_id,
        trace_id=trace_id,
    )


def resolve_client_turn_ids(
    *,
    user_message_id: str | None = None,
    message_id: str | None = None,
    trace_id: str | None = None,
) -> tuple[str, str, str]:
    """Keep desktop-minted ids; fill only missing legs so sidecar FIFO can start.

    ``trace_id`` must be 32-hex (``uuid4().hex``). Hyphenated leftovers are
    replaced, not coerced — same rule as ``parse_client_turn_ids``.
    """
    umid = (user_message_id or "").strip() or str(uuid4())
    mid = (message_id or "").strip() or str(uuid4())
    tid = (trace_id or "").strip()
    if not _TRACE_HEX32.fullmatch(tid):
        tid = uuid4().hex
    return umid, mid, tid


# Module-level singleton (single-worker posture, as turn_runs).
turn_queue = TurnQueue()
