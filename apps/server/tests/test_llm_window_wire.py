"""Unit tests for diagnostic LLM window wire projection."""

from agentcore.runtime.facts import (
    LlmCallFact,
    NoteFact,
    RoundBoundaryFact,
    RunHeadFact,
    ToolCallFact,
    TurnStartedFact,
    WindowCompactFact,
)
from agentcore.runtime.journal.window_wire import project_run_llm_window


def test_project_run_llm_window_folds_captain_tool_round():
    run_id = "cap"
    entries = [
        TurnStartedFact(
            system_prompt="SYS",
            user_message="go",
            model_profile="chat",
            history_len=0,
        )
        .to_fact()
        .entry(),
        RoundBoundaryFact(round_idx=0, run_id=run_id, role="captain").to_fact().entry(),
        LlmCallFact(
            run_id=run_id,
            round_idx=0,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
        ToolCallFact(
            run_id=run_id,
            tool_call_id="c1",
            name="search",
            arguments="{}",
            result="hit",
            success=True,
        )
        .to_fact()
        .entry(),
    ]
    resp = project_run_llm_window(entries, run_id=run_id)
    assert resp.available is True
    assert [m.role for m in resp.messages] == ["system", "user", "assistant", "tool"]
    assert resp.messages[2].tool_calls[0].function.name == "search"
    assert resp.messages[3].content == "hit"
    # Captain opening user has no context_blocks origin.
    assert resp.messages[1].origin is None


def test_project_run_llm_window_worker_run_head_origin():
    entries = [
        TurnStartedFact(
            system_prompt="CEO-SYS",
            user_message="build",
            model_profile="chat",
        )
        .to_fact()
        .entry(),
        RoundBoundaryFact(round_idx=0, run_id="cap", role="captain").to_fact().entry(),
        RunHeadFact(
            run_id="w1",
            system_prompt="WORKER-SYS",
            user_message="## 你的任务\n调研",
            user_origin="context_blocks",
        )
        .to_fact()
        .entry(),
        RoundBoundaryFact(round_idx=0, run_id="w1", role="worker").to_fact().entry(),
        LlmCallFact(
            run_id="w1",
            round_idx=0,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": "{}"},
                }
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
        ToolCallFact(
            run_id="w1",
            tool_call_id="c1",
            name="web_search",
            arguments="{}",
            result="ok",
            success=True,
        )
        .to_fact()
        .entry(),
    ]
    resp = project_run_llm_window(entries, run_id="w1")
    assert resp.available is True
    assert resp.messages[0].content == "WORKER-SYS"
    assert resp.messages[0].origin is None
    assert resp.messages[1].role == "user"
    assert resp.messages[1].content == "## 你的任务\n调研"
    assert resp.messages[1].origin == "context_blocks"
    assert resp.messages[2].role == "assistant"


def _tool_call(run_id: str, round_idx: int, tcid: str, result: str) -> list[dict]:
    return [
        RoundBoundaryFact(round_idx=round_idx, run_id=run_id, role="captain").to_fact().entry(),
        LlmCallFact(
            run_id=run_id,
            round_idx=round_idx,
            content=f"round-{round_idx}",
            tool_calls=[
                {
                    "id": tcid,
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
        ToolCallFact(
            run_id=run_id,
            tool_call_id=tcid,
            name="read",
            arguments="{}",
            result=result,
            success=True,
        )
        .to_fact()
        .entry(),
    ]


def test_project_at_call_is_the_request_before_that_call():
    run_id = "cap"
    entries = [
        TurnStartedFact(
            system_prompt="SYS",
            user_message="go",
            model_profile="chat",
            history_len=2,
        )
        .to_fact()
        .entry(),
        *_tool_call(run_id, 0, "c0", "OLD"),
        NoteFact(role="user", content="请改写收尾", reason="finish_guard", run_id=run_id)
        .to_fact()
        .entry(),
        *_tool_call(run_id, 1, "c1", "NEW"),
    ]
    opening = project_run_llm_window(entries, run_id=run_id, round_idx=0, history_len=2)
    assert opening.available is True
    assert opening.history_omitted is True
    assert opening.new_from == 0
    assert opening.compacted is False
    assert [m.role for m in opening.messages] == ["system", "user"]
    assert all(m.content != "OLD" for m in opening.messages)

    second = project_run_llm_window(
        entries,
        run_id=run_id,
        round_idx=1,
        history=[{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "prior"}],
        history_len=2,
    )
    assert second.history_omitted is False
    assert second.compacted is False
    assert second.new_from == 4
    assert [m.role for m in second.messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "tool",
        "user",
    ]
    assert second.messages[4].content == "round-0"
    assert second.messages[5].content == "OLD"
    assert second.messages[6].content == "请改写收尾"
    assert all((m.content or "") != "NEW" for m in second.messages)


def test_project_at_call_uses_the_compact_watermark_already_stored():
    run_id = "w1"
    entries = [
        RunHeadFact(run_id=run_id, system_prompt="WORKER", user_message="task").to_fact().entry(),
        *_tool_call(run_id, 0, "c0", "OLD"),
        *_tool_call(run_id, 1, "c1", "NEW"),
        WindowCompactFact(run_id=run_id, summary="SUM", folded_rounds=1).to_fact().entry(),
        LlmCallFact(run_id=run_id, round_idx=2, content="done", finish_reason="stop")
        .to_fact()
        .entry(),
    ]
    seen = project_run_llm_window(entries, run_id=run_id, round_idx=2)
    assert seen.available is True
    assert seen.compacted is True
    assert seen.new_from == 0
    blob = " ".join(m.content or "" for m in seen.messages)
    assert "SUM" in blob
    assert "NEW" in blob
    assert "OLD" not in blob
    assert seen.history_omitted is False

    before = project_run_llm_window(entries, run_id=run_id, round_idx=1)
    assert before.compacted is False
    assert before.new_from == 2
    assert "OLD" in " ".join(m.content or "" for m in before.messages)


def test_project_at_call_skips_a_rejected_answer_and_keeps_the_steer():
    run_id = "cap"
    entries = [
        TurnStartedFact(system_prompt="SYS", user_message="go", model_profile="chat")
        .to_fact()
        .entry(),
        LlmCallFact(run_id=run_id, round_idx=0, content="bad draft", finish_reason="stop")
        .to_fact()
        .entry(),
        NoteFact(role="user", content="请改写收尾", reason="finish_guard", run_id=run_id)
        .to_fact()
        .entry(),
        LlmCallFact(run_id=run_id, round_idx=1, content="fixed", finish_reason="stop")
        .to_fact()
        .entry(),
    ]
    seen = project_run_llm_window(entries, run_id=run_id, round_idx=1)
    assert [m.content for m in seen.messages] == ["SYS", "go", "请改写收尾"]
    assert seen.new_from == 2


def test_project_at_call_missing_round_is_unavailable():
    entries = [
        TurnStartedFact(system_prompt="SYS", user_message="go", model_profile="chat")
        .to_fact()
        .entry(),
    ]
    resp = project_run_llm_window(entries, run_id="cap", round_idx=3)
    assert resp.available is False
    assert resp.messages == []


def test_project_run_llm_window_unavailable_without_turn_started():
    resp = project_run_llm_window(
        [{"kind": "turn_end", "payload": {"finish_reason": "end_turn"}}],
        run_id="cap",
    )
    assert resp.available is False
    assert resp.messages == []
