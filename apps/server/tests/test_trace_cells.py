"""Owner-facing turn trace: model transcript only, payload intact, no dev-log tail."""

from agentcore.observability.query.trace_cells import build_turn_trace


def _entry(kind: str, payload: dict, ts: str | None = None) -> dict:
    row = {"kind": kind, "payload": payload}
    if ts is not None:
        row["ts"] = ts
    return row


def test_missing_trace_is_one_gap():
    payload = build_turn_trace(trace_id=None)
    assert payload["trace_id"] is None
    assert payload["gap"] == "这场回复没有追踪编号"
    assert payload["lines"] == []
    assert payload["in_progress"] is False


def test_empty_journal_is_a_gap_and_keeps_the_header():
    payload = build_turn_trace(trace_id="a" * 32, mode="local", duration_ms=12_000)
    assert payload["where"] == "本机"
    assert payload["duration_ms"] == 12_000
    assert payload["gap"] == "这次的记录不在"
    assert payload["lines"] == []
    assert "model" not in payload


def test_cloud_turn_without_journal_names_the_place():
    payload = build_turn_trace(trace_id="b" * 32, mode="cloud")
    assert payload["where"] == "云端"
    assert payload["gap"] == "这次的记录不在"


def test_journal_lines_keep_payloads_in_write_order():
    journal = [
        _entry(
            "turn_started",
            {
                "system_prompt": "SYS-SECRET",
                "user_message": "用户原话-SECRET",
                "model_profile": "deepseek-v4",
            },
            ts="2026-10-08T07:00:00Z",
        ),
        _entry(
            "llm_call",
            {
                "run_id": "cap",
                "round_idx": 0,
                "content": "先看文件",
                "reasoning_content": "想一下",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path":"a.txt"}'},
                    }
                ],
                "finish_reason": "tool_calls",
            },
            ts="2026-10-08T07:00:01Z",
        ),
        _entry(
            "tool_use_end",
            {"tool_call_id": "tc1", "tool_name": "read", "result": "capped-display"},
        ),
        _entry(
            "tool_call",
            {
                "run_id": "cap",
                "tool_call_id": "tc1",
                "name": "read",
                "arguments": '{"path":"a.txt"}',
                "result": "FULL TEXT",
                "success": False,
            },
        ),
        _entry(
            "note",
            {"role": "user", "content": "请改写收尾", "reason": "finish_guard", "run_id": "cap"},
        ),
        _entry(
            "llm_call",
            {
                "run_id": "cap",
                "round_idx": 1,
                "content": "写完了",
                "reasoning_content": "",
                "tool_calls": [],
                "finish_reason": "stop",
            },
        ),
        _entry("turn_end", {"finish_reason": "end_turn"}),
    ]
    payload = build_turn_trace(
        trace_id="c" * 32,
        mode="cloud",
        duration_ms=4000,
        journal=journal,
    )
    assert payload["where"] == "云端"
    assert payload["model"] == "deepseek-v4"
    assert payload["duration_ms"] == 4000
    assert payload["in_progress"] is False
    assert "gap" not in payload
    kinds = [line["kind"] for line in payload["lines"]]
    assert kinds == [
        "turn_started",
        "llm_call",
        "tool_call",
        "note",
        "llm_call",
        "turn_end",
    ]
    started = payload["lines"][0]
    assert started["ts"] == "2026-10-08T07:00:00Z"
    assert started["payload"]["system_prompt"] == "SYS-SECRET"
    assert started["payload"]["user_message"] == "用户原话-SECRET"
    first = payload["lines"][1]
    assert first["mark"] == "cap round=0"
    assert first["run_id"] == "cap"
    assert first["round_idx"] == 0
    assert first["payload"]["content"] == "先看文件"
    assert first["payload"]["reasoning_content"] == "想一下"
    tool = payload["lines"][2]
    assert tool["mark"] == "read"
    assert tool["payload"]["result"] == "FULL TEXT"
    assert "run_id" not in tool
    assert payload["lines"][3]["mark"] == "finish_guard"
    assert payload["lines"][3]["payload"]["content"] == "请改写收尾"
    second = payload["lines"][4]
    assert second["mark"] == "cap round=1"
    assert second["round_idx"] == 1
    assert payload["lines"][5]["mark"] == "end_turn"


def test_display_events_stay_out_of_the_model_transcript():
    journal = [
        _entry("turn_started", {"model_profile": "m"}),
        _entry("run_started", {"run_id": "cap", "agent_id": "captain"}),
        _entry("run_context", {"run_id": "cap", "blocks": [{"heading": "设定"}]}),
        _entry(
            "round_boundary",
            {"round_idx": 0, "run_id": "cap", "role": "captain"},
        ),
        _entry(
            "llm_call",
            {"run_id": "cap", "round_idx": 0, "content": "搜", "finish_reason": "tool_calls"},
        ),
        _entry("process_reasoning", {"text": "想"}),
        _entry("tool_use_start", {"name": "web_search"}),
        _entry("process_tool", {"status": "running"}),
        _entry("tool_use_end", {"name": "web_search", "result": "capped"}),
        _entry(
            "tool_call",
            {"run_id": "cap", "name": "web_search", "result": "FULL"},
        ),
        _entry("plan_snapshot", {"runs": []}),
        _entry("run_head", {"run_id": "w1", "system_prompt": "SYS", "user_message": "任务"}),
        _entry("note", {"reason": "nudge", "content": "收尾", "run_id": "cap"}),
        _entry("turn_end", {"finish_reason": "end_turn"}),
    ]
    payload = build_turn_trace(trace_id="d" * 32, journal=journal)
    assert [line["kind"] for line in payload["lines"]] == [
        "turn_started",
        "round_boundary",
        "llm_call",
        "tool_call",
        "run_head",
        "note",
        "turn_end",
    ]
    assert payload["lines"][3]["payload"]["result"] == "FULL"
    assert payload["in_progress"] is False


def test_two_runs_keep_their_own_round_marks_while_the_turn_is_open():
    journal = [
        _entry("turn_started", {"model_profile": "m"}),
        _entry(
            "llm_call",
            {"run_id": "cap", "round_idx": 0, "content": "派出去", "finish_reason": "stop"},
        ),
        _entry(
            "llm_call",
            {"run_id": "w1", "round_idx": 0, "content": "查完了", "finish_reason": "stop"},
        ),
    ]
    payload = build_turn_trace(trace_id="e" * 32, journal=journal)
    assert payload["in_progress"] is True
    assert payload["model"] == "m"
    marks = [line["mark"] for line in payload["lines"] if line["kind"] == "llm_call"]
    assert marks == ["cap round=0", "w1 round=0"]
    assert [line["payload"]["content"] for line in payload["lines"] if line["kind"] == "llm_call"] == [
        "派出去",
        "查完了",
    ]


def test_llm_call_without_a_run_still_marks_the_round():
    journal = [
        _entry(
            "llm_call",
            {"round_idx": 3, "content": "", "tool_calls": [], "finish_reason": "stop"},
        )
    ]
    payload = build_turn_trace(trace_id="f" * 32, journal=journal)
    line = payload["lines"][0]
    assert line["mark"] == "round=3"
    assert line["round_idx"] == 3
    assert "run_id" not in line
    assert payload["in_progress"] is True
