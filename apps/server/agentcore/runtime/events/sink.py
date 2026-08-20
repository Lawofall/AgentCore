"""EventSink — fan-out bridge between one running turn and its N live观察端."""

from __future__ import annotations

import asyncio
import contextlib
import copy
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
    _JOURNAL_SURFACE_TYPES,
    cap_process_result,
    journal_payload_for_persist,
)
from agentcore.runtime.events.process_persist import (
    ProcessPersistCursor,
    should_persist_on_close,
)
from agentcore.runtime.events.stream_checkpointer import StreamCheckpointer
from agentcore.runtime.events.types import EventType, SSEEvent
from agentcore.runtime.facts import Fact, current_fact_log, record_turn_fact

# One run_id may enter a terminal face once (live + journal). Duplicate
# run_failed / run_completed / run_cancelled / run_skipped for the same id
# are dropped so fold last-write-wins cannot diverge from the live stream.
_RUN_TERMINAL_TYPES = frozenset(
    {
        EventType.RUN_COMPLETED,
        EventType.RUN_FAILED,
        EventType.RUN_CANCELLED,
        EventType.RUN_SKIPPED,
    }
)

# Orchestration tools hand the turn to a sub-team and open a team execution. Their
# captain-level call is NOT rendered as a tool step — the `team` marker (emitted at
# run_plan) stands in its place as the collaboration graph's timeline slot. Mirrors
# the TS SSOT `@agentcore/protocol-fold-kit` (desktop/mobile consume that; keep Python
# twin in lockstep). Shared with the conformance oracle (projection.py) so live +
# golden agree.
ORCHESTRATION_TOOLS = frozenset({"delegate", "debate"})

# CEO self-calls whose inline-timeline slot is stood in for by a DEDICATED marker, so
# they make NO captain tool step: delegate/debate → `team` (at run_plan); ask_user →
# `checkpoint`/`ask` (at *_required / question_posted). Superset of ORCHESTRATION_TOOLS
# (which stays scoped to team/graph semantics). ask_user belongs here because a blocking
# ask SUSPENDs without a tool_use_end (its card marker represents it), and a rejected ask
# (card-shape validation) must not leak a red tool-error row — the model self-corrects and
# re-asks. Mirrors `@agentcore/protocol-fold-kit` + oracle (projection.py); keep lockstep.
MARKER_STANDIN_TOOLS = ORCHESTRATION_TOOLS | frozenset({"ask_user"})

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


def _step_has_marker(steps: list[dict[str, Any]], kind: str, key: str, value: str) -> bool:
    return any(s.get("kind") == kind and s.get(key) == value for s in steps)


def _insert_marker_step(
    steps: list[dict[str, Any]],
    marker: dict[str, Any],
    *,
    before_last_team: bool = False,
) -> None:
    """Insert a positional marker into ``steps`` (caller owns dedup).

    ``before_last_team`` mirrors team_preview product narrative: 开工卡 sits just
    before the collaboration-graph ``team`` marker, not after it.
    """
    if before_last_team:
        for i in range(len(steps) - 1, -1, -1):
            if steps[i].get("kind") == "team":
                steps.insert(i, marker)
                return
    steps.append(marker)


def _marker_spec_for_required(
    event_type: EventType | str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bool] | None:
    """Build (marker_step, before_last_team) for a timeline-marker surface event.

    Covers ``*_required`` / ask / raised ``run_escalation`` (统一时间线二期). Shared by
    ``EventSink._accumulate_process`` and suspension capture (G7) so live emit and
    ``turn_paused`` snapshot stay lockstep. Returns None when the event is not a
    marker surface or the id is empty.
    """
    t = event_type if isinstance(event_type, EventType) else EventType(event_type)
    if t == EventType.CHECKPOINT_REQUIRED:
        cid = payload.get("checkpoint_id") or ""
        if not cid:
            return None
        return {"kind": "checkpoint", "checkpoint_id": cid}, False
    if t == EventType.QUESTION_POSTED:
        aid = payload.get("ask_id") or ""
        if not aid:
            return None
        return {"kind": "ask", "ask_id": aid}, False
    if t == EventType.PLAN_REVIEW_REQUIRED:
        cid = payload.get("checkpoint_id") or ""
        if not cid:
            return None
        return {"kind": "plan_review", "checkpoint_id": cid}, False
    if t == EventType.TEAM_PREVIEW_REQUIRED:
        cid = payload.get("checkpoint_id") or ""
        if not cid:
            return None
        return {"kind": "team_preview", "checkpoint_id": cid}, True
    if t in (EventType.ESCALATION_REQUIRED, EventType.RUN_ESCALATION):
        eid = payload.get("escalation_id") or ""
        if not eid:
            return None
        return {"kind": "escalation", "escalation_id": eid}, False
    if t == EventType.APPROVAL_REQUIRED:
        aid = payload.get("approval_id") or ""
        if not aid:
            return None
        return {"kind": "approval", "approval_id": aid}, False
    if t == EventType.DELEGATION_AUTHORIZATION_REQUIRED:
        aid = payload.get("authorization_id") or ""
        if not aid:
            return None
        # 产品修正（统一时间线二期落地后拍板）：委派授权与开工卡同属「放行开工」族，
        # 统一叙事 授权 → 团队干活 —— 与 team_preview 同锚定，排协作图 team 标记之前。
        # 仅此一 kind；escalation / approval 维持事件时刻、排图后。
        return {"kind": "delegation_authorization", "authorization_id": aid}, True
    if t == EventType.STAGE_CARD_REQUIRED:
        sid = payload.get("stage_card_id") or ""
        if not sid:
            return None
        return {"kind": "stage_card", "stage_card_id": sid}, False
    return None


def synthesize_required_marker(
    steps: list[dict[str, Any]],
    event_type: EventType | str,
    payload: dict[str, Any],
) -> bool:
    """Synthesize a marker step into ``steps`` from a ``*_required`` event (G7).

    Dedups within ``steps`` (``_has_marker`` semantics on the target list). Returns
    whether a marker was inserted. Capture side uses this on the live process lane
    (then flushes to journal) so the pause-anchor marker lands even though the
    required event emits *after* ``persist_suspension_capture``.
    """
    spec = _marker_spec_for_required(event_type, payload)
    if spec is None:
        return False
    marker, before_last_team = spec
    kind = marker["kind"]
    key = next(k for k in marker if k != "kind")
    value = marker[key]
    if _step_has_marker(steps, kind, key, value):
        return False
    _insert_marker_step(steps, marker, before_last_team=before_last_team)
    return True


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


class EventSink:
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
        self._has_run_plan = False
        self._has_tool = False
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

    def run_has_terminal(self, run_id: str) -> bool:
        """True when this sink already emitted a terminal frame for ``run_id``.

        Terminal = ``run_completed`` / ``run_failed`` / ``run_cancelled`` /
        ``run_skipped``. Used by the executor ``finally`` so a started run cannot
        leave the journal without a close frame (CancelledError bypasses
        ``except Exception``).
        """
        return bool(run_id) and run_id in self._terminal_run_ids

    def emit(self, event: SSEEvent) -> bool:
        """Emit ``event`` to every live观察端. True iff at least one got it.

        A closed sink skips delivery (Pillar A may still journal DURABLE facts when
        closed); with nobody subscribed the frame spools instead. ``False`` means *no
        live consumer* — not "bridge dead". CLIENT_TOOL delivery is independent of
        this sink.
        """
        if event.type in _RUN_TERMINAL_TYPES:
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

    def _persist_durable_closed(self, event: SSEEvent) -> None:
        """Journal-only path after sink.close — no history / SSE / process lane."""
        if event.type not in _JOURNAL_EVENT_TYPES:
            return
        self._schedule_durable_persist(event)

    def _schedule_durable_persist(
        self, event: SSEEvent
    ) -> asyncio.Future[int | None] | None:
        """Append a DURABLE fact to the host/execution journal (sink-lifetime independent)."""
        from agentcore.runtime.delegate.graph_append import register_graph_host
        from agentcore.runtime.journal.writer import (
            current_journal_writer,
            is_seal_overflow_kind,
        )

        if event.type is EventType.RUN_PLAN and self._message_id:
            register_graph_host(
                str(event.payload.get("execution_id") or ""),
                self._message_id,
            )
        # Copy+cap for JSONB only — live SSE keeps the uncapped wire payload.
        persist_payload = journal_payload_for_persist(event.type.value, event.payload)
        self._journal.append(
            {
                "type": event.type.value,
                "payload": persist_payload,
                "timestamp": event.timestamp,
            }
        )
        # Prefer turn ContextVar writer (also updates fact_log) while the arming
        # turn is still attached. After ContextVar reset *or* detach
        # (``turn_attached=False``), DURABLE ``run_*`` / ``execution_*`` must land
        # on the execution-bound host writer — child tasks may still see a stale
        # ContextVar pointing at a sealed/new-turn writer.
        # Pause ``seal()`` is the same family: the live writer is frozen, so
        # execution terminals must take the (rebound, unsealed) host writer
        # instead of silently no-op'ing on the sealed ContextVar.
        kind = event.type.value
        live_writer = current_journal_writer.get()
        host_writer = self._execution_host_writer(event)
        detached = self._coordination_detached(event)
        sealed_live = live_writer is not None and getattr(live_writer, "sealed", False)
        use_host = detached or (sealed_live and is_seal_overflow_kind(kind))
        if live_writer is not None and not use_host:
            return record_turn_fact(
                Fact(
                    kind=kind,
                    payload=persist_payload,
                    ts=event.timestamp,
                )
            )
        # ContextVar writer is gone (or a child still holds a stale other-turn
        # writer). Keep the arming turn's fact log in sync so the post-drive
        # finalize snapshot includes these frames — schedule_append alone does
        # not update fact_log, and sidecar READY would otherwise replace the
        # progressive journal with the pre-detach settle copy.
        session = self._coordination_session_for_event(event)
        log = getattr(session, "host_fact_log", None) if session is not None else None
        persist_entry = {
            "kind": kind,
            "payload": persist_payload,
            "ts": event.timestamp,
        }
        fact = Fact(
            kind=kind,
            payload=persist_payload,
            ts=event.timestamp,
        )
        future: asyncio.Future[int | None] | None
        if host_writer is not None:
            future = host_writer.schedule_append(persist_entry)
        else:
            future = record_turn_fact(fact)
            # ``record_turn_fact`` already updated ``current_fact_log`` (may be
            # the same object as ``host_fact_log``). Avoid a duplicate row.
            if log is not None and current_fact_log.get() is log:
                return future
        overflow = is_seal_overflow_kind(kind)
        if log is not None and (future is not None or not overflow):
            log.record_fact(fact)
        return future

    def _execution_host_writer(self, event: SSEEvent):
        """Bound host journal writer for the event's execution, if any.

        Resolve order: payload.execution_id → current_execution_id ContextVar →
        conversation registry (cross-task after turn teardown resets ContextVars).
        A sealed pause writer is not a dead end — ``writable()`` is the unsealed
        overflow successor on the same ``turn_id``.
        """
        session = self._coordination_session_for_event(event)
        if session is None:
            return None
        writer = getattr(session, "host_journal_writer", None)
        if writer is None:
            return None
        writable = getattr(writer, "writable", None)
        if callable(writable):
            return writable()
        if getattr(writer, "sealed", False):
            return None
        return writer

    def _coordination_session_for_event(self, event: SSEEvent):
        """Live coordination session for ``event``, if any."""
        from agentcore.runtime.coordination.session import (
            active_coordination,
            registered_coordination_for_conversation,
        )

        eid = str((event.payload or {}).get("execution_id") or "").strip()
        session = active_coordination(eid) if eid else active_coordination()
        if session is None and self._conversation_id:
            session = registered_coordination_for_conversation(self._conversation_id)
        return session

    def _coordination_detached(self, event: SSEEvent) -> bool:
        """True when the event's coordination session has released the arming turn."""
        session = self._coordination_session_for_event(event)
        return session is not None and not session.turn_attached

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

    def _has_marker(self, kind: str, key: str, value: str) -> bool:
        """Whether a positional marker step (team / checkpoint / ask / plan_review) for
        ``value`` is already in the timeline — keeps a replayed / multi-batch event from
        dropping a duplicate anchor. Scans seeded ⊕ live so resume-seeded anchors dedup."""
        return _step_has_marker(self._seeded_process, kind, key, value) or _step_has_marker(
            self._process, kind, key, value
        )

    def _run_process(self, run_id: str) -> list[dict[str, Any]]:
        return self._run_processes.setdefault(run_id, [])

    def _persist_closed_captain_text(self) -> list[Any]:
        """Journal the open captain text step that a boundary is about to close."""
        merged = self.raw_process()
        if not merged or not should_persist_on_close(merged[-1]):
            return []
        return self._process_cursor.persist_captain_range(merged, start=0, end=len(merged))

    def _persist_closed_run_text(self, run_id: str) -> list[Any]:
        steps = self._run_process(run_id)
        seeded = self._seeded_run_processes.get(run_id) or []
        merged = [*seeded, *steps] if seeded else list(steps)
        if not merged or not should_persist_on_close(merged[-1]):
            return []
        return self._process_cursor.persist_run_range(run_id, merged, start=0, end=len(merged))

    def flush_process_to_journal(self) -> None:
        """Persist every not-yet-journaled process / run_process step (finalize / pause).

        Call at semantic turn boundaries so open trailing text steps and markers land
        before ``turn_end`` / ``turn_paused``. Idempotent via the ordinal cursor.
        """
        self._process_cursor.persist_new_captain_tail(self.raw_process())
        for rid, steps in self._merged_run_processes().items():
            self._process_cursor.persist_new_run_tail(rid, steps)

    def _persist_captain_marker_after_insert(
        self,
        marker: dict[str, Any],
        *,
        before_last_team: bool,
    ) -> list[Any]:
        """Journal a newly inserted captain marker (append or ``before_last_team``).

        Ordinal tail persist covers the common append case. Two compensations:

        - Mid-insert behind the cursor (``team`` already journaled at ``run_plan``,
          then 开工卡 / 授权 inserts before it): schedule the marker alone and advance
          the cursor by one so the shifted tail is not re-journaled.
        - Open tool ahead of the marker holds the cursor (SUSPEND ``ask_user``): schedule
          the marker and seed past the lane.
        """
        from agentcore.runtime.events.process_persist import schedule_process_step

        merged = self.raw_process()
        kind = marker["kind"]
        key = next(k for k in marker if k != "kind")
        marker_idx = next(
            (
                i
                for i, step in enumerate(merged)
                if step.get("kind") == kind and step.get(key) == marker[key]
            ),
            None,
        )
        futures: list[Any] = []
        if (
            before_last_team
            and marker_idx is not None
            and marker_idx < self._process_cursor.captain
        ):
            fut = schedule_process_step(marker)
            if fut is not None:
                futures.append(fut)
            self._process_cursor.seed_captain(self._process_cursor.captain + 1)
        futures.extend(self._process_cursor.persist_new_captain_tail(merged))
        # Open tool ahead of the marker holds the ordinal cursor — compensate.
        if marker_idx is not None and self._process_cursor.captain <= marker_idx:
            fut = schedule_process_step(marker)
            if fut is not None:
                futures.append(fut)
            self._process_cursor.seed_captain(len(merged))
        return futures

    def persist_required_marker(self, event_type: Any, payload: dict[str, Any]) -> None:
        """Insert a pause-anchor marker into the live captain lane and journal it.

        The ``*_required`` SSE is emitted *after* suspension capture, so the capture
        path must synthesize the marker itself for process_* progressive persistence.

        Mirrors the live SSE ``*_required`` accumulate path (close open text → insert
        → ordinal tail persist) so ``before_last_team`` markers keep live order in
        ``process_*`` (开工卡 before ``team``). When an open tool ahead of the marker
        holds the cursor (SUSPEND ``ask_user`` never emits ``tool_use_end`` before
        pause), fall back to scheduling the marker fact + seeding the cursor past
        the lane — same compensation the old always-``seed_captain(len)`` path used,
        but only when ordinal persist could not reach the marker.
        """
        spec = _marker_spec_for_required(event_type, payload)
        if spec is None:
            return
        marker, before_last_team = spec
        kind = marker["kind"]
        key = next(k for k in marker if k != "kind")
        if self._has_marker(kind, key, marker[key]):
            return

        # before_last_team: insert BEFORE closing/persisting so a trailing text step
        # cannot pin ``team`` ahead of the marker in process_* order. Append markers
        # still close open text first (live SSE parity).
        if not before_last_team:
            self._persist_closed_captain_text()
        if not synthesize_required_marker(self._process, event_type, payload):
            return

        self._persist_captain_marker_after_insert(
            marker, before_last_team=before_last_team
        )

    def _accumulate_run_process(self, event: SSEEvent) -> list[Any]:
        """Accumulate a worker run's ProcessStep[] (mirrors captain ``_accumulate_process``)."""
        futures: list[Any] = []
        t = event.type
        payload = event.payload
        if t == EventType.RUN_REASONING_DELTA:
            run_id = payload.get("run_id") or ""
            delta = payload.get("delta") or ""
            if not run_id or not delta:
                return futures
            steps = self._run_process(run_id)
            if steps and steps[-1].get("kind") == "reasoning":
                steps[-1]["text"] += delta
            else:
                if steps and should_persist_on_close(steps[-1]):
                    futures.extend(self._persist_closed_run_text(run_id))
                steps.append({"kind": "reasoning", "text": delta})
        elif t == EventType.RUN_OUTPUT_DELTA:
            run_id = payload.get("run_id") or ""
            delta = payload.get("delta") or ""
            if not run_id or not delta:
                return futures
            steps = self._run_process(run_id)
            if steps and steps[-1].get("kind") == "content":
                steps[-1]["text"] += delta
            else:
                if steps and should_persist_on_close(steps[-1]):
                    futures.extend(self._persist_closed_run_text(run_id))
                steps.append({"kind": "content", "text": delta})
        elif t == EventType.RUN_OUTPUT_RESET:
            run_id = payload.get("run_id") or ""
            if not run_id:
                return futures
            steps = self._run_process(run_id)
            # Discard open (unpersisted) trailing content — do not journal it.
            while steps and steps[-1].get("kind") == "content":
                steps.pop()
            # Only 交付前核验回炉 leaves the persisted「已按交付规范重写」trace; every
            # other reason (retry / narration / …) clears the draft without a chip.
            if payload.get("reason") == "finish_guard":
                steps.append({"kind": "rework"})
                seeded = self._seeded_run_processes.get(run_id) or []
                merged = [*seeded, *steps] if seeded else list(steps)
                futures.extend(self._process_cursor.persist_new_run_tail(run_id, merged))
        elif t == EventType.TOOL_USE_START:
            run_id = payload.get("run_id") or ""
            if not run_id:
                return futures
            futures.extend(self._persist_closed_run_text(run_id))
            self._run_process(run_id).append(
                {
                    "kind": "tool",
                    "id": payload.get("tool_call_id", ""),
                    "tool_name": payload.get("tool_name", ""),
                    "arguments": payload.get("arguments") or {},
                    "result": None,
                    "status": "running",
                }
            )
        elif t == EventType.TOOL_USE_END:
            run_id = payload.get("run_id") or ""
            if not run_id:
                return futures
            call_id = payload.get("tool_call_id", "")
            result = cap_process_result(payload.get("result"))
            display = payload.get("display")
            failure = payload.get("failure")
            for step in reversed(self._run_process(run_id)):
                if step.get("kind") == "tool" and step.get("id") == call_id:
                    step["result"] = result
                    step["status"] = payload.get("status", "success")
                    if display is not None:
                        step["display"] = display
                    if failure is not None:
                        step["failure"] = failure
                    break
            seeded = self._seeded_run_processes.get(run_id) or []
            live = self._run_process(run_id)
            merged = [*seeded, *live] if seeded else list(live)
            # Terminal tool persist (holds open tools on flush; compensates if cursor
            # already advanced past a stale running row).
            futures.extend(
                self._process_cursor.persist_resolved_run_tool(run_id, merged, call_id)
            )
        return futures

    def _accumulate_process(self, event: SSEEvent) -> list[Any]:
        # Worker-scoped deltas / tools accumulate on the per-run lane first (then the
        # captain branch no-ops them via run_id / event-type guards below).
        futures = self._accumulate_run_process(event)
        t = event.type
        if t == EventType.RUN_PLAN:
            self._has_run_plan = True
            # 旧 divert 生长帧带 host_message_id：不在新回合插 team（锚点曾由 graph_append
            # 承担）。新路径每回合新图，无 host_message_id，正常插 team。
            if event.payload.get("host_message_id"):
                return futures
            # 协作图时间线落点 (统一团队时间线): the FIRST run_plan of an execution drops a
            # zero-width `team` marker at its chronological spot, so the inline graph renders
            # there rather than at the bottom. Later batches (same execution_id) merge into the
            # same graph — one marker per execution.
            execution_id = event.payload.get("execution_id") or ""
            if execution_id and not self._has_marker("team", "execution_id", execution_id):
                futures.extend(self._persist_closed_captain_text())
                self._process.append({"kind": "team", "execution_id": execution_id})
                # Same as other timeline markers: journal at insert so mid-run reload
                # (workers still running, no captain flush yet) replays ``team``.
                futures.extend(
                    self._process_cursor.persist_new_captain_tail(self.raw_process())
                )
        elif t == EventType.USER_INTERJECTION:
            # 用户插话时间线落点: 同 interjection_id 首次出现（received）钉零宽 marker，
            # 正文与五态仍由旁路 userInterjections 按 id 查；后续 injected/addressed/
            # queued/failed 只更新旁路，不重复落标记。打断 content 尾部合并是预期红利。
            iid = event.payload.get("interjection_id") or ""
            if iid and not self._has_marker("user_interjection", "interjection_id", iid):
                futures.extend(self._persist_closed_captain_text())
                self._process.append({"kind": "user_interjection", "interjection_id": iid})
                futures.extend(
                    self._process_cursor.persist_new_captain_tail(self.raw_process())
                )
        elif t == EventType.GRAPH_APPEND:
            # 已停发：仅兼容旧 journal 回放。
            futures.extend(self._persist_closed_captain_text())
            self._process.append(
                {
                    "kind": "graph_append",
                    "execution_id": event.payload.get("execution_id") or "",
                    "host_message_id": event.payload.get("host_message_id") or "",
                    "added_count": int(event.payload.get("added_count") or 0),
                }
            )
        elif t == EventType.REASONING_DELTA:
            delta = event.payload.get("delta") or ""
            if not delta:
                return futures
            if self._process and self._process[-1].get("kind") == "reasoning":
                self._process[-1]["text"] += delta
            else:
                if self._process and should_persist_on_close(self._process[-1]):
                    futures.extend(self._persist_closed_captain_text())
                self._process.append({"kind": "reasoning", "text": delta})
        elif t == EventType.CONTENT_DELTA:
            delta = event.payload.get("delta") or ""
            if not delta:
                return futures
            # Live rewrite superseded the pre-reset draft — drop interrupt stash.
            self._interrupt_content_stash = None
            if self._process and self._process[-1].get("kind") == "content":
                self._process[-1]["text"] += delta
            else:
                if self._process and should_persist_on_close(self._process[-1]):
                    futures.extend(self._persist_closed_captain_text())
                self._process.append({"kind": "content", "text": delta})
        elif t == EventType.CONTENT_RESET:
            # Discard open (unpersisted) trailing content — do not journal discarded prose.
            # Stash discarded text for /stop salvage (empty discard keeps prior stash).
            trailing: list[str] = []
            i = len(self._process) - 1
            while i >= 0 and self._process[i].get("kind") == "content":
                trailing.append(self._process[i].get("text", "") or "")
                i -= 1
            discarded = "".join(reversed(trailing))
            if discarded:
                self._interrupt_content_stash = discarded
            while self._process and self._process[-1].get("kind") == "content":
                self._process.pop()
        elif t == EventType.TOOL_USE_START:
            payload = event.payload
            # Skip a delegated worker's call (run-scoped — belongs to its run node, not the
            # captain timeline) and a marker-standin call (delegate/debate → `team`;
            # ask_user → `checkpoint`/`ask`). Neither becomes a captain tool step.
            if payload.get("run_id") or payload.get("tool_name") in MARKER_STANDIN_TOOLS:
                return futures
            futures.extend(self._persist_closed_captain_text())
            self._has_tool = True
            self._process.append(
                {
                    "kind": "tool",
                    "id": payload.get("tool_call_id", ""),
                    "tool_name": payload.get("tool_name", ""),
                    "arguments": payload.get("arguments") or {},
                    "result": None,
                    "status": "running",
                }
            )
        elif t == EventType.TOOL_USE_END:
            payload = event.payload
            if payload.get("run_id") or payload.get("tool_name") in MARKER_STANDIN_TOOLS:
                return futures
            call_id = payload.get("tool_call_id", "")
            result = cap_process_result(payload.get("result"))
            display = payload.get("display")
            failure = payload.get("failure")
            for step in reversed(self._process):
                if step.get("kind") == "tool" and step.get("id") == call_id:
                    step["result"] = result
                    step["status"] = payload.get("status", "success")
                    if display is not None:
                        step["display"] = display
                    if failure is not None:
                        step["failure"] = failure
                    break
            # Terminal tool persist (holds open tools on flush; compensates if cursor
            # already advanced past a stale running row).
            futures.extend(
                self._process_cursor.persist_resolved_captain_tool(
                    self.raw_process(), call_id
                )
            )
        elif t in (
            EventType.CHECKPOINT_REQUIRED,
            EventType.QUESTION_POSTED,
            EventType.PLAN_REVIEW_REQUIRED,
            EventType.TEAM_PREVIEW_REQUIRED,
            EventType.ESCALATION_REQUIRED,
            EventType.RUN_ESCALATION,
            EventType.APPROVAL_REQUIRED,
            EventType.DELEGATION_AUTHORIZATION_REQUIRED,
            EventType.STAGE_CARD_REQUIRED,
        ):
            # Positional card / 痕迹 anchors — shared builder with synthesize_required_marker
            # (G7). Dedup scans seeded⊕live; insert targets live only.
            spec = _marker_spec_for_required(t, event.payload)
            if spec is None:
                return futures
            marker, before_last_team = spec
            kind = marker["kind"]
            key = next(k for k in marker if k != "kind")
            if self._has_marker(kind, key, marker[key]):
                return futures
            futures.extend(self._persist_closed_captain_text())
            _insert_marker_step(self._process, marker, before_last_team=before_last_team)
            # Middle-insert (before_last_team) after an already-journaled ``team`` needs
            # cursor compensation — shared with persist_required_marker.
            futures.extend(
                self._persist_captain_marker_after_insert(
                    marker, before_last_team=before_last_team
                )
            )
        return futures

    def seed_journal(self, events: list[dict[str, Any]]) -> None:
        self._journal.extend(events)

    def seed_process(self, steps: list[dict[str, Any]]) -> None:
        """Hydrate resume captain timeline into the seeded zone (G1/G7). Deep-copied."""
        self._seeded_process = copy.deepcopy(list(steps))
        # Seeded steps already lived in journal — skip re-append on flush.
        self._process_cursor.seed_captain(len(self._seeded_process))

    def seed_run_processes(self, run_map: dict[str, list[dict[str, Any]]]) -> None:
        """Hydrate resume worker timelines into the seeded zone (G1/G7). Deep-copied."""
        self._seeded_run_processes = {
            rid: copy.deepcopy(list(steps)) for rid, steps in run_map.items()
        }
        for rid, steps in self._seeded_run_processes.items():
            self._process_cursor.seed_run(rid, len(steps))

    def raw_process(self) -> list[dict[str, Any]]:
        """Seeded ⊕ live captain steps with **no** structural gate (G1 capture).

        Unlike ``process_timeline()``, a pure prose turn still returns its steps —
        suspension snapshots must not drop pre-pause content/reasoning.
        """
        if not self._seeded_process:
            return list(self._process)
        return [*self._seeded_process, *self._process]

    def raw_run_processes(self) -> dict[str, list[dict[str, Any]]]:
        """Seeded ⊕ live worker step maps with **no** empty-map gate (G1 capture)."""
        return self._merged_run_processes()

    def _merged_run_processes(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for rid, steps in self._seeded_run_processes.items():
            if steps:
                out[rid] = list(steps)
        for rid, steps in self._run_processes.items():
            if not steps:
                continue
            prior = out.get(rid)
            out[rid] = [*prior, *steps] if prior else list(steps)
        return out

    def execution_journal(self) -> list[dict[str, Any]] | None:
        has_surface = any(e["type"] in _JOURNAL_SURFACE_TYPES for e in self._journal)
        return self._journal if has_surface else None

    def process_timeline(self) -> list[dict[str, Any]] | None:
        # Persist the timeline whenever it carries STRUCTURE beyond the CEO's own text —
        # a tool, the team graph, or an interaction / 痕迹 marker (checkpoint / ask /
        # plan_review / team_preview / escalation / approval / delegation_authorization /
        # user_interjection).
        # A pure reasoning/content turn needs none (the content scalar IS the answer, and
        # reasoning rides its own column), matching the fold's "tool-less single-agent turn
        # → no process" so live / reload / golden stay aligned.
        # Gate scans seeded⊕live; unseeded path returns the live list by reference
        # (status-quo identity for callers that mutate / compare).
        if self._seeded_process:
            merged = [*self._seeded_process, *self._process]
            structural = any(s.get("kind") not in ("reasoning", "content") for s in merged)
            return merged if structural else None
        structural = any(s.get("kind") not in ("reasoning", "content") for s in self._process)
        return self._process if structural else None

    def run_process_timelines(self) -> dict[str, list[dict[str, Any]]] | None:
        """Per-run ProcessStep[] maps for worker detail timelines (对称 CEO process).

        Persist any non-empty run timeline so reload keeps true interleaving (tools
        between thinking/output). Empty map → None (no field on the runs payload).
        When seeded, returns seeded⊕live merge; otherwise the live map only (status quo).
        """
        if self._seeded_run_processes:
            out = self._merged_run_processes()
            return out or None
        out = {rid: steps for rid, steps in self._run_processes.items() if steps}
        return out or None

    def last_turn_error(self) -> dict[str, Any] | None:
        """Latest ``error`` SSE payload (code/message[/context]), or None.

        ERROR events are transport-only (not journaled / not in ``_history``); this
        is the durable hand-off into ``turn_end`` + settle result for reload.
        """
        return self._last_error

    def streamed_content(self) -> str:
        """The CEO bubble's currently-streamed text — concatenated ``content``-kind
        process entries, honoring ``content_reset`` (reset pops them).

        断线别白干 (中途取消 salvage): the partial reply the user already saw, read off the
        turn's live accumulation so a turn CANCELLED before it persisted keeps that text
        instead of being replaced by a generic「连接中断」note. Empty for a turn that had
        streamed no assistant text yet (e.g. still mid-tool). Accumulates even while
        detached, so a disconnect that later cancels still recovers what streamed.

        Live zone only — seeded pre-pause content must not re-enter salvage joins (G8).
        After ``content_reset`` this is empty until the next delta; use
        :meth:`interrupt_salvage_content` for stop-after-reset salvage.
        """
        return "".join(
            step.get("text", "") for step in self._process if step.get("kind") == "content"
        )

    def interrupt_salvage_content(self) -> str:
        """Body to keep on user stop: live streamed text, else pre-reset stash.

        ``content_reset`` / finish_guard clears the live content lane so the bubble
        can rewrite; if the user stops before a new delta, industry habit is to keep
        whatever already streamed — not an empty shell.
        """
        live = self.streamed_content()
        if live:
            return live
        return self._interrupt_content_stash or ""

    def streamed_reasoning(self) -> str:
        """CEO thinking text accumulated so far (live ``reasoning`` steps only)."""
        return "".join(
            step.get("text", "") for step in self._process if step.get("kind") == "reasoning"
        )

    def stream_memory_snapshot(self) -> dict[str, str]:
        """In-memory stream-channel texts (for error/FAILED salvage merge)."""
        if self._checkpointer is None:
            return {}
        return self._checkpointer.memory_snapshot()

    async def flush_stream_state(self) -> None:
        """Best-effort flush of dirty stream segments (call before turn收口)."""
        if self._checkpointer is not None:
            await self._checkpointer.flush()

    def captain_context(self) -> list[dict[str, Any]] | None:
        from agentcore.runtime.runs.types import RunKind

        captain_run_id: str | None = None
        for e in self._journal:
            payload = e.get("payload") or {}
            if (
                e.get("type") == EventType.RUN_STARTED.value
                and payload.get("kind") == RunKind.CAPTAIN.value
            ):
                captain_run_id = payload.get("run_id")
                break
        if captain_run_id is None:
            return None
        found = False
        blocks: list[dict[str, Any]] = []
        for e in self._journal:
            payload = e.get("payload") or {}
            if (
                e.get("type") == EventType.RUN_CONTEXT.value
                and payload.get("run_id") == captain_run_id
            ):
                found = True
                blocks.extend(payload.get("blocks") or [])
        return blocks if found else None

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
