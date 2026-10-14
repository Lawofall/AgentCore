"""Crash / infra resume windows: journal hints + unmatched trailing tool calls."""

from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.runs.redrive_sites import (
    ResumeHint,
    ResumeKind,
    bind_resume_hints,
    current_resume_hints,
    get_resume_hint,
    hints_from_journal,
    is_resume_site,
    reset_resume_hints,
    unmatched_trailing_tool_calls,
    window_has_react_progress,
)
from agentcore.runtime.runs.types import RunSpec


def _tc(call_id: str = "fw", name: str = "write") -> ToolCall:
    return ToolCall(
        id=call_id,
        function=ToolCallFunction(name=name, arguments="{}"),
    )


def _asst_tools(*ids: str) -> LLMMessage:
    return LLMMessage(
        role="assistant",
        content=None,
        tool_calls=[_tc(i) for i in ids],
    )


class _FakeTurn:
    def __init__(
        self,
        unfinished: list[str],
        windows: dict[str, list[LLMMessage] | None],
    ) -> None:
        self.unfinished_run_ids = unfinished
        self._windows = windows

    def window(self, *, run_id: str | None = None, history=None):
        return self._windows.get(run_id)


def test_window_has_react_progress_requires_assistant():
    assert window_has_react_progress(None) is False
    assert window_has_react_progress([]) is False
    assert (
        window_has_react_progress(
            [
                LLMMessage(role="system", content="s"),
                LLMMessage(role="user", content="task"),
            ]
        )
        is False
    )
    assert window_has_react_progress([LLMMessage(role="assistant", content="hi")]) is True


def test_unmatched_trailing_tool_calls_empty_and_complete():
    assert unmatched_trailing_tool_calls([]) == []
    assert unmatched_trailing_tool_calls([LLMMessage(role="user", content="go")]) == []
    complete = [
        LLMMessage(role="user", content="go"),
        _asst_tools("fw"),
        LLMMessage(role="tool", content="ok", tool_call_id="fw"),
    ]
    assert unmatched_trailing_tool_calls(complete) == []


def test_unmatched_trailing_tool_calls_pending_same_ids():
    pending = unmatched_trailing_tool_calls(
        [
            LLMMessage(role="system", content="s"),
            LLMMessage(role="user", content="task"),
            _asst_tools("a", "b"),
            LLMMessage(role="tool", content="done-a", tool_call_id="a"),
        ]
    )
    assert [tc.id for tc in pending] == ["b"]


def test_unmatched_trailing_tool_calls_abort_after_user_or_later_assistant():
    after_user = [
        _asst_tools("fw"),
        LLMMessage(role="user", content="## 续干指令\n继续"),
    ]
    assert unmatched_trailing_tool_calls(after_user) == []
    after_asst = [
        _asst_tools("fw"),
        LLMMessage(role="assistant", content="moved on"),
    ]
    assert unmatched_trailing_tool_calls(after_asst) == []


def test_hints_from_journal_only_unfinished_with_assistant():
    progress = [
        LLMMessage(role="system", content="s"),
        LLMMessage(role="user", content="task"),
        _asst_tools("fw"),
    ]
    head_only = [
        LLMMessage(role="system", content="s"),
        LLMMessage(role="user", content="task"),
    ]
    hints = hints_from_journal(
        _FakeTurn(  # type: ignore[arg-type]
            ["w2", "w3"],
            {"w2": progress, "w3": head_only, "w1": progress},
        )
    )
    assert set(hints) == {"w2"}
    assert hints["w2"].kind is ResumeKind.CRASH
    assert hints["w2"].transcript == tuple(progress)


def test_bind_resume_hints_lookup_not_in_completed():
    window = (
        LLMMessage(role="user", content="task"),
        _asst_tools("fw"),
    )
    token = bind_resume_hints(
        {"w2": ResumeHint(kind=ResumeKind.CRASH, transcript=window)}
    )
    try:
        hint = get_resume_hint("w2")
        assert hint is not None
        assert hint.kind is ResumeKind.CRASH
        assert hint.transcript == window
        assert get_resume_hint("other") is None
    finally:
        reset_resume_hints(token)
    assert current_resume_hints.get() is None


