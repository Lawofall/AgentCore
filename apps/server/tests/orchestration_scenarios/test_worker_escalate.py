"""Worker escalate is visible on the event stream; turn still closes END_TURN."""

from agentcore.runtime.events import EventType, FinishReason
from tests.orchestration_scenarios.conftest import (
    CEO_FINAL,
    ESC_QUESTION,
    event_types,
    journal_kinds,
    run_orchestration_turn,
    turn_end_finish,
)


async def test_worker_escalate_then_turn_closes(monkeypatch, tmp_path):
    result, events, provider = await run_orchestration_turn(
        monkeypatch, tmp_path, mode="escalate"
    )

    # Unarmed / non-blocking escalate: worker continues, CEO synthesizes, END_TURN.
    assert result["finish_reason"] == FinishReason.END_TURN
    assert CEO_FINAL in (result.get("content") or "")
    assert turn_end_finish(result) == FinishReason.END_TURN.value
    assert int((result.get("collab") or {}).get("escalations") or 0) >= 1

    types = event_types(events)
    assert EventType.RUN_ESCALATION in types
    worker_start = next(
        i
        for i, e in enumerate(events)
        if e.type is EventType.RUN_STARTED and e.payload.get("kind") == "agent"
    )
    esc_at = types.index(EventType.RUN_ESCALATION)
    worker_done = next(
        i
        for i, e in enumerate(events)
        if e.type is EventType.RUN_COMPLETED and e.payload.get("role") == "member"
    )
    message_end_at = types.index(EventType.MESSAGE_END)
    assert worker_start < esc_at < worker_done < message_end_at

    raised = next(e for e in events if e.type is EventType.RUN_ESCALATION)
    assert raised.payload.get("question") == ESC_QUESTION
    assert raised.payload.get("blocking") is False

    kinds = journal_kinds(result)
    assert "run_escalation" in kinds
    assert "turn_end" in kinds

    scenarios = [getattr(r, "scenario", "") for r in provider.requests]
    assert "chat" in scenarios and "agent" in scenarios
