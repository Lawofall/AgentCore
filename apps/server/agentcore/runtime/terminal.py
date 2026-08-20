"""Turn/run terminal-state read path — the only place to look first.

查「这个回合 / 这个 run 处于什么终态」从本模块开始。三层事实不要压成一种：

1. **节点关没关** — :class:`~agentcore.runtime.runs.types.RunPhase` /
   ``TERMINAL_PHASES`` / :func:`is_run_phase_terminal`
   调度器：这个 DAG 节点是否已完成，波能否推进。
2. **流怎么收口** — :class:`~agentcore.runtime.events.types.FinishReason`
   （含 ``PAUSED``）。``message_end.finish_reason``。``PAUSED`` 是收口但不是
   ``END_TURN``、也不是 ``CANCELLED``（闸卡挂起，回合并未做完）。
3. **产出好不好** — ``TurnOutcome`` / :func:`resolve_turn_outcome`
   ``ok | partial | paused | error``。闸卡挂起时为 ``None``；限流可续跑会显式盖
   ``paused``。

Run 关帧事件是**有意不同的集合**（格：product ⊂ stream-flush ⊂ close），禁止并成一个：

- :data:`RUN_CLOSE_EVENT_TYPES` — occupancy / first-wins（live sink、journal
  team_batch、coordination 耐久关帧）。completed / failed / cancelled / skipped。
- :data:`RUN_PRODUCT_EVENT_TYPES` — 可能带可重建产出（journal fold splice、
  attach replay）。仅 completed / failed。skipped 从未跑；cancelled 的 salvage
  走另一条路，fold 不在此拼 delta。
- :data:`RUN_STREAM_FLUSH_EVENT_TYPES` — 流式 checkpointer 冲刷边。completed /
  failed / cancelled（skipped 没有缓冲 delta）。

专门的中止收口（user stop / lease salvage）仍看 ``runtime.turn.interrupt``，
不要把它的 ``CANCELLED|INTERRUPTED`` 当成通用「回合终态」。

``RunPhase`` / ``TERMINAL_PHASES`` are lazy: this module sits on the EventSink
import path, and ``runtime.runs`` package init would otherwise cycle through
approvals → events → sink → here.
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.events.types import EventType, FinishReason
from agentcore.runtime.turn.outcome import (
    PRODUCED_OUTCOMES,
    TurnOutcome,
    coerce_produced_outcome,
    resolve_turn_outcome,
)

# Occupancy: a run_id may enter one of these faces once. Scheduler-terminal
# phases and these close frames are the same four outcomes.
RUN_CLOSE_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.RUN_COMPLETED,
        EventType.RUN_FAILED,
        EventType.RUN_CANCELLED,
        EventType.RUN_SKIPPED,
    }
)

# Product-bearing close: reconstructed output/thinking is spliced only here.
RUN_PRODUCT_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.RUN_COMPLETED,
        EventType.RUN_FAILED,
    }
)

# Stream-channel flush edges (started writing, then closed). Skipped never wrote.
RUN_STREAM_FLUSH_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.RUN_COMPLETED,
        EventType.RUN_FAILED,
        EventType.RUN_CANCELLED,
    }
)

assert RUN_PRODUCT_EVENT_TYPES <= RUN_STREAM_FLUSH_EVENT_TYPES <= RUN_CLOSE_EVENT_TYPES


def _terminal_phases() -> frozenset[Any]:
    from agentcore.runtime.runs.types import TERMINAL_PHASES

    return TERMINAL_PHASES


def is_run_phase_terminal(phase: object) -> bool:
    """True when a DAG node is done (scheduler may advance past it)."""
    return phase in _terminal_phases()


def is_run_close_event(event_type: object) -> bool:
    """True when ``event_type`` is an occupancy close frame (all four faces)."""
    return event_type in RUN_CLOSE_EVENT_TYPES


def is_run_product_event(event_type: object) -> bool:
    """True when ``event_type`` may carry reconstructed worker output."""
    return event_type in RUN_PRODUCT_EVENT_TYPES


def is_run_stream_flush_event(event_type: object) -> bool:
    """True when the stream checkpointer should flush at this run close."""
    return event_type in RUN_STREAM_FLUSH_EVENT_TYPES


def is_gate_pause_finish(finish_reason: object) -> bool:
    """True when the stream closed as a gate pause (outcome stays ``None``)."""
    raw = getattr(finish_reason, "value", finish_reason)
    return raw == FinishReason.PAUSED.value


def __getattr__(name: str) -> Any:
    if name in {"TERMINAL_PHASES", "RunPhase"}:
        from agentcore.runtime.runs.types import TERMINAL_PHASES, RunPhase

        return TERMINAL_PHASES if name == "TERMINAL_PHASES" else RunPhase
    raise AttributeError(f"module {__name__!r} has no attribute {name}")


__all__ = [
    "FinishReason",
    "PRODUCED_OUTCOMES",
    "RUN_CLOSE_EVENT_TYPES",
    "RUN_PRODUCT_EVENT_TYPES",
    "RUN_STREAM_FLUSH_EVENT_TYPES",
    "TurnOutcome",
    "coerce_produced_outcome",
    "is_gate_pause_finish",
    "is_run_close_event",
    "is_run_phase_terminal",
    "is_run_product_event",
    "is_run_stream_flush_event",
    "resolve_turn_outcome",
]
