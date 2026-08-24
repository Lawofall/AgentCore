"""Resume cutover: the CEO window is rebuilt from the journal, NOT the frame transcript.

Guards :func:`agentcore.runtime.pipeline.resumed_captain_window` — the consumer side of
the conformance golden (执行级事件溯源 §18.3 Phase 2 ④). The golden proves the
projection ``window_from_journal(journal-at-pause) == frame.transcript``; this proves
resume actually READS the projection: it folds ``suspension.journal_entries`` (+ reloaded
history) into the window and ignores ``frame.transcript`` whenever the journal is present.
A stale/wrong frame transcript must NOT leak into the resumed loop — so now that the frame
column is dropped (Phase 2 ⑤) resume is unaffected. When the journal cannot be folded
(missing ``turn_started``, empty entries, or degraded pause) resume fails loud with
:class:`ResumeJournalDegradedError` rather than running on an empty window.
"""

import pytest

from agentcore.core.errors import ResumeJournalDegradedError
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.facts import LlmCallFact, RoundBoundaryFact, TurnStartedFact
from agentcore.runtime.journal import window_from_journal
from agentcore.runtime.pipeline.resume import resumed_captain_window
from agentcore.runtime.suspension import AskUserSuspension

_STALE = [LLMMessage(role="user", content="STALE-FRAME-MUST-NOT-BE-USED")]


def _delegate_pause_journal() -> list[dict]:
    """A captain paused at a `delegate`: head + one captain round issuing the suspended
    tool call (no tool_call fact — the wave is parked), the shape the golden pins."""
    return [
        TurnStartedFact(system_prompt="你是 CEO。", user_message="调研", model_profile="m")
        .to_fact()
        .entry(),
        RoundBoundaryFact(round_idx=0, run_id="cap", role="captain").to_fact().entry(),
        LlmCallFact(
            run_id="cap",
            round_idx=0,
            tool_calls=[
                {
                    "id": "call_del",
                    "type": "function",
                    "function": {"name": "delegate", "arguments": "{}"},
                }
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
    ]


def _suspension(journal: list[dict]) -> AskUserSuspension:
    s = AskUserSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap",
        checkpoint_id="ck1",
        tool_call_id="call_del",
        base_system_prompt="你是 CEO。",
        user_message="调研",
        transcript=list(_STALE),  # deliberately WRONG: resume must not read this
    )
    s.journal_entries = journal
    return s


def test_resumed_window_folds_journal_and_splices_history_not_frame():
    # The frame transcript is a stale sentinel; resume must rebuild from the journal +
    # the reloaded history, so the result is the projection — never the sentinel.
    history = [{"role": "assistant", "content": "早前摘要"}]
    susp = _suspension(_delegate_pause_journal())

    window = resumed_captain_window(susp, history)

    expected = window_from_journal(
        susp.journal_entries,
        history=[LLMMessage(role="assistant", content="早前摘要")],
    )
    assert window == expected
    assert window != susp.transcript  # the stale frame did NOT leak in
    # Head shape: system, the spliced history, the user message, then the suspended
    # delegate assistant (no tool message — the call is still pending).
    assert [m.role for m in window] == ["system", "assistant", "user", "assistant"]
    assert window[1].content == "早前摘要"
    assert window[-1].tool_calls[0].function.name == "delegate"
    assert all(m.role != "tool" for m in window)


def test_resumed_window_without_history_has_no_prefix():
    # No prior turns (or history reloaded to empty) → head is just system + user; the
    # falsy history must not crash or inject an empty message.
    susp = _suspension(_delegate_pause_journal())

    window = resumed_captain_window(susp, history=[])

    assert [m.role for m in window] == ["system", "user", "assistant"]
    assert window != susp.transcript


def test_resumed_window_raises_when_journal_missing_turn_started():
    # Entries without a turn_started anchor cannot fold — fail loud for the user.
    susp = _suspension([{"kind": "checkpoint_required", "payload": {}, "ts": "t"}])

    with pytest.raises(ResumeJournalDegradedError, match="执行日志保存失败"):
        resumed_captain_window(susp, history=[])


def test_resumed_window_raises_when_journal_and_transcript_both_absent():
    # A claimed frame after the Phase 2 ⑤ cutover: transcript is no longer serialized, so a
    # lost journal write leaves NOTHING to rebuild from. Fail loud rather than resume the
    # CEO on a silently empty window (which would drop the whole pre-pause exchange).
    susp = _suspension([])
    susp.transcript = []  # a claimed frame deserializes to empty

    with pytest.raises(ResumeJournalDegradedError, match="执行日志保存失败"):
        resumed_captain_window(susp, history=[{"role": "user", "content": "x"}])
