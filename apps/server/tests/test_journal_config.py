"""Journal persist caps: process-lane 8k for tool_use_end, 1 MiB safety for others."""

from agentcore.runtime.events.journal_config import (
    _JOURNAL_PAYLOAD_SAFETY_CAP,
    _PROCESS_RESULT_CAP,
    cap_journal_safety_string,
    cap_process_result,
    journal_payload_for_persist,
)
from agentcore.runtime.events.types import EventType


def test_cap_process_result_marks_overflow():
    big = "x" * (_PROCESS_RESULT_CAP + 500)
    capped = cap_process_result(big)
    assert isinstance(capped, str)
    assert capped.endswith("…")
    assert len(capped) == _PROCESS_RESULT_CAP + 1
    assert capped[:_PROCESS_RESULT_CAP] == big[:_PROCESS_RESULT_CAP]


def test_cap_process_result_leaves_short_and_non_str():
    assert cap_process_result("ok") == "ok"
    assert cap_process_result(None) is None
    assert cap_process_result({"k": "v"}) == {"k": "v"}


def test_safety_string_marks_original_length_and_is_idempotent():
    big = "n" * (_JOURNAL_PAYLOAD_SAFETY_CAP + 50_000)
    capped = cap_journal_safety_string(big)
    assert isinstance(capped, str)
    assert len(capped) == _JOURNAL_PAYLOAD_SAFETY_CAP
    assert f"original_chars={len(big)}" in capped
    assert f"cap={_JOURNAL_PAYLOAD_SAFETY_CAP}" in capped
    assert cap_journal_safety_string(capped) == capped


def test_journal_payload_caps_tool_use_end_result_not_live_alias():
    big = "x" * (_PROCESS_RESULT_CAP + 200)
    wire = {"tool_call_id": "t1", "tool_name": "read_url", "result": big, "status": "success"}
    persisted = journal_payload_for_persist(EventType.TOOL_USE_END.value, wire)
    assert persisted is not wire
    assert persisted["result"] == cap_process_result(big)
    assert wire["result"] == big


def test_journal_payload_safety_caps_leftover_team_preview_note():
    huge = "注" * (_JOURNAL_PAYLOAD_SAFETY_CAP + 10)
    wire = {"checkpoint_id": "tp1", "decision": "continue", "note": huge}
    persisted = journal_payload_for_persist("team_preview_resolved", wire)
    assert persisted["checkpoint_id"] == "tp1"
    assert persisted["decision"] == "continue"
    assert isinstance(persisted["note"], str)
    assert len(persisted["note"]) == _JOURNAL_PAYLOAD_SAFETY_CAP
    assert "journal_capped" in persisted["note"]
    assert wire["note"] == huge


def test_journal_payload_skips_safety_cap_on_run_context():
    huge = "s" * (_JOURNAL_PAYLOAD_SAFETY_CAP + 10)
    wire = {"blocks": [{"channel": "system", "body": huge}]}
    persisted = journal_payload_for_persist(EventType.RUN_CONTEXT.value, wire)
    assert persisted["blocks"][0]["body"] == huge
    assert persisted is not wire


def test_execution_tool_call_fact_bypasses_display_caps():
    """Red line: ``tool_call.result`` is the resume window — never the 8k/1MiB display caps."""
    from agentcore.runtime.facts import (
        ToolCallFact,
        TurnFactLog,
        current_fact_log,
        record_turn_fact,
    )

    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        big = "x" * 20_000
        record_turn_fact(
            ToolCallFact(
                run_id="r1",
                tool_call_id="t1",
                name="read_url",
                result=big,
            ).to_fact()
        )
        fact = next(e for e in log.entries() if e["kind"] == "tool_call")
        assert fact["payload"]["result"] == big
    finally:
        current_fact_log.reset(token)
