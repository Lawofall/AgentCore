"""StreamCheckpointer — single writer for in-flight stream-channel snapshots (P1).

Accumulates four channel families (captain content/reasoning, per-run output/reasoning)
and flushes via ``ConversationStore.upsert_stream_segments`` on 3s / 4KB / semantic
boundaries. Segment write failures degrade to memory + log — never interrupt the
pipeline (流式回复持久化 §3.2).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agentcore.core.logging import get_logger
from agentcore.runtime.events.types import EventType, SSEEvent
from agentcore.runtime.terminal import RUN_STREAM_FLUSH_EVENT_TYPES

logger = get_logger(__name__)

FLUSH_INTERVAL_S = 3.0
FLUSH_BYTES = 4096

CHANNEL_CAPTAIN_CONTENT = "captain:content"
CHANNEL_CAPTAIN_REASONING = "captain:reasoning"


def run_output_channel(run_id: str) -> str:
    return f"run:{run_id}:output"


def run_reasoning_channel(run_id: str) -> str:
    return f"run:{run_id}:reasoning"


def parse_run_channel(channel: str) -> tuple[str, str] | None:
    """Return ``(run_id, kind)`` for ``run:{id}:output|reasoning``, else ``None``."""
    if not channel.startswith("run:"):
        return None
    rest = channel[4:]
    if rest.endswith(":output"):
        return rest[: -len(":output")], "output"
    if rest.endswith(":reasoning"):
        return rest[: -len(":reasoning")], "reasoning"
    return None


@dataclass
class _ChannelAcc:
    text: str = ""
    generation: int = 0
    dirty: bool = False


@dataclass
class StreamCheckpointer:
    """Per-turn stream-segment accumulator + flusher (EventSink side)."""

    turn_id: str
    _channels: dict[str, _ChannelAcc] = field(default_factory=dict)
    _dirty_bytes: int = 0
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    _flush_inflight: asyncio.Task[None] | None = field(default=None, repr=False)
    _closed: bool = False
    # run_id → agent_id (from run_started) for overlay attribution
    _run_agent_ids: dict[str, str] = field(default_factory=dict)
    _captain_run_id: str | None = None

    def start(self) -> None:
        if self._task is not None or self._closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self._flush_loop())

    def observe(self, event: SSEEvent) -> None:
        """Fold one SSE event into channel accumulators; schedule boundary flushes."""
        if self._closed:
            return
        t = event.type
        payload = event.payload

        if t == EventType.RUN_STARTED:
            run_id = payload.get("run_id")
            if not run_id:
                return
            if payload.get("kind") == "captain":
                self._captain_run_id = run_id
            else:
                self._run_agent_ids[run_id] = payload.get("agent_id") or ""
            return

        if t == EventType.CONTENT_DELTA:
            self._append(CHANNEL_CAPTAIN_CONTENT, payload.get("delta") or "")
            return
        if t == EventType.REASONING_DELTA:
            self._append(CHANNEL_CAPTAIN_REASONING, payload.get("delta") or "")
            return
        if t == EventType.RUN_OUTPUT_DELTA:
            run_id = payload.get("run_id")
            if run_id:
                self._append(run_output_channel(run_id), payload.get("delta") or "")
            return
        if t == EventType.RUN_REASONING_DELTA:
            run_id = payload.get("run_id")
            if run_id:
                self._append(run_reasoning_channel(run_id), payload.get("delta") or "")
            return

        if t == EventType.CONTENT_RESET:
            self._reset(CHANNEL_CAPTAIN_CONTENT)
            self.flush_now()
            return
        if t == EventType.RUN_OUTPUT_RESET:
            run_id = payload.get("run_id")
            if run_id:
                self._reset(run_output_channel(run_id))
                self.flush_now()
            return

        # Semantic boundaries — flush before / at terminal edges.
        if t == EventType.TOOL_USE_START:
            self.flush_now()
            return
        if t in RUN_STREAM_FLUSH_EVENT_TYPES:
            self.flush_now()
            return

    def _acc(self, channel: str) -> _ChannelAcc:
        acc = self._channels.get(channel)
        if acc is None:
            acc = _ChannelAcc()
            self._channels[channel] = acc
        return acc

    def _append(self, channel: str, delta: str) -> None:
        if not delta:
            return
        acc = self._acc(channel)
        acc.text += delta
        acc.dirty = True
        self._dirty_bytes += len(delta.encode("utf-8"))
        if self._dirty_bytes >= FLUSH_BYTES:
            self.flush_now()

    def _reset(self, channel: str) -> None:
        acc = self._acc(channel)
        acc.generation += 1
        acc.text = ""
        acc.dirty = True
        # Reset does not add dirty bytes; still force a flush so the empty gen lands.

    def memory_snapshot(self) -> dict[str, str]:
        """In-memory channel → text (for salvage merge; includes non-dirty)."""
        return {ch: acc.text for ch, acc in self._channels.items() if acc.text}

    def run_agent_ids(self) -> dict[str, str]:
        return dict(self._run_agent_ids)

    def flush_now(self) -> None:
        """Schedule a non-blocking best-effort flush."""
        if self._closed or not self.turn_id:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._flush_inflight is not None and not self._flush_inflight.done():
            return
        self._flush_inflight = loop.create_task(self.flush())

    async def flush(self) -> None:
        """UPSERT all dirty channels in one store call (best-effort)."""
        dirty = [
            (ch, acc.text, acc.generation)
            for ch, acc in self._channels.items()
            if acc.dirty
        ]
        if not dirty:
            return
        try:
            from agentcore.conversation.store import get_conversation_store

            await get_conversation_store().upsert_stream_segments(
                turn_id=self.turn_id,
                segments=dirty,
            )
            for ch, _, _ in dirty:
                acc = self._channels.get(ch)
                if acc is not None:
                    acc.dirty = False
            self._dirty_bytes = 0
        except Exception as e:
            logger.warning(
                "chat.stream_segment_flush_failed",
                turn_id=self.turn_id,
                channels=len(dirty),
                error=str(e),
            )

    async def _flush_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(FLUSH_INTERVAL_S)
                if self._closed:
                    break
                await self.flush()
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        """Final flush then stop the timer (call before turn teardown)."""
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            self._task = None
        await self.flush()
