"""Cross-turn captain transcript: tool rounds plus sealed prose."""

from types import SimpleNamespace

from agentcore.config import settings
from agentcore.conversation.compact_prompt import estimate_message_tokens
from agentcore.conversation.history import _fold_history_messages, _history_tail
from agentcore.conversation.transcript import (
    CLEARED_TOOL_RESULT,
    UNCLOSED_TOOL_RESULT,
    transcript_rows,
)
from agentcore.runtime.engine.window_compact import BRIDGE_USER, SUMMARY_LEAD
from agentcore.runtime.resolve.prompt.envelope import opening_ceo_messages


def _llm(run_id: str, content: str, calls: list[dict], reasoning: str = "") -> dict:
    return {
        "kind": "llm_call",
        "payload": {
            "run_id": run_id,
            "content": content,
            "reasoning_content": reasoning,
            "tool_calls": calls,
        },
    }


def _call(tcid: str, name: str) -> dict:
    return {
        "id": tcid,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def _boundary(run_id: str, role: str) -> dict:
    return {"kind": "round_boundary", "payload": {"run_id": run_id, "role": role}}


def _tool(run_id: str, tcid: str, result: str) -> dict:
    return {
        "kind": "tool_call",
        "payload": {"run_id": run_id, "tool_call_id": tcid, "result": result},
    }


def test_transcript_keeps_captain_tools_and_final_prose_without_worker_or_system():
    entries = [
        {"kind": "turn_started", "payload": {"system_prompt": "SYS", "user_message": "问"}},
        _boundary("cap", "captain"),
        _llm("cap", "先看", [_call("c1", "read")], reasoning="想一下"),
        _tool("cap", "c1", "文件内容"),
        _boundary("w1", "worker"),
        _llm("w1", "队员", [_call("w1", "run")]),
        _tool("w1", "w1", "不该出现"),
    ]
    rows = transcript_rows(entries, "查完了")
    assert [row["role"] for row in rows] == ["assistant", "tool", "assistant"]
    assert rows[0]["tool_calls"][0]["function"]["name"] == "read"
    assert rows[0]["reasoning_content"] == "想一下"
    assert rows[1] == {"role": "tool", "content": "文件内容", "tool_call_id": "c1"}
    assert rows[2] == {"role": "assistant", "content": "查完了"}
    assert "reasoning_content" not in rows[2]
    assert all("SYS" not in str(row) for row in rows)
    assert all("不该出现" not in str(row) for row in rows)


def test_transcript_closes_a_tool_that_never_returned():
    entries = [
        _boundary("cap", "captain"),
        _llm("cap", "", [_call("c1", "run")]),
    ]
    rows = transcript_rows(entries, "")
    assert rows[-1]["role"] == "tool"
    assert rows[-1]["content"] == UNCLOSED_TOOL_RESULT
    assert rows[-1]["tool_call_id"] == "c1"


def test_fold_replays_tools_instead_of_a_failure_note():
    assistant = SimpleNamespace(
        id="a1",
        role="assistant",
        content="",
        usage={"status": "failed", "finish_reason": "error"},
        evidence_ledger=None,
    )
    journals = {
        "a1": [
            _boundary("cap", "captain"),
            _llm("cap", "试一下", [_call("c1", "run")]),
            _tool("cap", "c1", "退出码 1"),
        ]
    }
    out = _fold_history_messages(
        [
            SimpleNamespace(role="user", content="为什么跑不了", id="u1", usage=None),
            assistant,
        ],
        journals,
    )
    # user row then tool transcript; the empty failure is not the note.
    assert out[0]["role"] == "user"
    assert any(row.get("role") == "tool" and "退出码 1" in row["content"] for row in out)
    assert not any("未产生有效回复" in (row.get("content") or "") for row in out)


def test_history_tail_does_not_open_on_a_tool_row():
    items = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "content": "a", "tool_call_id": "c1"},
        {"role": "tool", "content": "b", "tool_call_id": "c2"},
        {"role": "user", "content": "后"},
    ]
    assert _history_tail(items, 2)[0]["role"] == "assistant"
    assert _history_tail(items, 1) == [{"role": "user", "content": "后"}]


def test_opening_window_keeps_tool_rounds():
    history = transcript_rows(
        [
            _boundary("cap", "captain"),
            _llm("cap", "看", [_call("c1", "read")], reasoning="推理"),
            _tool("cap", "c1", "正文"),
        ],
        "结论",
    )
    messages = opening_ceo_messages(
        system_prompt="sys",
        history=history,
        turn_envelope="",
        user_content="接着说",
    )
    roles = [msg.role for msg in messages]
    assert roles == ["system", "assistant", "tool", "assistant", "user"]
    assert messages[1].tool_calls[0].function.name == "read"
    assert messages[1].reasoning_content == "推理"
    assert messages[2].tool_call_id == "c1"
    assert messages[2].content == "正文"
    assert messages[3].content == "结论"
    assert messages[3].reasoning_content is None


def _compact(run_id: str, summary: str, folded: int) -> dict:
    return {
        "kind": "window_compact",
        "payload": {
            "run_id": run_id,
            "summary": summary,
            "folded_rounds": folded,
        },
    }


def test_transcript_applies_stored_window_compact_instead_of_fat_journal():
    entries = [_boundary("cap", "captain")]
    for index in range(6):
        entries.append(_llm("cap", f"看{index}", [_call(f"c{index}", "read")]))
        entries.append(_tool("cap", f"c{index}", f"全文{index}"))
    entries.append(_compact("cap", "已读前几份", 4))
    rows = transcript_rows(entries, "结论")
    text = "\n".join(str(row.get("content") or "") for row in rows)
    assert SUMMARY_LEAD + "已读前几份" in text
    assert BRIDGE_USER in text
    assert "全文0" not in text
    assert "全文3" not in text
    assert "全文4" in text
    assert "全文5" in text
    assert rows[-1] == {"role": "assistant", "content": "结论"}
    names = [
        row["tool_calls"][0]["function"]["name"]
        for row in rows
        if row.get("tool_calls")
    ]
    assert names == ["read", "read"]


def test_transcript_clears_old_tool_results_over_recency(monkeypatch):
    monkeypatch.setattr(settings, "compaction_recency_token_budget", 20, raising=True)
    entries = [_boundary("cap", "captain")]
    for index in range(3):
        entries.append(_llm("cap", f"看{index}", [_call(f"c{index}", "read")]))
        entries.append(_tool("cap", f"c{index}", "字" * 80))
    rows = transcript_rows(entries, "结论")
    tool_rows = [row for row in rows if row["role"] == "tool"]
    assert len(tool_rows) == 3
    assert tool_rows[0]["content"] == CLEARED_TOOL_RESULT
    assert tool_rows[1]["content"] == CLEARED_TOOL_RESULT
    assert tool_rows[2]["content"] == "字" * 80
    assert tool_rows[0]["tool_call_id"] == "c0"
    assert rows[-1]["content"] == "结论"
    call_names = [
        row["tool_calls"][0]["function"]["name"]
        for row in rows
        if row.get("tool_calls")
    ]
    assert call_names == ["read", "read", "read"]


def test_transcript_does_not_clear_when_compact_already_fits_recency():
    entries = [_boundary("cap", "captain")]
    for index in range(6):
        entries.append(_llm("cap", f"看{index}", [_call(f"c{index}", "read")]))
        entries.append(_tool("cap", f"c{index}", "字" * 80))
    entries.append(_compact("cap", "已读前几份", 4))
    rows = transcript_rows(entries, "结论")
    tool_contents = [row["content"] for row in rows if row["role"] == "tool"]
    assert CLEARED_TOOL_RESULT not in tool_contents
    assert any(content.startswith("字") for content in tool_contents)


def test_fold_uses_projected_replay_not_fat_journal(monkeypatch):
    monkeypatch.setattr(settings, "compaction_recency_token_budget", 20, raising=True)
    assistant = SimpleNamespace(
        id="a1",
        role="assistant",
        content="查完了",
        usage=None,
        evidence_ledger=None,
    )
    journals = {
        "a1": [
            _boundary("cap", "captain"),
            _llm("cap", "先看", [_call("c0", "file_list")]),
            _tool("cap", "c0", "字" * 80),
            _llm("cap", "再看", [_call("c1", "file_list")]),
            _tool("cap", "c1", "字" * 80),
        ]
    }
    out = _fold_history_messages(
        [
            SimpleNamespace(role="user", content="整理", id="u1", usage=None),
            assistant,
        ],
        journals,
    )
    tool_contents = [row["content"] for row in out if row.get("role") == "tool"]
    assert tool_contents[0] == CLEARED_TOOL_RESULT
    assert tool_contents[-1] == "字" * 80
    assert out[-1]["content"] == "查完了"
    fat = estimate_message_tokens(assistant, journals={"a1": journals["a1"]})
    raw_receipt = 160
    assert fat < raw_receipt
