"""ask_user checkpoint suspends the turn — then cold-resumes to a real close."""

from agentcore.runtime.events import EventType, FinishReason
from agentcore.runtime.suspension import AskUserSuspension
from tests.orchestration_scenarios.conftest import (
    CEO_FINAL,
    cold_claim_frame,
    event_types,
    journal_kinds,
    run_orchestration_resume,
    run_orchestration_turn,
    turn_end_finish,
)


async def test_ask_user_checkpoint_pauses_not_fake_complete(monkeypatch, tmp_path):
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        return None

    result, events, provider = await run_orchestration_turn(
        monkeypatch,
        tmp_path,
        mode="ask",
        approvals_enabled=True,
        suspension_saver=_save,
        suspension_deleter=_drop,
    )

    assert result["finish_reason"] == FinishReason.PAUSED
    assert turn_end_finish(result) == FinishReason.PAUSED.value
    assert saved, "durable frame must persist — otherwise D11 fails instead of pausing"

    types = event_types(events)
    assert EventType.CHECKPOINT_REQUIRED in types
    assert EventType.MESSAGE_END in types
    assert types.index(EventType.CHECKPOINT_REQUIRED) < types.index(EventType.MESSAGE_END)
    end = next(e for e in events if e.type is EventType.MESSAGE_END)
    assert end.payload["finish_reason"] == FinishReason.PAUSED

    kinds = journal_kinds(result)
    assert "checkpoint_required" in kinds or EventType.CHECKPOINT_REQUIRED.value in kinds
    assert "turn_end" in kinds

    scenarios = [getattr(r, "scenario", "") for r in provider.requests]
    assert "agent" not in scenarios
    assert provider.calls >= 1


async def test_ask_user_checkpoint_resumes_to_end_turn(monkeypatch, tmp_path):
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        return None

    paused, _pause_events, _pause_provider = await run_orchestration_turn(
        monkeypatch,
        tmp_path,
        mode="ask",
        approvals_enabled=True,
        suspension_saver=_save,
        suspension_deleter=_drop,
    )
    assert paused["finish_reason"] == FinishReason.PAUSED
    assert saved, "durable frame must persist — otherwise D11 fails instead of pausing"

    restored = cold_claim_frame(saved[0])
    assert isinstance(restored, AskUserSuspension)
    assert restored.journal_entries, "resume window is folded from the pause journal"

    result, events, provider = await run_orchestration_resume(
        monkeypatch,
        tmp_path,
        suspension=restored,
        suspension_saver=_save,
        suspension_deleter=_drop,
    )

    assert result["finish_reason"] == FinishReason.END_TURN
    assert CEO_FINAL in (result.get("content") or "")
    assert turn_end_finish(result) == FinishReason.END_TURN.value
    assert len(saved) == 1, "resume must close, not re-pause"

    kinds = journal_kinds(result)
    assert "checkpoint_resolved" in kinds
    assert "checkpoint_required" in kinds or EventType.CHECKPOINT_REQUIRED.value in kinds
    assert "turn_end" in kinds

    types = event_types(events)
    assert EventType.CHECKPOINT_RESOLVED in types
    assert EventType.MESSAGE_END in types
    assert types.index(EventType.CHECKPOINT_RESOLVED) < types.index(
        EventType.MESSAGE_END
    )
    end = next(e for e in events if e.type is EventType.MESSAGE_END)
    assert end.payload["finish_reason"] == FinishReason.END_TURN

    tool_msgs = [
        m
        for req in provider.requests
        for m in req.messages
        if getattr(m, "role", None) == "tool"
    ]
    assert any(
        getattr(m, "tool_call_id", None) == restored.tool_call_id for m in tool_msgs
    ), "settled ask_user result must feed the resumed CEO window"
