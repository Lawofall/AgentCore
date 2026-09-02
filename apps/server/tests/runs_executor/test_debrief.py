"""完工交接简报 (debrief) harvest — closing-round prose, with args fallback.

New rounds: the brief is the assistant ``content`` on the ``handoff`` message
(arguments are empty). Historical transcripts that stuffed four-grid fields
into arguments still harvest those fields. Never parsed out of markdown prose.
"""

from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.runs.serialize import debrief_from_transcript


def _handoff(
    arguments: str = "{}",
    call_id: str = "h1",
    content: str | None = None,
) -> LLMMessage:
    return LLMMessage(
        role="assistant",
        content=content,
        tool_calls=[
            ToolCall(id=call_id, function=ToolCallFunction(name="handoff", arguments=arguments))
        ],
    )


def _call(name: str, arguments: str, call_id: str = "c1") -> LLMMessage:
    return LLMMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(id=call_id, function=ToolCallFunction(name=name, arguments=arguments))
        ],
    )


def test_parses_legacy_four_fields_from_args():
    transcript = [
        LLMMessage(role="user", content="做事"),
        LLMMessage(role="assistant", content="这是交付正文。"),
        _handoff(
            '{"summary": "完成了登录接口重构", '
            '"key_points": ["响应从 800ms 降到 120ms", "改动 auth/login.py"], '
            '"assumptions": "沿用现有 JWT 方案", '
            '"next_steps": "给注册接口做同样的缓存改造"}'
        ),
        LLMMessage(role="tool", content="已收尾。", tool_call_id="h1"),
    ]
    debrief = debrief_from_transcript(transcript)
    assert debrief == {
        "summary": "完成了登录接口重构",
        "key_points": ["响应从 800ms 降到 120ms", "改动 auth/login.py"],
        "assumptions": "沿用现有 JWT 方案",
        "next_steps": "给注册接口做同样的缓存改造",
    }


def test_harvests_closing_round_content_when_args_empty():
    transcript = [
        LLMMessage(role="user", content="做事"),
        _handoff("{}", content="完成了登录接口重构。改动 auth/login.py，响应从 800ms 降到 120ms。"),
        LLMMessage(role="tool", content="已收尾。", tool_call_id="h1"),
    ]
    debrief = debrief_from_transcript(transcript)
    assert debrief == {
        "summary": "完成了登录接口重构。改动 auth/login.py，响应从 800ms 降到 120ms。"
    }


def test_empty_content_and_empty_args_is_none():
    assert debrief_from_transcript([_handoff("{}")]) is None
    assert debrief_from_transcript([_handoff("{}", content="   ")]) is None


def test_malformed_args_still_harvests_content():
    """Parse-failed JSON must not drop a brief already written as content."""
    transcript = [
        _handoff('{"summary": ', content="交叉验证完成，建议一周内表态。", call_id="h1"),
        LLMMessage(role="tool", content="工具参数无效", tool_call_id="h1"),
        _handoff("{}", content=None, call_id="h2"),
        LLMMessage(role="tool", content="已收尾。", tool_call_id="h2"),
    ]
    assert debrief_from_transcript(transcript) == {
        "summary": "交叉验证完成，建议一周内表态。"
    }


def test_no_handoff_call_returns_none():
    transcript = [
        LLMMessage(role="user", content="做事"),
        LLMMessage(role="assistant", content="纯交付正文，没有调用 handoff。"),
    ]
    assert debrief_from_transcript(transcript) is None


def test_other_tool_calls_are_ignored():
    transcript = [
        _call("web_search", '{"query": "x"}'),
        _call("escalate", '{"question": "Y?"}', call_id="c2"),
    ]
    assert debrief_from_transcript(transcript) is None


def test_last_usable_handoff_wins():
    transcript = [
        _handoff("{}", call_id="h1", content="第一版结论"),
        LLMMessage(role="user", content="改一下"),
        _handoff("{}", call_id="h2", content="以最后这版为准"),
    ]
    assert debrief_from_transcript(transcript) == {"summary": "以最后这版为准"}


def test_content_wins_over_same_message_args():
    """New rounds: 便条 is content; leftover four-grid must not crush it."""
    assert debrief_from_transcript(
        [_handoff('{"summary": "只给了结论一条"}', content="这是交付正文。")]
    ) == {"summary": "这是交付正文。"}


def test_key_points_only_no_summary_legacy():
    debrief = debrief_from_transcript([_handoff('{"key_points": ["要点一", "要点二"]}')])
    assert debrief == {"key_points": ["要点一", "要点二"]}


def test_lone_string_key_points_is_tolerated():
    debrief = debrief_from_transcript([_handoff('{"summary": "S", "key_points": "单条要点"}')])
    assert debrief == {"summary": "S", "key_points": ["单条要点"]}


def test_markdown_list_key_points_are_split():
    raw = (
        '{"summary": "S", "key_points": "- 响应降到 120ms\\n- 改动 auth/login.py\\n'
        '- 未覆盖边缘路径"}'
    )
    debrief = debrief_from_transcript([_handoff(raw)])
    assert debrief == {
        "summary": "S",
        "key_points": ["响应降到 120ms", "改动 auth/login.py", "未覆盖边缘路径"],
    }


def test_json_array_string_key_points_still_parsed():
    debrief = debrief_from_transcript(
        [_handoff('{"summary": "S", "key_points": "[\\"a\\", \\"b\\"]"}')]
    )
    assert debrief == {"summary": "S", "key_points": ["a", "b"]}


def test_blank_key_points_entries_dropped():
    debrief = debrief_from_transcript(
        [_handoff('{"summary": "S", "key_points": ["有内容", "   ", ""]}')]
    )
    assert debrief == {"summary": "S", "key_points": ["有内容"]}


def test_malformed_arguments_skipped_without_content():
    assert debrief_from_transcript([_handoff("not json")]) is None
