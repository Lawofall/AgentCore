"""Unified terminal-state read path: three layers, named close-event sets."""

from __future__ import annotations

from agentcore.runtime.events.types import EventType, FinishReason
from agentcore.runtime.runs.types import TERMINAL_PHASES, RunPhase
from agentcore.runtime.terminal import (
    RUN_CLOSE_EVENT_TYPES,
    RUN_PRODUCT_EVENT_TYPES,
    RUN_STREAM_FLUSH_EVENT_TYPES,
    is_gate_pause_finish,
    is_run_close_event,
    is_run_phase_terminal,
    is_run_product_event,
    is_run_stream_flush_event,
    resolve_turn_outcome,
)
from agentcore.runtime.turn.outcome import resolve_turn_outcome as resolve_from_outcome


def test_close_sets_keep_intentional_lattice():
    """product ⊂ stream-flush ⊂ occupancy close — do not merge them."""
    assert {e.value for e in RUN_PRODUCT_EVENT_TYPES} == {
        "run_completed",
        "run_failed",
    }
    assert {e.value for e in RUN_STREAM_FLUSH_EVENT_TYPES} == {
        "run_completed",
        "run_failed",
        "run_cancelled",
    }
    assert {e.value for e in RUN_CLOSE_EVENT_TYPES} == {
        "run_completed",
        "run_failed",
        "run_cancelled",
        "run_skipped",
    }
    assert RUN_PRODUCT_EVENT_TYPES < RUN_STREAM_FLUSH_EVENT_TYPES
    assert RUN_STREAM_FLUSH_EVENT_TYPES < RUN_CLOSE_EVENT_TYPES


def test_strenum_membership_accepts_wire_strings():
    """Journal dicts store type as str; sink emits EventType — both must match."""
    assert "run_completed" in RUN_CLOSE_EVENT_TYPES
    assert EventType.RUN_SKIPPED in RUN_CLOSE_EVENT_TYPES
    assert "run_skipped" not in RUN_PRODUCT_EVENT_TYPES
    assert "run_cancelled" in RUN_STREAM_FLUSH_EVENT_TYPES
    assert "run_skipped" not in RUN_STREAM_FLUSH_EVENT_TYPES


def test_run_phase_terminal_matches_scheduler_set():
    assert {p.value for p in TERMINAL_PHASES} == {
        "completed",
        "failed",
        "skipped",
        "cancelled",
    }
    for phase in TERMINAL_PHASES:
        assert is_run_phase_terminal(phase)
        assert is_run_phase_terminal(phase.value)
    assert not is_run_phase_terminal(RunPhase.RUNNING)
    assert not is_run_phase_terminal(RunPhase.QUEUED)
    assert not is_run_phase_terminal(RunPhase.RETRYING)


def test_event_helpers_match_named_sets():
    assert is_run_close_event(EventType.RUN_SKIPPED)
    assert is_run_close_event("run_cancelled")
    assert not is_run_product_event(EventType.RUN_SKIPPED)
    assert is_run_product_event("run_failed")
    assert is_run_stream_flush_event(EventType.RUN_CANCELLED)
    assert not is_run_stream_flush_event(EventType.RUN_SKIPPED)
    assert not is_run_close_event(EventType.RUN_STARTED)


def test_paused_is_a_close_not_end_turn_or_cancelled():
    assert FinishReason.PAUSED not in (
        FinishReason.END_TURN,
        FinishReason.CANCELLED,
    )
    assert is_gate_pause_finish(FinishReason.PAUSED)
    assert is_gate_pause_finish("paused")
    assert not is_gate_pause_finish(FinishReason.END_TURN)
    assert resolve_turn_outcome(finish_reason=FinishReason.PAUSED) is None
    assert resolve_turn_outcome is resolve_from_outcome


def test_facade_reexports_same_scheduler_set():
    import agentcore.runtime.terminal as terminal

    assert terminal.TERMINAL_PHASES is TERMINAL_PHASES
