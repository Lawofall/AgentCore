"""EventSink — fan-out bridge between one running turn and its N live观察端.

Journal persist lives in ``sink_journal``; process / run_process projection in
``sink_process``; run-terminal occupancy in ``sink_terminal``. Public import path
stays this module (``EventSink``, ``SinkSubscription``, marker constants, tap).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.observability.drop_heartbeat import DropLogHeartbeat, DropPulse
from agentcore.observability.stream_timing import (
    consumer_mode,
    current_http_req_id,
    elapsed_ms,
    mono_now,
    wall_now_iso,
)
from agentcore.runtime.events.chat import content_delta
from agentcore.runtime.events.journal_config import (
    _HISTORY_COALESCE_RUN,
    _HISTORY_COALESCE_TURN,
    _HISTORY_SKIP_TYPES,
    _JOURNAL_EVENT_TYPES,
    cap_process_result,
)
from agentcore.runtime.events.process_persist import ProcessPersistCursor
from agentcore.runtime.events.sink_journal import SinkJournalMixin
from agentcore.runtime.events.sink_process import (
    MARKER_STANDIN_TOOLS,  # noqa: F401 — public re-export
    ORCHESTRATION_TOOLS,  # noqa: F401 — public re-export
    SinkProcessMixin,
    synthesize_required_marker,  # noqa: F401 — public re-export
)
from agentcore.runtime.events.sink_terminal import SinkTerminalMixin
from agentcore.runtime.events.stream_checkpointer import StreamCheckpointer
from agentcore.runtime.events.types import EventType, SSEEvent
from agentcore.runtime.terminal import RUN_CLOSE_EVENT_TYPES

logger = get_logger(__name__)

# Dev-only observation seam (demo-tape recorder): installed by
# ``agentcore.demo_tape.recorder`` under DEMO_TAPE_RECORD_ENABLED, None otherwise.
# Called for every emitted event AFTER normal processing — purely observational;
# a tap failure is logged and never breaks the turn. Not a product contract.
_emit_tap: Callable[[EventSink, SSEEvent], None] | None = None


def set_emit_tap(tap: Callable[[EventSink, SSEEvent], None] | None) -> None:
    """Install / clear the process-wide emit tap (dev-only, e.g. tape recording)."""
    global _emit_tap
    _emit_tap = tap


def _run_emit_tap(sink: EventSink, event: SSEEvent) -> None:
    if _emit_tap is None:
        return
    try:
        _emit_tap(sink, event)
    except Exception as e:  # noqa: BLE001 — observation must never break the turn
        logger.warning("event_tap.failed", error=str(e))


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


# One观察端's live queue is capped here; a端 too slow to drain sheds its OLDEST
# undelivered frame instead of growing without bound or stalling ``emit``. Sized like
# the IM firehose (``messaging/hub.py``) — only a genuinely stuck client sheds, and it
# loses only its own smoothness: correctness is the journal's job (L2), not this queue's.
_SUBSCRIBER_QUEUE_MAXSIZE = 1000


class SinkSubscription:
    """One live consumer of an :class:`EventSink` — a bounded, drop-oldest queue.

    Every观察端 (POST 发送流 / attach / 对话级订阅) holds its OWN subscription: they are
    peers, not「primary + 旁路」. Dropping one never touches the others (:meth:`EventSink.
    unsubscribe`), which is exactly the 断开连坐 bug the single-queue sink had.
    """

    __slots__ = (
        "_drop_log",
        "_last_byte_mono",
        "_queue",
        "_started_at",
        "_started_mono",
        "dropped",
        "label",
    )

    def __init__(self, *, label: str) -> None:
        self.label = label
        self.dropped = 0
        self._drop_log = DropLogHeartbeat()
        self._queue: asyncio.Queue[_Frame | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_MAXSIZE
        )
        now = mono_now()
        self._started_mono = now
        self._last_byte_mono = now
        self._started_at = wall_now_iso()

    def note_byte(self) -> None:
        """Mark that this consumer just received a frame or SSE heartbeat byte."""
        self._last_byte_mono = mono_now()

    def stream_timing(self) -> tuple[str, int, int]:
        """``started_at``, age since subscribe, idle since last byte (ms)."""
        now = mono_now()
        return (
            self._started_at,
            elapsed_ms(self._started_mono, now_mono=now),
            elapsed_ms(self._last_byte_mono, now_mono=now),
        )

    def _offer(self, frame: _Frame) -> bool:
        """Enqueue ``frame``; drop the oldest when full. False → something was shed."""
        try:
            self._queue.put_nowait(frame)
            return True
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(frame)
            self.dropped += 1
            return False

    def _close(self) -> None:
        """End-of-stream sentinel for this consumer (keeps the pending backlog)."""
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(None)

    async def get(self) -> SSEEvent | None:
        """Next event for this consumer, or ``None`` once the sink closed.

        Waits for the emit-side ``seq`` backfill (shielded, so this consumer's own
        cancellation never cancels a task其它端 are also waiting on) before handing the
        event over — the ``id:`` line must carry the journal seq for ``Last-Event-ID``.
        """
        frame = await self._queue.get()
        if frame is None:
            return None
        ready = frame.seq_ready
        if ready is not None and not ready.done():
            try:
                await asyncio.shield(ready)
            except asyncio.CancelledError:
                # The shared backfill was cancelled (sink closed with the write still in
                # flight): ship the frame without an ``id:`` rather than tearing down N
                # streams over transport metadata. Our OWN cancellation still unwinds.
                if not ready.cancelled():
                    raise
        self.note_byte()
        return frame.event

    async def __aiter__(self) -> AsyncIterator[SSEEvent]:
        while True:
            event = await self.get()
            if event is None:
                return
            yield event


class EventSink(SinkJournalMixin, SinkProcessMixin, SinkTerminalMixin):
    """Fan-out bridge between one running turn (producer) and its N live观察端.

    Execution emits here; every subscribed端 gets its own bounded queue
    (:class:`SinkSubscription`), so two devices watching the same turn see the SAME
    frames instead of瓜分ing them, and one disconnecting never cuts the others. Frames
    emitted while nobody is subscribed spool in ``_queue`` (the handoff window between
    sink creation and the first consumer, plus the legacy single-consumer :meth:`get`
    path the sidecar pump uses).

    Durability is orthogonal: DURABLE facts land in the journal whether or not anybody
    is listening, so catching up is a replay concern, never a queue concern.
    """

    def __init__(
        self,
        *,
        conversation_id: str | None = None,
        message_id: str | None = None,
    ) -> None:
        # Live观察端 (peers — no primary). Fan-out order is registration order.
        self._subscribers: list[SinkSubscription] = []
        # Spool for frames emitted with nobody subscribed: the handoff window (sink
        # created → the POST / drain / resume consumer subscribes) and the legacy
        # single-consumer :meth:`get` path. Bounded + drop-oldest like a subscription.
        self._queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_MAXSIZE
        )
        self._closed = False
        # Observability only: a consumer once dropped off this sink. Paired with an
        # empty ``_subscribers`` it means "closed after everyone left" — never used to
        # gate delivery (that would resurrect the sink-wide detach latch we just killed).
        self._consumer_dropped = False
        # Strong refs for fire-and-forget barrier combiner / seq-backfill tasks so the
        # loop does not destroy them while pending ("Task was destroyed but it is pending").
        self._barrier_tasks: set[asyncio.Task[None]] = set()
        self._seq_tasks: set[asyncio.Task[None]] = set()
        self._history: list[SSEEvent] = []
        self._journal: list[dict[str, Any]] = []
        self._process: list[dict[str, Any]] = []
        # Resume-seeded captain / worker timelines (G1/G7). Deep-copied on seed; never
        # mixed into live ``_process`` / ``_run_processes``. Persist projection merges
        # seeded⊕live; streamed_* and content_reset read/mutate live only.
        self._seeded_process: list[dict[str, Any]] = []
        self._seeded_run_processes: dict[str, list[dict[str, Any]]] = {}
        # Per-worker-run 思考·正文·工具 timeline (对称 CEO ``_process``). Keyed by run_id;
        # tools tagged with ``run_id`` land here (not on the captain bubble). Persisted as
        # ``runs.run_processes`` so reload matches live interleaving — ``message_final``
        # splice is NOT the worker timeline source.
        self._run_processes: dict[str, list[dict[str, Any]]] = {}
        # Progressive process_* / run_process_* journal cursor (ordinal idempotent).
        self._process_cursor = ProcessPersistCursor()
        self._conversation_id = conversation_id
        self._message_id = message_id
        now = mono_now()
        self._created_mono = now
        self._created_at = wall_now_iso()
        self._checkpointer: StreamCheckpointer | None = None
        # G6: after content_reset, display-only reinject this text into history + SSE
        # (skip process / checkpointer). None = hook unset (status-quo behaviour).
        self._content_reset_reinjection: str | None = None
        # Stop-after-reset salvage: content_reset pops live content steps; stash the
        # discarded prose so /stop can restore what the user already saw (industry
        # habit). Cleared when a new CONTENT_DELTA arrives (live takes over).
        self._interrupt_content_stash: str | None = None
        # Soft-fail error (ERROR is history-skipped / not journaled): keep the latest
        # payload so settle can stamp turn_end + result.error for reload.
        self._last_error: dict[str, Any] | None = None
        # MESSAGE_END is DERIVED (history-skipped, never journaled). Capture finish_reason
        # so :meth:`history_snapshot` can synthesize the close frame — same role as
        # ``_turn_end_close_event`` on the journal cursor-replay path (收口窗对齐).
        self._stream_finish_reason: str | None = None
        self._stream_outcome: str | None = None
        # First terminal event per run_id wins; later terminals are dropped.
        self._terminal_run_ids: set[str] = set()
        if conversation_id and message_id:
            self._try_start_stream_checkpointer()

    def bind_content_checkpoint(
        self,
        *,
        conversation_id: str,
        message_id: str,
    ) -> None:
        """Wire stream-segment durability for this turn's assistant row (P1).

        Name kept for call-site stability; the 10s ``messages.content`` checkpoint
        loop is retired in favour of ``StreamCheckpointer`` → ``turn_stream_state``.
        """
        self._conversation_id = conversation_id
        self._message_id = message_id
        self._try_start_stream_checkpointer()

    def _try_start_stream_checkpointer(self) -> None:
        if self._checkpointer is not None or self._closed or not self._message_id:
            return
        self._checkpointer = StreamCheckpointer(turn_id=self._message_id)
        self._checkpointer.start()

    def set_content_reset_reinjection(self, text: str | None) -> None:
        """G6: after each ``content_reset``, display-only reinject ``text`` (or clear hook).

        Resume pipeline sets pre_pause so client bubble reset does not wipe the
        suspended-turn base. Pass ``None`` to disable (status quo).
        """
        self._content_reset_reinjection = text

    @property
    def conversation_id(self) -> str | None:
        """The bound conversation id (None until bind_content_checkpoint / ctor set it)."""
        return self._conversation_id

    @property
    def message_id(self) -> str | None:
        """The bound turn/message id (None until bind_content_checkpoint / ctor set it)."""
        return self._message_id

    def _emit_display_only(self, event: SSEEvent) -> None:
        """History + SSE only — skip process accumulation, journal, and checkpointer."""
        if self._closed:
            return
        self._record_history(event)
        self._deliver(event, None)
        _run_emit_tap(self, event)

    def emit_sse_only(self, event: SSEEvent) -> None:
        """Public SSE/history path without journal (e.g. interjection confirm stream)."""
        self._emit_display_only(event)

    def _combine_persist_barriers(
        self,
        futures: list[asyncio.Future[int | None] | None],
    ) -> asyncio.Future[int | None] | None:
        """One SSE barrier that awaits every scheduled journal write for this event.

        Process-lane facts must land before (or with) the closing DURABLE so mid-run
        refresh can fold journal alone — invariant: live-visible process ⇒ journal.
        """
        pending = [f for f in futures if f is not None]
        if not pending:
            return None
        if len(pending) == 1:
            return pending[0]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return pending[-1]
        combined: asyncio.Future[int | None] = loop.create_future()

        async def _wait() -> None:
            seq: int | None = None
            for fut in pending:
                try:
                    allocated = await fut
                except Exception as exc:  # noqa: BLE001 — barrier must not hang SSE
                    if not combined.done():
                        combined.set_exception(exc)
                    return
                if allocated is not None:
                    seq = allocated
            if not combined.done():
                combined.set_result(seq)

        task = loop.create_task(_wait())
        self._barrier_tasks.add(task)

        def _release(done: asyncio.Task[None]) -> None:
            self._barrier_tasks.discard(done)
            # Settle here rather than in a ``finally`` inside ``_wait``: :meth:`close`
            # may cancel this task before it ever ran (emit + close in one tick), and a
            # never-started coroutine skips its own cleanup. An unsettled ``combined``
            # hangs every端 waiting for this event's seq — forever.
            if not combined.done():
                combined.cancel()

        task.add_done_callback(_release)
        return combined

    @property
    def is_closed(self) -> bool:
        """True after :meth:`close` — this sink will never grow a live SSE consumer again."""
        return self._closed

    @property
    def is_detached(self) -> bool:
        """Derived: no端 is currently subscribed.

        Not a latch — there is no sink-wide detach flag any more. One consumer
        dropping only removes ITS subscription (断开不连坐); this reads True solely
        when the last one has gone (or none ever arrived).
        """
        return not self._subscribers

    @property
    def subscriber_count(self) -> int:
        """How many live观察端 this turn currently has."""
        return len(self._subscribers)

    def subscribe(self, *, label: str = "sse", backlog: bool = False) -> SinkSubscription:
        """Register one live consumer and return its own bounded queue.

        ``backlog=True`` hands over the frames emitted before anybody was listening —
        the handoff window of a sink that is born already streaming (POST 发送 preflight
        warnings / ``turn_queue_started`` / cold-resume warnings). Attach-style
        consumers pass ``False`` and catch up through replay instead, otherwise the
        spool would double-deliver what the replay already carries.

        Subscribing to an already-closed sink yields the sentinel immediately, so a
        late观察端 replays and stops rather than hanging on a queue nobody feeds.
        """
        sub = SinkSubscription(label=label)
        if backlog:
            while True:
                try:
                    spooled = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if spooled is not None:
                    sub._offer(_Frame(event=spooled))
        self._subscribers.append(sub)
        if self._closed:
            sub._close()
        logger.info(
            "event_sink.attach",
            conversation_id=self._conversation_id,
            message_id=self._message_id,
            label=label,
            mode=consumer_mode(label),
            started_at=sub._started_at,
            http_req_id=current_http_req_id(),
        )
        return sub

    def unsubscribe(self, sub: SinkSubscription, *, reason: str = "unspecified") -> None:
        """Drop ONE consumer (disconnect / page away). Peers keep streaming.

        Observability: logs ``event_sink.detach`` — ``already_detached`` marks an
        idempotent repeat (this subscription was already gone).
        """
        already_detached = sub not in self._subscribers
        if not already_detached:
            self._subscribers.remove(sub)
            self._consumer_dropped = True
        self._flush_backpressure_drop(sub)
        sub._close()
        started_at, duration_ms, idle_ms = sub.stream_timing()
        logger.info(
            "event_sink.detach",
            reason=reason,
            conversation_id=self._conversation_id,
            message_id=self._message_id,
            already_detached=already_detached,
            started_at=started_at,
            duration_ms=duration_ms,
            idle_ms=idle_ms,
            label=sub.label,
            mode=consumer_mode(sub.label),
            http_req_id=current_http_req_id(),
        )

    def note_no_consumer(self, *, reason: str) -> None:
        """Record that this sink was handed off with nobody listening (drain / resume).

        Purely observational — the turn runs detached and its facts still land in the
        journal, so a later观察端 catches up by replay.
        """
        self._consumer_dropped = True
        now = mono_now()
        logger.info(
            "event_sink.detach",
            reason=reason,
            conversation_id=self._conversation_id,
            message_id=self._message_id,
            already_detached=not self._subscribers,
            started_at=self._created_at,
            duration_ms=elapsed_ms(self._created_mono, now_mono=now),
            idle_ms=elapsed_ms(self._created_mono, now_mono=now),
        )

    def _deliver(self, event: SSEEvent, barrier: asyncio.Future[int | None] | None) -> bool:
        """Fan ``event`` out to every subscriber (or spool it). True → someone got it.

        The journal ``seq`` is resolved HERE, once per event, before fan-out — see
        :func:`_backfill_seq` for why that cannot live on the consumer side any more.
        """
        ready = self._arm_seq_backfill(event, barrier)
        subs = tuple(self._subscribers)
        if not subs:
            self._spool(event)
            return False
        frame = _Frame(event=event, seq_ready=ready)
        event_type = event.type.value
        for sub in subs:
            if not sub._offer(frame):
                pulse = sub._drop_log.note(event_type)
                if pulse is not None:
                    self._log_backpressure_drop(sub, pulse)
        return True

    def _log_backpressure_drop(self, sub: SinkSubscription, pulse: DropPulse) -> None:
        logger.warning(
            "event_sink.backpressure_drop",
            conversation_id=self._conversation_id,
            message_id=self._message_id,
            label=sub.label,
            type=pulse.event_type,
            dropped_delta=pulse.dropped_delta,
            dropped_total=pulse.dropped_total,
        )

    def _flush_backpressure_drop(self, sub: SinkSubscription) -> None:
        pulse = sub._drop_log.flush()
        if pulse is not None:
            self._log_backpressure_drop(sub, pulse)

    def _arm_seq_backfill(
        self, event: SSEEvent, barrier: asyncio.Future[int | None] | None
    ) -> asyncio.Task[None] | None:
        if barrier is None:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        task = loop.create_task(_backfill_seq(event, barrier))
        self._seq_tasks.add(task)
        task.add_done_callback(self._seq_tasks.discard)
        return task

    def _spool(self, event: SSEEvent) -> None:
        """Buffer a frame nobody is listening to yet (bounded, drop-oldest)."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(event)

    def emit(self, event: SSEEvent) -> bool:
        """Emit ``event`` to every live观察端. True iff at least one got it.

        A closed sink skips delivery (Pillar A may still journal DURABLE facts when
        closed); with nobody subscribed the frame spools instead. ``False`` means *no
        live consumer* — not "bridge dead". CLIENT_TOOL delivery is independent of
        this sink.
        """
        if event.type in RUN_CLOSE_EVENT_TYPES:
            run_id = (event.payload or {}).get("run_id")
            if isinstance(run_id, str) and run_id:
                if run_id in self._terminal_run_ids:
                    logger.info(
                        "run.terminal_duplicate_dropped",
                        run_id=run_id,
                        type=event.type.value,
                    )
                    return False
                self._terminal_run_ids.add(run_id)
        if self._closed:
            # Pillar A: DURABLE display facts persist at execution/host journal scope
            # even after the turn sink closes; SSE / history are best-effort only.
            self._persist_durable_closed(event)
            _run_emit_tap(self, event)
            return False
        if event.type is EventType.MESSAGE_END:
            finish = (event.payload or {}).get("finish_reason")
            if finish is not None:
                self._stream_finish_reason = str(finish)
            raw_outcome = (event.payload or {}).get("outcome")
            if raw_outcome in ("ok", "partial", "paused", "error"):
                self._stream_outcome = str(raw_outcome)
            # 团队状态由本回合 journal 派生；未显式传入时在此盖章（所有 emit 口同一真源）。
            if not (event.payload or {}).get("team_batch"):
                from agentcore.runtime.journal.team_batch import team_batch_from_entries

                event.payload["team_batch"] = team_batch_from_entries(self._journal)
        if event.type is EventType.ERROR:
            self._last_error = dict(event.payload)
        # Accumulate FIRST: closed process_* / run_process_* schedule before the
        # DURABLE fact that closed them (journal interleave == live timeline).
        process_futures = self._accumulate_process(event)
        persist_future: asyncio.Future[int | None] | None = None
        if event.type in _JOURNAL_EVENT_TYPES:
            persist_future = self._schedule_durable_persist(event)
        self._record_history(event)
        if self._checkpointer is not None:
            self._checkpointer.observe(event)
        live = self._deliver(
            event, self._combine_persist_barriers([*process_futures, persist_future])
        )
        _run_emit_tap(self, event)
        # G6: reinject after content_reset is fully processed (history + SSE +
        # checkpointer already saw the reset). Display-only path skips process /
        # checkpointer so salvage and persist timelines stay unduplicated.
        if (
            event.type is EventType.CONTENT_RESET
            and self._content_reset_reinjection is not None
        ):
            self._emit_display_only(content_delta(self._content_reset_reinjection))
        return live

    def _record_history(self, event: SSEEvent) -> None:
        t = event.type
        if t in _HISTORY_SKIP_TYPES:
            return
        if t in _HISTORY_COALESCE_TURN:
            delta = event.payload.get("delta") or ""
            if not delta:
                return
            last = self._history[-1] if self._history else None
            if last is not None and last.type == t:
                last.payload["delta"] = (last.payload.get("delta") or "") + delta
            else:
                self._history.append(
                    SSEEvent(type=t, payload={"delta": delta}, timestamp=event.timestamp)
                )
            return
        if t in _HISTORY_COALESCE_RUN:
            delta = event.payload.get("delta") or ""
            if not delta:
                return
            run_id = event.payload.get("run_id")
            last = self._history[-1] if self._history else None
            if last is not None and last.type == t and last.payload.get("run_id") == run_id:
                last.payload["delta"] = (last.payload.get("delta") or "") + delta
            else:
                self._history.append(
                    SSEEvent(type=t, payload=dict(event.payload), timestamp=event.timestamp)
                )
            return
        if t == EventType.TOOL_USE_END:
            payload = dict(event.payload)
            payload["result"] = cap_process_result(payload.get("result"))
            self._history.append(SSEEvent(type=t, payload=payload, timestamp=event.timestamp))
            return
        self._history.append(SSEEvent(type=t, payload=event.payload, timestamp=event.timestamp))

    def history_snapshot(self) -> list[SSEEvent]:
        """Replay段 for a观察端 that just subscribed (same-process fast path).

        A pure read: it neither evicts a consumer nor re-arms anything — every端 is one
        of N peers and catches up on its own (which is why the old ``take_over``, with
        its「清空积压再武装」exclusivity, is gone).

        Aligned with journal cursor replay: MESSAGE_END is history-skipped, and a turn
        that finished with nobody attached emitted it into the void — without a synthetic
        close the client finalizes only via reconnect-banner salvage (bubble stuck
        streaming).
        """
        snapshot = list(self._history)
        if self._stream_finish_reason is not None:
            from agentcore.runtime.journal.team_batch import team_batch_from_entries

            close_payload: dict[str, Any] = {"finish_reason": self._stream_finish_reason}
            if self._stream_outcome is not None:
                close_payload["outcome"] = self._stream_outcome
            close_payload["team_batch"] = team_batch_from_entries(self._journal)
            snapshot.append(
                SSEEvent(
                    type=EventType.MESSAGE_END,
                    payload=close_payload,
                )
            )
        return snapshot

    def last_turn_error(self) -> dict[str, Any] | None:
        """Latest ``error`` SSE payload (code/message[/context]), or None.

        ERROR events are transport-only (not journaled / not in ``_history``); this
        is the durable hand-off into ``turn_end`` + settle result for reload.
        """
        return self._last_error

    def stream_memory_snapshot(self) -> dict[str, str]:
        """In-memory stream-channel texts (for error/FAILED salvage merge)."""
        if self._checkpointer is None:
            return {}
        return self._checkpointer.memory_snapshot()

    async def flush_stream_state(self) -> None:
        """Best-effort flush of dirty stream segments (call before turn收口)."""
        if self._checkpointer is not None:
            await self._checkpointer.flush()

    def close(self, *, reason: str = "unspecified") -> None:
        """Permanently close this sink (sentinel for SSE consumers). Idempotent.

        Observability: logs ``event_sink.close`` only on the open→closed transition
        (``was_detached`` distinguishes a prior consumer drop from a still-attached close).
        """
        if not self._closed:
            was_detached = self._consumer_dropped and not self._subscribers
            self._closed = True
            for task in list(self._barrier_tasks):
                task.cancel()
            self._barrier_tasks.clear()
            if self._checkpointer is not None:
                # Schedule final flush without blocking close (SSE consumer may still drain).
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._checkpointer.close())
                except RuntimeError:
                    pass
                self._checkpointer = None
            for sub in tuple(self._subscribers):
                self._flush_backpressure_drop(sub)
                sub._close()
            self._spool_close()
            logger.info(
                "event_sink.close",
                reason=reason,
                conversation_id=self._conversation_id,
                message_id=self._message_id,
                was_detached=was_detached,
            )

    def _spool_close(self) -> None:
        """End-of-stream sentinel on the spool (legacy single-consumer path)."""
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(None)

    async def get(self) -> SSEEvent | None:
        """Legacy single-consumer read off the spool (sidecar pump / tests).

        SSE routes take a :meth:`subscribe` handle instead — one bounded queue per端 is
        what makes two clients see the same frames. This path carries no ``seq`` (the
        sidecar's JSON-RPC notifications have no ``id:`` line to fill).
        """
        return await self._queue.get()

    async def __aiter__(self) -> AsyncIterator[SSEEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event
