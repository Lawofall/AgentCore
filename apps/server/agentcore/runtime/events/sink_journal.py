"""DURABLE journal persist path for EventSink.

Split from ``sink.py`` — pure move. Live SSE / history / process lanes stay on
the sink; this mixin only appends host/execution journal facts and the
in-memory ``_journal`` snapshot used by reload / settle.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentcore.runtime.events.journal_config import (
    _JOURNAL_EVENT_TYPES,
    _JOURNAL_SURFACE_TYPES,
    journal_payload_for_persist,
)
from agentcore.runtime.events.types import EventType, SSEEvent
from agentcore.runtime.facts import Fact, current_fact_log, record_turn_fact


class SinkJournalMixin:
    """Append DURABLE facts to the turn/host journal (sink-lifetime independent)."""

    _journal: list[dict[str, Any]]
    _message_id: str | None
    _conversation_id: str | None

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

    def seed_journal(self, events: list[dict[str, Any]]) -> None:
        self._journal.extend(events)

    def execution_journal(self) -> list[dict[str, Any]] | None:
        has_surface = any(e["type"] in _JOURNAL_SURFACE_TYPES for e in self._journal)
        return self._journal if has_surface else None

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
