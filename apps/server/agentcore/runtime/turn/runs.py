"""In-process registry of active chat-turn runs (执行与请求解耦 C1 · slice 1a).

Background — why this exists
---------------------------
A chat turn used to be bound to its SSE request: the route spawned the pipeline as
a ``producer`` task and the SSE layer cancelled it the instant the client
disconnected. That coupling lost the most painful real case (实测案例复盘 案例 1: a
7-minute 法律论文 turn dropped its connection and the whole delivery was thrown
away). To decouple execution from the request, the turn now runs as a *detached*
task tracked here, keyed by ``conversation_id``; the SSE layer only ATTACHES to it.

Resulting lifecycle
-------------------
- **client disconnect** → the SSE stream detaches, but the run keeps going and
  persists its reply in the background (断线不再丢交付).
- **explicit stop** (``POST .../stop``) → :meth:`stop` cancels the run task, which
  unwinds through the turn's existing ``CancelledError`` salvage (finished team
  work is kept as an incomplete message).
- **lifespan shutdown** → :func:`~agentcore.runtime.turn.shutdown.salvage_turns_on_shutdown`
  marks clean-cancel,
  cascade-stops every live run, awaits unwind (grace timeout), then force-releases
  leftover leases — never the sweeper orphan path.
- **normal completion** → the task's done-callback drops it from the registry.

Posture
-------
This registry is the **process-local cache** of live tasks + sinks. Durable RUNNING
ownership lives in ``turn_leases`` (Postgres; heartbeat + owner) so a process restart
no longer silently drops in-flight turns — the lease sweeper claims expired rows and
routes them through ``recover_turn``. Reconnect (``EventSink.subscribe`` /
``unsubscribe``) and stop stay orthogonal to recover. Cross-process Redis lease backend
is a later swap behind the same repository seam.

Every registration also publishes the new run to
:mod:`agentcore.runtime.events.conversation_hub`, so端 following the conversation (not
just the turn) pick it up — see :meth:`TurnRunRegistry.register`.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any, Literal

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import AgentCoreError
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.fulfill.user_signal import (
    TurnActivityDoneReason,
    push_turn_activity_done,
    push_turn_activity_running,
)
from agentcore.runtime.events import EventSink, message_end
from agentcore.runtime.events.types import FinishReason

logger = get_logger(__name__)

ResumeBusyReason = Literal["wrap_up", "live_turn"]


class ResumeClaimUnresolvedError(AgentCoreError):
    """Deferred wake: claim returned None but the paused frame is still on disk.

    Idle ``POST …/resume`` answers 500 + ``resume_claim_failed`` in this case so the
    client retries. The busy-slot wake must fail the waiting SSE the same way — not
    cancel it as if the card were gone (D9 / 幂等续跑).
    """

    code = ErrorCode.PIPELINE_ERROR
    retryable = True
    status_code = 500

    def __init__(self) -> None:
        super().__init__("续跑暂时没接上，请再试一次。")


@dataclass
class TurnRun:
    """A detached chat-turn task and its sink, tracked while it runs."""

    run_id: str
    conversation_id: str
    task: asyncio.Task
    sink: EventSink
    # Account owner — fulfill activity frames are keyed by user, not conversation.
    user_id: str = ""
    # Set by :meth:`mark_user_stop` / :meth:`stop` / :meth:`stop_and_drain` /
    # shutdown salvage before ``task.cancel()`` so the CancelledError handler can
    # tell clean cancel (terminal + release) from true hard kill (orphan lease for
    # sweeper).
    user_stopped: bool = False
    # Newer message took this conversation's slot. Clean-cancel (release lease)
    # but not a user Stop — the interrupt body must not inherit USER_STOP silence.
    superseded: bool = False
    # True once ``task.cancel()`` has been delivered for this run, so a repeat Stop
    # (or an overlap supersede landing on an already-unwinding run) does not deliver
    # a second CancelledError into the turn's teardown ``finally``.
    cancel_requested: bool = False


@dataclass
class ResumeDeferredWaiter:
    """Cold resume waiting for the conversation slot (冷 resume × live deferred).

    Settlement is prewritten before registration. On wake we claim + start
    ``resume_chat``; a still-open SSE receives the sink via ``started`` (else
    detached, same posture as FIFO queue drain).
    """

    conversation_id: str
    message_id: str
    busy_reason: ResumeBusyReason
    checkpoint_response: Any
    user_id: str = ""
    llm_credentials: Any = None
    llm_supports_tools: bool | None = None
    x_client_platform: str | None = None
    # The device that clicked resume: the wake runs off another turn's callback,
    # so it must be re-bound here rather than inherited (see fulfill/origin.py).
    origin_device_id: str | None = None
    # Soft-gate warnings to emit on the resume sink when the slot frees.
    preflight_warnings: list[str] = field(default_factory=list)
    started: asyncio.Future[Any] | None = field(default=None, repr=False)
    # SSE futures adopted from repeat submits of the SAME message_id (幂等 join):
    # they settle together with ``started`` off this one resume run.
    joined: list[asyncio.Future[Any]] = field(default_factory=list, repr=False)

    def waiting(self) -> list[asyncio.Future[Any]]:
        """Every SSE future still waiting on this resume (primary + joined)."""
        return [f for f in (self.started, *self.joined) if f is not None]

    def join(self, other: ResumeDeferredWaiter) -> None:
        """Adopt ``other``'s SSE future — a re-click on the same cold card.

        Both connections are handed the same sink on wake and each subscribes to it, so
        they are peers that see the same frames (the old single-queue sink made them
        split frames instead). Only the first one drains the pre-subscribe handoff
        backlog — preflight warnings; everything after fans out to both.
        """
        fut = other.started
        if fut is None or fut is self.started or fut in self.joined:
            return
        self.joined.append(fut)

    def settle(self, sink: EventSink) -> bool:
        """Hand the resume sink to every waiting SSE; False when none is left."""
        delivered = False
        for fut in self.waiting():
            if not fut.done():
                fut.set_result(sink)
                delivered = True
        return delivered

    def unwind(self) -> None:
        """Cancel every waiting SSE — this resume will never produce a sink."""
        for fut in self.waiting():
            if not fut.done():
                fut.cancel()

    def fail(self, exc: BaseException) -> None:
        """Unblock every waiting SSE with an error (idle resume's 5xx twin)."""
        for fut in self.waiting():
            if not fut.done():
                fut.set_exception(exc)


# Set on an ``asyncio.Task`` when that specific run was cancelled by user stop —
# survives slot replacement so the old task's CancelledError salvage still sees
# :meth:`is_user_stop` as True.
_TASK_USER_STOPPED = "_agentcore_user_stopped"
# Same survival trick for overlap supersede (not a user Stop).
_TASK_SUPERSEDED = "_agentcore_superseded"

# Lifespan finally: graceful shutdown salvage (interrupt + release, never orphan).
# True hard kill / crash without lifespan still orphans for the sweeper.
_shutting_down: bool = False


class TurnRunRegistry:
    """Tracks the active detached run per conversation (one active run each).

    Turns for one conversation are serialized client-side (and by per-mutation
    ``workspace_lock`` sinks), so one active run per ``conversation_id`` is the
    model.     A rare overlap (e.g. a regenerate fired before the prior run cleared)
    cancels the older task (marked superseded, not user-stopped) before the
    newer run takes the slot — otherwise the orphaned task can no longer be
    addressed by :meth:`stop`.
    """

    def __init__(self) -> None:
        self._runs: dict[str, TurnRun] = {}
        # At most one cold-resume deferred waiter per conversation (slot owner next).
        self._resume_deferred: dict[str, ResumeDeferredWaiter] = {}
        # Strong refs to the detached wake tasks (bare create_task lets the loop GC
        # a running task mid-claim).
        self._deferred_tasks: set[asyncio.Task] = set()

    @staticmethod
    def _mark_user_stopped(run: TurnRun) -> None:
        run.user_stopped = True
        setattr(run.task, _TASK_USER_STOPPED, True)

    @staticmethod
    def _emit_user_stop_close(run: TurnRun) -> None:
        """Live ``message_end(cancelled)`` before child drain / salvage.

        UI freezes on click; this frame confirms ``stopping`` → ``stopped``.
        Wave teardown must not block it. Idempotent if salvage emits again.
        """
        sink = run.sink
        if sink._closed or sink._stream_finish_reason is not None:
            return
        with contextlib.suppress(Exception):
            sink.emit(message_end(FinishReason.CANCELLED))

    @staticmethod
    def _mark_superseded(run: TurnRun) -> None:
        run.superseded = True
        setattr(run.task, _TASK_SUPERSEDED, True)

    @staticmethod
    def _cancel_once(run: TurnRun) -> bool:
        """Deliver ``task.cancel()`` at most once per run; True when it was sent.

        A repeat Stop — or an overlap supersede landing on a run that is already
        unwinding — must not re-deliver: the extra ``CancelledError`` lands inside
        the turn's teardown ``finally`` and skips whatever flush / release had not
        run yet. ``task.done()`` cannot guard that window (a task running its
        ``finally`` is not done).
        """
        if run.cancel_requested or run.task.done():
            return False
        run.cancel_requested = True
        run.task.cancel()
        return True

    @staticmethod
    def begin_shutdown_salvage() -> None:
        """Arm process-wide shutdown salvage before cancelling live turns."""
        global _shutting_down
        _shutting_down = True

    @staticmethod
    def end_shutdown_salvage() -> None:
        """Clear the shutdown flag (tests / re-entry)."""
        global _shutting_down
        _shutting_down = False

    @staticmethod
    def is_shutdown_salvage() -> bool:
        """True while lifespan is salvaging in-flight turns."""
        return _shutting_down

    def register(
        self,
        *,
        conversation_id: str,
        task: asyncio.Task,
        sink: EventSink,
        user_id: str = "",
    ) -> str:
        """Track ``task`` as the conversation's active run; returns its ``run_id``.

        Installs a done-callback that drops the run from the registry when it ends —
        only if it is still the registered one, so a newer run is never evicted by an
        older task finishing. An in-flight prior run is cancelled (supersede salvage)
        so overlap cannot leave an unstoppable orphan.
        """
        run_id = new_id()
        existing = self._runs.get(conversation_id)
        if existing is not None and not existing.task.done():
            logger.warning(
                "turn_run.overlap",
                conversation_id=conversation_id,
                existing_run_id=existing.run_id,
                new_run_id=run_id,
            )
            self._mark_superseded(existing)
            with contextlib.suppress(Exception):
                from agentcore.runtime.graph.session.session import (
                    cancel_coordination_on_user_stop,
                )

                cancel_coordination_on_user_stop(conversation_id)
            self._cancel_once(existing)
        self._runs[conversation_id] = TurnRun(
            run_id=run_id,
            conversation_id=conversation_id,
            task=task,
            sink=sink,
            user_id=user_id,
        )
        task.add_done_callback(lambda _t: self._discard(conversation_id, run_id))
        # 对话级订阅 (云对话多端同权 B2): every turn start funnels through here, so this is
        # where端 parked on the conversation (idle second device, another window) learn a
        # new run exists — send / FIFO drain / cold-resume wake / stage_card alike.
        try:
            from agentcore.runtime.events.conversation_hub import conversation_streams

            conversation_streams.publish_run(conversation_id, sink)
        except Exception:  # noqa: BLE001 — a观察端 must never break turn registration
            logger.exception(
                "turn_run.conversation_publish_failed",
                conversation_id=conversation_id,
                run_id=run_id,
            )
        _emit_activity_running(user_id, conversation_id)
        return run_id

    def _discard(self, conversation_id: str, run_id: str) -> None:
        current = self._runs.get(conversation_id)
        if current is None or current.run_id != run_id:
            return
        user_id = current.user_id
        reason = activity_done_reason(current)
        del self._runs[conversation_id]
        # Cold resume deferred owns the next slot ahead of FIFO — drain 接棒, no done.
        waiter = self._resume_deferred.pop(conversation_id, None)
        if waiter is not None:
            self._arm_resume_deferred_start(waiter)
            return
        try:
            from .queue import turn_queue

            if turn_queue.depth(conversation_id) > 0:
                turn_queue.schedule_drain(conversation_id)
                return
        except Exception:  # noqa: BLE001 — queue drain must not break done-callback
            logger.exception(
                "turn_run.queue_drain_failed",
                conversation_id=conversation_id,
            )
        # 槽空才 done：queued / cold-resume takeover already returned above.
        _emit_activity_done(user_id, conversation_id, reason)

    def busy_reason_for_resume(
        self, conversation_id: str, message_id: str
    ) -> ResumeBusyReason | None:
        """``wrap_up`` / ``live_turn`` when a live task holds the slot; else ``None``."""
        run = self._runs.get(conversation_id)
        if run is None or run.task.done():
            return None
        if run.sink.message_id == message_id:
            return "wrap_up"
        return "live_turn"

    def has_resume_deferred(self, conversation_id: str) -> bool:
        return conversation_id in self._resume_deferred

    def resume_deferred_message_id(self, conversation_id: str) -> str | None:
        """The parked cold card's ``message_id`` (``None`` when nothing is parked).

        Lets the resume route recognise a repeat submit of the same card and skip a
        second settlement prewrite before it joins (运行时三模型与挂起 · 重复提交).
        """
        waiter = self._resume_deferred.get(conversation_id)
        return waiter.message_id if waiter is not None else None

    def register_resume_deferred(self, waiter: ResumeDeferredWaiter) -> ResumeDeferredWaiter:
        """Park a cold resume until the slot frees (or start immediately if already idle).

        Same ``message_id`` → **幂等 join**: the parked waiter keeps the slot and
        adopts this submit's SSE future, so a double-click shares one resume run and
        the first stream is never cut. Last-click-wins only applies to *another* cold
        card of the same conversation — then the prior SSE is cancelled so it unwinds.
        Returns the waiter that owns the slot (the parked one on join).
        """
        prior = self._resume_deferred.get(waiter.conversation_id)
        if prior is not None and prior is not waiter:
            if prior.message_id == waiter.message_id:
                prior.join(waiter)
                logger.info(
                    "resume.deferred_joined",
                    conversation_id=waiter.conversation_id,
                    message_id=waiter.message_id,
                    busy_reason=prior.busy_reason,
                    waiting=len(prior.waiting()),
                )
                return prior
            prior.unwind()
        self._resume_deferred[waiter.conversation_id] = waiter
        logger.info(
            "resume.deferred",
            conversation_id=waiter.conversation_id,
            message_id=waiter.message_id,
            busy_reason=waiter.busy_reason,
        )
        existing = self._runs.get(waiter.conversation_id)
        if existing is None or existing.task.done():
            taken = self._resume_deferred.pop(waiter.conversation_id, None)
            if taken is waiter:
                self._arm_resume_deferred_start(taken)
        return waiter

    def _arm_resume_deferred_start(self, waiter: ResumeDeferredWaiter) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "resume.deferred_started",
                conversation_id=waiter.conversation_id,
                message_id=waiter.message_id,
                armed=False,
                reason="no_running_loop",
            )
            return
        loop.call_soon(lambda: self._spawn_resume_deferred(waiter))

    def _spawn_resume_deferred(self, waiter: ResumeDeferredWaiter) -> None:
        task = asyncio.create_task(self._start_resume_deferred(waiter))
        self._deferred_tasks.add(task)
        task.add_done_callback(self._deferred_tasks.discard)

    def _repark_resume_deferred(self, waiter: ResumeDeferredWaiter) -> None:
        """Slot re-taken between arm and run: park again without evicting a newer card.

        Registration during the arm window is the later click: same ``message_id``
        joins into it, a different card supersedes this one (its SSE unwinds).
        """
        current = self._resume_deferred.get(waiter.conversation_id)
        if current is None or current is waiter:
            self._resume_deferred[waiter.conversation_id] = waiter
            return
        if current.message_id == waiter.message_id:
            current.join(waiter)
            return
        waiter.unwind()

    async def _start_resume_deferred(self, waiter: ResumeDeferredWaiter) -> None:
        """Claim the paused frame and start resume_chat, settling the waiter either way.

        Detached from the request: nothing else can settle this waiter, so a claim
        that raises must still fail the SSE futures (else that「继续」spins forever)
        and hand the freed slot back to the FIFO queue.
        """
        try:
            await self._drive_resume_deferred(waiter)
        except asyncio.CancelledError:
            self._abandon_resume_deferred(waiter)
            raise
        except Exception as e:  # noqa: BLE001 — detached: no caller left to settle it
            logger.exception(
                "resume.deferred_failed",
                conversation_id=waiter.conversation_id,
                message_id=waiter.message_id,
                error=str(e),
            )
            self._abandon_resume_deferred(waiter, error=e)

    def _abandon_resume_deferred(
        self,
        waiter: ResumeDeferredWaiter,
        *,
        error: BaseException | None = None,
    ) -> None:
        """Unblock the waiting SSE(s) and let FIFO take the slot this wake gave up.

        ``error`` is the idle-route 5xx twin (claim/start raised). Shutdown cancel
        still ``unwind``s — there is no client left to retry this Future.
        """
        if error is not None:
            waiter.fail(error)
        else:
            waiter.unwind()
        try:
            from .queue import turn_queue

            turn_queue.schedule_drain(waiter.conversation_id)
        except Exception:  # noqa: BLE001 — drain must not mask the wake failure
            logger.exception(
                "turn_run.queue_drain_failed",
                conversation_id=waiter.conversation_id,
            )

    async def _finish_deferred_claim_miss(self, waiter: ResumeDeferredWaiter) -> None:
        """Claim returned None: same fork as idle ``POST …/resume``.

        Frame still on disk → 5xx so the client retries (not a silent cancel).
        Frame gone + this message still running → join that sink (幂等).
        Frame gone and no live run → unwind; a later POST gets ``resume_settled``.
        """
        from agentcore.runtime.suspension.persistence import paused_turn_exists

        from .queue import turn_queue

        try:
            still_there = await paused_turn_exists(
                waiter.message_id, conversation_id=waiter.conversation_id
            )
        except Exception as e:  # noqa: BLE001 — exists raises on DB fault; 5xx not「已处理」
            logger.warning(
                "resume.claim_unresolved",
                conversation_id=waiter.conversation_id,
                message_id=waiter.message_id,
                phase="deferred_exists",
                error=str(e),
            )
            waiter.fail(e)
            turn_queue.schedule_drain(waiter.conversation_id)
            return
        if still_there:
            logger.warning(
                "resume.claim_unresolved",
                conversation_id=waiter.conversation_id,
                message_id=waiter.message_id,
                phase="deferred",
            )
            waiter.fail(ResumeClaimUnresolvedError())
            turn_queue.schedule_drain(waiter.conversation_id)
            return
        existing_run = self._runs.get(waiter.conversation_id)
        live_sink = (
            existing_run.sink
            if existing_run is not None
            and not existing_run.task.done()
            and existing_run.sink.message_id == waiter.message_id
            else None
        )
        if live_sink is not None:
            waiter.settle(live_sink)
            return
        logger.warning(
            "resume.deferred_started",
            conversation_id=waiter.conversation_id,
            message_id=waiter.message_id,
            claimed=False,
        )
        waiter.unwind()
        turn_queue.schedule_drain(waiter.conversation_id)

    async def _drive_resume_deferred(self, waiter: ResumeDeferredWaiter) -> None:
        from agentcore.runtime.events import turn_warning
        from agentcore.runtime.suspension.persistence import claim_paused_turn
        from agentcore.runtime.turn.driver import get_turn_driver

        logger.info(
            "resume.deferred_started",
            conversation_id=waiter.conversation_id,
            message_id=waiter.message_id,
            busy_reason=waiter.busy_reason,
        )
        # Another turn may have claimed the slot between arm and run.
        existing = self._runs.get(waiter.conversation_id)
        if existing is not None and not existing.task.done():
            self._repark_resume_deferred(waiter)
            return

        # The deferred wake is this card's claim winner, so it states the conclusion it
        # is about to apply — the settlement was prewritten back when the click arrived,
        # but only the claim can say「这张卡是这一次、这台设备结的」.
        suspension = await claim_paused_turn(
            waiter.message_id,
            conversation_id=waiter.conversation_id,
            decision=str(waiter.checkpoint_response.decision),
            settled_by=waiter.origin_device_id or "",
        )
        if suspension is None:
            await self._finish_deferred_claim_miss(waiter)
            return

        # Bound up-front (the pipeline re-binds the same id later): the registry's
        # ``sink.message_id`` is how a repeat submit of this cold card recognises its
        # own continuation and joins it instead of racing a second claim.
        sink = EventSink(message_id=waiter.message_id)
        for warning in waiter.preflight_warnings:
            sink.emit(turn_warning(warning))

        if not waiter.settle(sink):
            sink.note_no_consumer(reason="resume_deferred_no_waiter")

        from agentcore.fulfill.origin import origin_device

        with origin_device(waiter.origin_device_id):
            task = asyncio.create_task(
                get_turn_driver().resume_turn(
                    suspension=suspension,
                    response=waiter.checkpoint_response,
                    sink=sink,
                    llm_credentials=waiter.llm_credentials,
                    llm_supports_tools=waiter.llm_supports_tools,
                    x_client_platform=waiter.x_client_platform,
                )
            )
        self.register(
            conversation_id=waiter.conversation_id,
            task=task,
            sink=sink,
            user_id=waiter.user_id,
        )

    def get(self, conversation_id: str) -> TurnRun | None:
        """The conversation's active run, or ``None`` if nothing is running."""
        return self._runs.get(conversation_id)

    def mark_user_stop(self, conversation_id: str) -> bool:
        """Record 用户主动停止 **before** anything cancels (stop / regenerate routes).

        Both routes orphan hot pending interactions first, and that pass awaits the
        DB while cancelling their Futures — with ≥2 pending, the awaiter unwinds on
        the first cancel, mid-pass. If the flag were still unset there,
        :meth:`is_clean_cancel` would read False and the turn would orphan its lease
        (bubble「中断」instead of「已停止」, and the sweeper re-drives the very turn
        the user just stopped). Returns ``False`` when nothing is running.
        """
        run = self._runs.get(conversation_id)
        if run is None or run.task.done():
            return False
        self._mark_user_stopped(run)
        self._emit_user_stop_close(run)
        return True

    def stop(self, conversation_id: str) -> bool:
        """Hard-cancel the conversation's active run (explicit ``POST .../stop``).

        Returns ``True`` if a live run was found and signalled, ``False`` when
        nothing is running (already finished / never started) — so the endpoint is
        idempotent. Cancelling drives the turn through its ``CancelledError`` salvage
        (finished work kept as an incomplete message), then the done-callback clears
        the slot.

        Marks ``user_stopped`` before cancel so lease handling releases (terminal)
        instead of orphaning for sweeper reclaim. Also cascade-cancels any live
        coordination drive + in-flight workers (SSE disconnect must NOT use this
        path — detach-and-continue stays on ``release_turn_coordination`` alone).
        Cancel is delivered once per run (:meth:`_cancel_once`): a second click while
        the turn is still unwinding reports ``True`` without re-cancelling.
        """
        run = self._runs.get(conversation_id)
        if run is None or run.task.done():
            return False
        self._mark_user_stopped(run)
        self._emit_user_stop_close(run)
        with contextlib.suppress(Exception):
            from agentcore.runtime.graph.session.session import (
                cancel_coordination_on_user_stop,
            )

            cancel_coordination_on_user_stop(conversation_id)
        signalled = self._cancel_once(run)
        logger.info(
            "turn_run.stop",
            conversation_id=conversation_id,
            run_id=run.run_id,
            signalled=signalled,
        )
        return True

    def is_user_stop(self, conversation_id: str) -> bool:
        """True when the current (or still-unwinding) run is a hard 停止.

        Checks the calling task first so a stop-cancelled task still salvages as
        user-stop after a newer run has taken the registry slot. Overlap
        supersede is :meth:`is_superseded`, not this flag.
        """
        task = asyncio.current_task()
        if task is not None and getattr(task, _TASK_USER_STOPPED, False):
            return True
        run = self._runs.get(conversation_id)
        return bool(run is not None and run.user_stopped)

    def is_superseded(self, conversation_id: str) -> bool:
        """True when this task was squeezed out by a newer message on the slot."""
        task = asyncio.current_task()
        if task is not None and getattr(task, _TASK_SUPERSEDED, False):
            return True
        run = self._runs.get(conversation_id)
        return bool(run is not None and run.superseded)

    def is_clean_cancel(self, conversation_id: str) -> bool:
        """True when CancelledError should terminal-close + release (not orphan).

        Covers explicit ``/stop``, overlap supersede, and lifespan shutdown
        salvage. True hard kill without lifespan still orphans.
        """
        return (
            self.is_user_stop(conversation_id)
            or self.is_superseded(conversation_id)
            or self.is_shutdown_salvage()
        )

    def live_runs(self) -> list[TurnRun]:
        """Snapshot of runs whose tasks are still not done."""
        return [run for run in self._runs.values() if not run.task.done()]

    async def drain(self, conversation_id: str, *, timeout: float = 10.0) -> bool:
        """Wait for any live run to finish WITHOUT cancelling it.

        Kept for callers that still need a short residual-unwind wait (e.g. stage_card
        interactions). Cold ``POST .../resume`` no longer 409s on busy — it registers a
        deferred waiter instead (see :meth:`register_resume_deferred`).
        Returns ``True`` when the slot is clear; ``False`` when a live task remains.
        """
        run = self._runs.get(conversation_id)
        if run is None or run.task.done():
            return True
        logger.info(
            "turn_run.drain",
            conversation_id=conversation_id,
            run_id=run.run_id,
            timeout=timeout,
        )
        await asyncio.wait({run.task}, timeout=timeout)
        run = self._runs.get(conversation_id)
        return run is None or run.task.done()

    async def stop_and_drain(self, conversation_id: str, *, timeout: float = 10.0) -> bool:
        """Cancel the conversation's live run AND await its unwind (explicit ``/stop``).

        Fire-and-forget :meth:`stop` signals cancel; this waits for unwind so the
        caller can proceed without racing an in-flight task. Resume uses
        :meth:`drain` instead — it must not cancel a D9 in-flight new turn.
        Returns ``True`` if a live run was found and signalled.
        """
        run = self._runs.get(conversation_id)
        if run is None or run.task.done():
            return False
        task = run.task
        # Same flag as :meth:`stop` — must mark before cancel.
        if not self.stop(conversation_id):
            return False
        # ``asyncio.wait`` never re-raises the task's (CancelledError) result; a timeout just
        # means it is still unwinding.
        await asyncio.wait({task}, timeout=timeout)
        return True

    async def stop_all_and_drain(self, *, timeout: float = 20.0) -> list[TurnRun]:
        """Cancel every live turn like ``/stop`` and await unwind up to ``timeout``.

        Returns runs still live after the wait — caller must force-close + release
        their leases (graceful shutdown must not fall through to orphan).
        """
        live = self.live_runs()
        if not live:
            return []
        tasks: list[asyncio.Task] = []
        for run in live:
            self._mark_user_stopped(run)
            with contextlib.suppress(Exception):
                from agentcore.runtime.graph.session.session import (
                    cancel_coordination_on_user_stop,
                )

                cancel_coordination_on_user_stop(run.conversation_id)
            if not run.task.done():
                self._cancel_once(run)
                tasks.append(run.task)
            logger.info(
                "turn_run.shutdown_salvage",
                conversation_id=run.conversation_id,
                run_id=run.run_id,
                message_id=run.sink.message_id,
            )
        if tasks:
            await asyncio.wait(set(tasks), timeout=timeout)
        return [run for run in live if not run.task.done()]


def activity_done_reason(run: TurnRun) -> TurnActivityDoneReason:
    """Map a finished run to the account-level ``ai_turn_activity`` done reason."""
    if run.user_stopped:
        return "stopped"
    finish = getattr(run.sink, "_stream_finish_reason", None)
    if finish == FinishReason.PAUSED.value:
        return "paused"
    if finish in {FinishReason.ERROR.value, FinishReason.INTERRUPTED.value}:
        return "error"
    if finish == FinishReason.CANCELLED.value:
        return "stopped"
    task = run.task
    if task.done() and not task.cancelled():
        try:
            if task.exception() is not None:
                return "error"
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass
    return "completed"


def _emit_activity_running(user_id: str, conversation_id: str) -> None:
    if not user_id or not conversation_id:
        return
    try:
        push_turn_activity_running(user_id=user_id, conversation_id=conversation_id)
    except Exception:  # noqa: BLE001 — activity must never break registration
        logger.exception(
            "turn_run.activity_running_failed",
            conversation_id=conversation_id,
        )


def _emit_activity_done(
    user_id: str, conversation_id: str, reason: TurnActivityDoneReason
) -> None:
    if not user_id or not conversation_id:
        return
    try:
        push_turn_activity_done(
            user_id=user_id, conversation_id=conversation_id, reason=reason
        )
    except Exception:  # noqa: BLE001 — activity must never break the done-callback
        logger.exception(
            "turn_run.activity_done_failed",
            conversation_id=conversation_id,
        )


# Module-level singleton (single-worker posture, as approvals / interaction).
turn_runs = TurnRunRegistry()
