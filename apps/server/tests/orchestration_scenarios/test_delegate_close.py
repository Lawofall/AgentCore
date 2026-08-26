"""CEO delegate → one worker finishes → run open/close + assistant close."""

from agentcore.runtime.events import EventType, FinishReason
from tests.orchestration_scenarios.conftest import (
    CEO_FINAL,
    event_types,
    journal_kinds,
    run_orchestration_turn,
    turn_end_finish,
)


async def test_ceo_delegate_single_worker_closes(monkeypatch, tmp_path):
    result, events, provider = await run_orchestration_turn(
        monkeypatch, tmp_path, mode="delegate"
    )

    assert result["finish_reason"] == FinishReason.END_TURN
    assert CEO_FINAL in (result.get("content") or "")
    assert turn_end_finish(result) == FinishReason.END_TURN.value

    kinds = journal_kinds(result)
    assert "run_started" in kinds
    assert "run_completed" in kinds
    assert "turn_end" in kinds
    assert "message_final" in kinds

    types = event_types(events)
    assert EventType.MESSAGE_END in types
    worker_starts = [
        i
        for i, e in enumerate(events)
        if e.type is EventType.RUN_STARTED and e.payload.get("kind") == "agent"
    ]
    worker_done = [
        i
        for i, e in enumerate(events)
        if e.type is EventType.RUN_COMPLETED and e.payload.get("role") == "member"
    ]
    message_end_at = types.index(EventType.MESSAGE_END)
    assert worker_starts, types
    assert worker_done, types
    assert worker_starts[0] < worker_done[0] < message_end_at

    end = next(e for e in events if e.type is EventType.MESSAGE_END)
    assert end.payload["finish_reason"] == FinishReason.END_TURN

    scenarios = [getattr(r, "scenario", "") for r in provider.requests]
    assert "chat" in scenarios
    assert "agent" in scenarios
