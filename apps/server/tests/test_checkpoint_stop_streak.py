"""Same-turn consecutive checkpoint STOP → terminal close (no further CEO round)."""

from __future__ import annotations

import pytest

from agentcore.core.types import ToolEffect
from agentcore.runtime.checkpoint_stop_streak import (
    compose_repeated_stop_closing,
    consecutive_checkpoint_stops,
    is_repeated_checkpoint_stop,
)
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.pipeline.resume import settle_resumed_suspension
from agentcore.runtime.suspension import AskUserSuspension


def _resolved(kind: str, decision: str, *, checkpoint_id: str = "prior") -> dict:
    return {
        "kind": kind,
        "payload": {"checkpoint_id": checkpoint_id, "decision": decision, "note": ""},
        "ts": "t0",
    }


def test_streak_counts_trailing_stops_across_card_kinds():
    entries = [
        _resolved("team_preview_resolved", "stop", checkpoint_id="tp1"),
        {"kind": "checkpoint_required", "payload": {}, "ts": "t1"},
        _resolved("checkpoint_resolved", "stop", checkpoint_id="ask1"),
    ]
    assert consecutive_checkpoint_stops(entries) == 2


def test_streak_resets_on_non_stop_decision():
    entries = [
        _resolved("team_preview_resolved", "stop"),
        _resolved("checkpoint_resolved", "continue"),
        _resolved("checkpoint_resolved", "stop"),
    ]
    assert consecutive_checkpoint_stops(entries) == 1
    assert is_repeated_checkpoint_stop(entries, CheckpointDecision.STOP) is True

    mid = [
        _resolved("team_preview_resolved", "stop"),
        _resolved("checkpoint_resolved", "continue"),
    ]
    assert consecutive_checkpoint_stops(mid) == 0
    assert is_repeated_checkpoint_stop(mid, CheckpointDecision.STOP) is False


def test_streak_empty_and_first_stop():
    assert consecutive_checkpoint_stops([]) == 0
    assert consecutive_checkpoint_stops(None) == 0
    assert is_repeated_checkpoint_stop([], CheckpointDecision.STOP) is False
    assert is_repeated_checkpoint_stop([], CheckpointDecision.CONTINUE) is False


def test_streak_excludes_consecutive_adjust():
    """team_preview adjust shares STOP's no-grant path but must not count as STOP."""
    entries = [
        _resolved("team_preview_resolved", "adjust", checkpoint_id="tp1"),
        _resolved("team_preview_resolved", "adjust", checkpoint_id="tp2"),
        _resolved("team_preview_resolved", "adjust", checkpoint_id="tp3"),
    ]
    assert consecutive_checkpoint_stops(entries) == 0
    assert is_repeated_checkpoint_stop(entries, CheckpointDecision.ADJUST) is False
    assert is_repeated_checkpoint_stop(entries, CheckpointDecision.STOP) is False


def test_ignore_checkpoint_id_skips_current_prewritten_stop():
    """Cold-path prewrite of the current card is not a prior STOP."""
    entries = [_resolved("checkpoint_resolved", "stop", checkpoint_id="ck_current")]
    assert consecutive_checkpoint_stops(entries, ignore_checkpoint_id="ck_current") == 0
    assert (
        is_repeated_checkpoint_stop(
            entries, CheckpointDecision.STOP, ignore_checkpoint_id="ck_current"
        )
        is False
    )


def test_ignore_checkpoint_id_preserves_prior_stop_as_repeated():
    """Prior card STOP + current prewrite STOP → ignore current still repeated."""
    entries = [
        _resolved("team_preview_resolved", "stop", checkpoint_id="tp0"),
        _resolved("checkpoint_resolved", "stop", checkpoint_id="ck_current"),
    ]
    assert consecutive_checkpoint_stops(entries, ignore_checkpoint_id="ck_current") == 1
    assert (
        is_repeated_checkpoint_stop(
            entries, CheckpointDecision.STOP, ignore_checkpoint_id="ck_current"
        )
        is True
    )


def test_compose_repeated_stop_closing_never_empty():
    assert "已按你的意思停下" in compose_repeated_stop_closing()
    assert "先到这" in compose_repeated_stop_closing(note="先到这")


def test_compose_repeated_stop_closing_with_note_does_not_ask_user_to_restate():
    """卡上已写下一步 → 禁止再要用户「发新消息说明」（他刚说过）。"""
    body = compose_repeated_stop_closing(note="这 10 款都不行，换新的")
    assert "这 10 款都不行，换新的" in body
    assert "请发新消息说明" not in body
    # 无 note 那支仍得请用户开口，原引导保留。
    assert "请发新消息说明" in compose_repeated_stop_closing()


def _ask_frame(*, journal_entries: list[dict] | None = None) -> AskUserSuspension:
    return AskUserSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="ck_ask",
        tool_call_id="call_ask",
        base_system_prompt="sys",
        user_message="task",
        transcript=[],
        question="澄清？",
        questions=[],
        journal_entries=list(journal_entries or []),
    )


def _sink_with_required(*, kind: EventType = EventType.CHECKPOINT_REQUIRED) -> EventSink:
    sink = EventSink()
    sink.seed_journal([{"type": kind.value, "payload": {}, "timestamp": "t"}])
    return sink


@pytest.mark.asyncio
async def test_cross_kind_second_stop_terminates_without_ceo_round():
    """team_preview STOP then ask_user STOP → INTERACT terminal (no CEO feed)."""
    prior = [_resolved("team_preview_resolved", "stop", checkpoint_id="tp0")]
    sink = _sink_with_required()
    settled = await settle_resumed_suspension(
        _ask_frame(journal_entries=prior),
        decision=CheckpointDecision.STOP,
        note="别问了",
        selected=[],
        sink=sink,
        delegate_tool=None,
        execution_id="",
    )
    assert settled.effect is ToolEffect.INTERACT
    assert settled.terminal_text is not None
    assert "已按你的意思停下" in settled.terminal_text
    assert "别问了" in settled.terminal_text
    # Still journaled the current STOP so UI / reload see the settled card.
    journal = sink.execution_journal() or []
    assert any(e["type"] == EventType.CHECKPOINT_RESOLVED.value for e in journal)


@pytest.mark.asyncio
async def test_stop_continue_stop_not_consecutive():
    """STOP → CONTINUE → STOP keeps first-stop CONTINUE-feed behaviour."""
    prior = [
        _resolved("team_preview_resolved", "stop", checkpoint_id="tp0"),
        _resolved("checkpoint_resolved", "continue", checkpoint_id="ask0"),
    ]
    sink = _sink_with_required()
    settled = await settle_resumed_suspension(
        _ask_frame(journal_entries=prior),
        decision=CheckpointDecision.STOP,
        note="",
        selected=[],
        sink=sink,
        delegate_tool=None,
        execution_id="",
    )
    assert settled.effect is ToolEffect.CONTINUE
    assert settled.terminal_text is None
    assert "取消了澄清" in settled.output


@pytest.mark.asyncio
async def test_single_stop_unchanged():
    """First STOP in a turn still CONTINUE-feeds the CEO (no terminal)."""
    sink = _sink_with_required()
    settled = await settle_resumed_suspension(
        _ask_frame(journal_entries=[]),
        decision=CheckpointDecision.STOP,
        note="收工",
        selected=[],
        sink=sink,
        delegate_tool=None,
        execution_id="",
    )
    assert settled.effect is ToolEffect.CONTINUE
    assert settled.terminal_text is None
    assert "取消了澄清" in settled.output
    assert "收工" in settled.output
