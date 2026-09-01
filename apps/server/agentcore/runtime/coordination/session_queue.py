"""Coordination event queue, wait/coalesce, and close.

Split from ``session.py`` — pure move. Worker / timeout / snapshot stay on
their mixins; this mixin is the in-process event pipe.
"""

# mypy: disable-error-code="misc"

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.runtime.coordination.session_types import (
    CoordinationEvent,
    CoordinationEventKind,
)

if TYPE_CHECKING:
    from agentcore.runtime.coordination.session import CoordinationSession

logger = get_logger("agentcore.runtime.coordination.session")


class SessionQueueMixin:
    """Post / wait / drain the coordination event queue; close."""

    _pending: list[CoordinationEvent]

    def post(self: CoordinationSession, event: CoordinationEvent) -> bool:
        """Enqueue ``event``. Returns False when dropped (inactive / escalation dedupe)."""
        if not self.active and event.kind not in (
            CoordinationEventKind.ALL_COMPLETED,
            CoordinationEventKind.DRIVE_CANCELLED,
        ):
            return False
        if event.kind is CoordinationEventKind.ESCALATION:
            key = (
                f"{event.payload.get('run_id') or ''}|"
                f"{event.payload.get('kind') or ''}|"
                f"{(event.payload.get('question') or event.payload.get('summary') or '')[:120]}"
            )
            if key in self._escalation_keys:
                return False
            self._escalation_keys.add(key)
        if event.kind in (
            CoordinationEventKind.ALL_COMPLETED,
            CoordinationEventKind.DRIVE_CANCELLED,
        ):
            self.terminal_posted = True
        if event.kind is CoordinationEventKind.DRIVE_CANCELLED:
            self.drive_cancelled = True
        self._queue.put_nowait(event)
        logger.debug(
            "coordination.event_posted",
            kind=event.kind.value,
            execution_id=self.execution_id,
        )
        return True

    async def wait_events(
        self: CoordinationSession,
        *,
        timeout: float | None = None,
        merge_idle: float = 0.05,
    ) -> list[CoordinationEvent]:
        """Wait for at least one event; briefly coalesce follow-ups (cost merge).

        Also consumes ``_pending`` (events drained by ``snapshot`` while this wait
        was blocked). Drain sets ``_wake`` so we do not sit on an empty queue until
        the full timeout.
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout

        while True:
            if self._pending:
                batch = self._pending
                self._pending = []
                return batch

            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                return []

            # Clear-then-recheck: avoid losing a wake that lands between clear and wait.
            self._wake.clear()
            if self._pending:
                batch = self._pending
                self._pending = []
                return batch

            get_task = asyncio.create_task(self._queue.get())
            wake_task = asyncio.create_task(self._wake.wait())
            done, pending_tasks = await asyncio.wait(
                {get_task, wake_task},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending_tasks:
                task.cancel()
            for task in pending_tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

            if get_task in done and not get_task.cancelled():
                try:
                    first = get_task.result()
                except (asyncio.CancelledError, Exception):
                    first = None
                if first is not None:
                    batch = [first]
                    if self._pending:
                        batch.extend(self._pending)
                        self._pending = []
                    # Short coalesce window so independent mid-wave completions can merge.
                    coalesce_deadline = loop.time() + merge_idle
                    while True:
                        left = coalesce_deadline - loop.time()
                        if left <= 0:
                            break
                        try:
                            nxt = await asyncio.wait_for(self._queue.get(), timeout=left)
                        except TimeoutError:
                            break
                        batch.append(nxt)
                        if nxt.kind in (
                            CoordinationEventKind.ALL_COMPLETED,
                            CoordinationEventKind.DRIVE_CANCELLED,
                        ):
                            break
                    if self._pending:
                        batch.extend(self._pending)
                        self._pending = []
                    return batch

            # Woken by drain → loop and take ``_pending``. Pure timeout → empty.
            if wake_task not in done and not self._pending:
                return []

    def drain_nowait(self: CoordinationSession) -> list[CoordinationEvent]:
        batch = list(self._pending)
        self._pending = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    def has_unread_user_interjection(self: CoordinationSession) -> bool:
        """True when a USER_INTERJECTION is queued and not yet consumed this wait.

        Moves queue items into ``_pending`` (same as snapshot drain) so a later
        ``drain_nowait`` / ``wait_events`` still sees them.
        """
        self._drain_queue_copy()
        return any(
            ev.kind is CoordinationEventKind.USER_INTERJECTION for ev in self._pending
        )

    def _drain_queue_copy(self: CoordinationSession) -> list[CoordinationEvent]:
        """Non-destructive peek is unavailable on Queue — drain into pending + wake."""
        drained: list[CoordinationEvent] = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        self._pending.extend(drained)
        if drained:
            self._wake.set()
        return list(drained)

    def close(self: CoordinationSession) -> None:
        was_active = self.active
        self.active = False
        self.cancel_all_timeouts()
        # 未消化插话升格对话 FIFO（或终局已答 → addressed）。仅从 active→inactive
        # 触发一次，避免重复 close 双入队。
        if was_active and self.pending_interjections:
            from agentcore.runtime.coordination.interjections import (
                promote_pending_on_close,
            )

            promote_pending_on_close(self)
