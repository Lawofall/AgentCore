"""供应商工具协议标签清洗（LongCat 等残留 → 合法工具名 / 干净正文）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.core.types import ToolCategory
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.engine.tool_exec import execute_tools
from agentcore.runtime.engine.tool_protocol_sanitize import (
    ASSISTANT_CONTENT_MAX_CHARS,
    ASSISTANT_CONTENT_OVERSIZE_FACE,
    prepare_assistant_content,
    sanitize_protocol_text,
    sanitize_tool_args,
    sanitize_tool_name,
    truncate_at_dsml_open,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.serialize import debrief_from_transcript
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def test_sanitize_tool_name_strips_longcat_arg_key():
    assert sanitize_tool_name("web_query</longcat_arg_key>") == "web_query"
    assert sanitize_tool_name("<longcat_tool_call>web_search") == "web_search"
    assert sanitize_tool_name("web_search") == "web_search"


def test_sanitize_protocol_text_strips_tool_call_tags():
    raw = "结论如下<longcat_tool_call>勿泄漏</longcat_tool_call>完。"
    cleaned = sanitize_protocol_text(raw)
    assert "<longcat" not in cleaned
    assert "结论如下" in cleaned
    assert "完。" in cleaned


def test_sanitize_protocol_text_strips_dsml_keeps_prose():
    raw = (
        "先派设计更新任务。\n\n"
        '<｜DSML｜tool_calls>\n'
        '<｜DSML｜invoke name="delegate">\n'
        '<｜DSML｜parameter name="tasks" string="false">[{"role": "调研员"}]'
        "</｜DSML｜parameter>\n"
        "</｜DSML｜invoke>\n"
        "</｜DSML｜tool_calls>\n\n"
        "交付综述如下。"
    )
    cleaned = sanitize_protocol_text(raw)
    assert "｜DSML｜" not in cleaned
    assert "<｜DSML｜" not in cleaned
    assert "先派设计更新任务。" in cleaned
    assert "交付综述如下。" in cleaned
    assert "调研员" not in cleaned


def test_sanitize_protocol_text_unclosed_dsml_drops_tail():
    raw = "自然语言前缀\n\n<｜DSML｜tool_calls>\n<｜DSML｜invoke name=\"delegate\">\n未闭合…"
    cleaned = sanitize_protocol_text(raw)
    assert cleaned.strip() == "自然语言前缀"
    assert "｜DSML｜" not in cleaned


def test_sanitize_protocol_text_truncated_close_keeps_resume_prose():
    raw = '前缀\n\n<｜DSML｜parameter name="t">json</｜DSML｜parameter\n\n后缀正文'
    cleaned = sanitize_protocol_text(raw)
    assert "前缀" in cleaned
    assert "后缀正文" in cleaned
    assert "json" not in cleaned
    assert "｜DSML｜" not in cleaned


def test_truncate_at_dsml_open_salvage():
    raw = "已写一半\n\n<｜DSML｜tool_calls>\n<｜DSML｜invoke name=\"delegate\">\nx"
    assert truncate_at_dsml_open(raw) == "已写一半\n\n"
    assert truncate_at_dsml_open("无标记") == "无标记"


def test_prepare_assistant_content_salvage_truncates_then_sanitizes():
    raw = "前缀OK\n\n<｜DSML｜tool_calls>\n" + ("x" * 1000)
    out = prepare_assistant_content(raw, salvage=True)
    assert out.strip() == "前缀OK"
    assert "｜DSML｜" not in out


def test_prepare_assistant_content_oversize_after_strip_is_short_face():
    # No DSML — plain prose past the ceiling becomes the short error face.
    raw = "字" * (ASSISTANT_CONTENT_MAX_CHARS + 1)
    assert prepare_assistant_content(raw) == ASSISTANT_CONTENT_OVERSIZE_FACE


def test_prepare_assistant_content_dsml_wall_then_oversize_face():
    # Strip leaves a still-huge prefix → short face (not the multi-MB wall).
    prefix = "字" * (ASSISTANT_CONTENT_MAX_CHARS + 50)
    raw = prefix + "\n\n<｜DSML｜tool_calls>\n" + ("y" * 10_000)
    assert prepare_assistant_content(raw) == ASSISTANT_CONTENT_OVERSIZE_FACE
    # Salvage truncates before the wall, so oversize prefix alone still faces.
    assert prepare_assistant_content(raw, salvage=True) == ASSISTANT_CONTENT_OVERSIZE_FACE


def test_sanitize_tool_args_recursive():
    args = {
        "summary": "要点</longcat_arg_key>",
        "key_points": ["a<longcat_tool_call>", "b"],
        "nested": {"q": "x</longcat_arg_value>"},
    }
    out = sanitize_tool_args(args)
    assert out["summary"] == "要点"
    assert out["key_points"][0] == "a"
    assert out["nested"]["q"] == "x"


def test_sanitize_raw_tool_arguments_strips_xml_hybrid():
    from agentcore.runtime.engine.tool_protocol_sanitize import (
        sanitize_raw_tool_arguments,
    )

    raw = (
        '{"tasks"><parameter name="list"><object>'
        '<parameter name="role": "导师审稿人", "task": "审阅全文"}'
    )
    cleaned = sanitize_raw_tool_arguments(raw)
    assert "<parameter" not in cleaned
    assert "<object>" not in cleaned
    assert '"tasks":' in cleaned
    assert '"role":' in cleaned
    # Salvageable enough to parse as JSON object with tasks key shape attempt.
    # Full array brackets may still be missing — parse honesty preserved if invalid.
    assert cleaned.startswith('{"tasks":')


def test_sanitize_raw_tool_arguments_noop_on_clean_json():
    from agentcore.runtime.engine.tool_protocol_sanitize import (
        sanitize_raw_tool_arguments,
    )

    raw = '{"tasks": [{"role": "研究员", "task": "调研"}]}'
    assert sanitize_raw_tool_arguments(raw) == raw


@pytest.mark.asyncio
async def test_execute_tools_sanitizes_name_and_runs():
    class _Echo:
        def __init__(self) -> None:
            self.seen: dict | None = None

        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="web_search",
                description="stub",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}},
                category=ToolCategory.SEARCH,
            )

        async def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
            self.seen = arguments
            return ToolResult(tool_call_id="", success=True, output="ok")

    echo = _Echo()
    reg = ToolRegistry()
    reg.register(echo)
    tc = ToolCall(
        id="c1",
        function=ToolCallFunction(
            name="web_search</longcat_arg_key>",
            arguments=json.dumps({"query": "茉莉奶白 LV</longcat_arg_value>"}, ensure_ascii=False),
        ),
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )
    msgs, terminal, attempts = await execute_tools(
        [tc], reg, ctx, EventSink(), approval_gate=None
    )
    assert terminal is None
    assert attempts[0].success is True
    assert tc.function.name == "web_search"
    assert echo.seen is not None
    assert echo.seen["query"] == "茉莉奶白 LV"
    assert msgs[0].content == "ok"


@pytest.mark.asyncio
async def test_execute_tools_not_found_mentions_protocol_strip():
    reg = ToolRegistry()
    tc = ToolCall(
        id="c1",
        function=ToolCallFunction(name="web_query</longcat_arg_key>", arguments="{}"),
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )
    msgs, _, attempts = await execute_tools([tc], reg, ctx, EventSink(), approval_gate=None)
    assert attempts[0].success is False
    assert "not found" in msgs[0].content
    assert "协议标签" in msgs[0].content


@pytest.mark.asyncio
async def test_execute_tools_worker_only_miss_is_actionable_policy():
    """CEO-style empty registry calling run must not look like a typo."""
    reg = ToolRegistry()
    tc = ToolCall(
        id="c1",
        function=ToolCallFunction(name="run", arguments="{}"),
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="ceo",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )
    msgs, _, attempts = await execute_tools([tc], reg, ctx, EventSink(), approval_gate=None)
    assert attempts[0].success is False
    assert attempts[0].policy_failure is True
    assert "未装配" in msgs[0].content
    assert "not found" not in msgs[0].content.lower()


@pytest.mark.asyncio
async def test_execute_tools_file_write_miss_is_not_assembled():
    reg = ToolRegistry()
    tc = ToolCall(
        id="c1",
        function=ToolCallFunction(name="file_write", arguments="{}"),
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="ceo",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )
    msgs, _, attempts = await execute_tools([tc], reg, ctx, EventSink(), approval_gate=None)
    assert attempts[0].success is False
    assert attempts[0].policy_failure is True
    assert "未装配" in msgs[0].content
    assert "not found" not in msgs[0].content.lower()


def test_debrief_strips_protocol_tags_from_handoff_fields():
    transcript = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="h1",
                    function=ToolCallFunction(
                        name="handoff",
                        arguments=json.dumps(
                            {
                                "summary": "交叉验证完成<longcat_tool_call>",
                                "key_points": ["共识</longcat_arg_key>"],
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
            ],
        )
    ]
    debrief = debrief_from_transcript(transcript)
    assert debrief is not None
    assert debrief["summary"] == "交叉验证完成"
    assert debrief["key_points"] == ["共识"]


def test_parse_empty_arguments_repairs_to_empty_object():
    """Empty / whitespace is a wire repair, not a silent ``None``.

    Leaving ``function.arguments=""`` 400s the next OpenAI-compatible turn
    (OpenCode Go: must be valid JSON). Callers must rewrite the slot to ``"{}"``.
    """
    from agentcore.runtime.engine.tool_protocol_sanitize import parse_tool_call_arguments

    for raw in ("", "   ", "\n"):
        parsed, repaired = parse_tool_call_arguments(raw, tool_name="grep")
        assert parsed == {}
        assert repaired == "{}"
        assert json.loads(repaired) == {}


def test_parse_trailing_extra_brace_keeps_object():
    from agentcore.runtime.engine.tool_protocol_sanitize import parse_tool_call_arguments

    raw = '{"message":"先确认——","questions":[{"id":"q1"}],"default":"A"}}'
    parsed, repaired = parse_tool_call_arguments(raw, tool_name="ask_user")
    assert isinstance(parsed, dict)
    assert parsed["message"] == "先确认——"
    assert parsed["default"] == "A"
    assert len(parsed["questions"]) == 1
    assert repaired is not None
    assert json.loads(repaired) == parsed


def test_parse_does_not_close_truncated_json():
    """截断 ≠ 尾部垃圾：值没写完就诚实失败，不许闭合后冒充成功。

    上一条（尾部多个 ``}``）丢掉的只是垃圾，值本身是完整的。截断相反：模型没发出来的内容
    补不回来，闭合只是给缺失盖章——``file_write`` 会把半截正文落盘并配一张成功回执。
    诚实失败那条路的模型面本来就教「缩短单次参数 / 拆成多次调用」。
    """
    from agentcore.runtime.engine.tool_protocol_sanitize import parse_tool_call_arguments

    with pytest.raises(json.JSONDecodeError):
        parse_tool_call_arguments('{"query":"半截搜索词', tool_name="web_search")
    with pytest.raises(json.JSONDecodeError):
        parse_tool_call_arguments('{"path":"a.md","content":"半截正文', tool_name="file_write")


def test_parse_unrepairable_raises():
    from agentcore.runtime.engine.tool_protocol_sanitize import parse_tool_call_arguments

    with pytest.raises(json.JSONDecodeError):
        parse_tool_call_arguments("{@@@}", tool_name="ask_user")


def test_salvage_handoff_quotes_bare_next_steps():
    from agentcore.runtime.engine.tool_protocol_sanitize import (
        salvage_handoff_raw_arguments,
    )

    raw = '{"summary":"调研完成","key_points":["a"],"next_steps": 请下游去做某事}'
    out = salvage_handoff_raw_arguments(raw, tool_name="handoff")
    assert out is not None
    parsed = json.loads(out)
    assert isinstance(parsed, dict)
    assert parsed["summary"] == "调研完成"
    assert parsed["key_points"] == ["a"]
    assert parsed["next_steps"] == "请下游去做某事"
    assert isinstance(parsed["next_steps"], str)


def test_salvage_handoff_noop_on_clean_json():
    from agentcore.runtime.engine.tool_protocol_sanitize import (
        salvage_handoff_raw_arguments,
    )

    raw = json.dumps(
        {
            "summary": "干净结论",
            "key_points": ["a"],
            "next_steps": "已引号包裹",
        },
        ensure_ascii=False,
    )
    assert salvage_handoff_raw_arguments(raw, tool_name="handoff") is None
    # Clean JSON also must remain loadable without salvage.
    assert json.loads(raw)["next_steps"] == "已引号包裹"


def test_salvage_handoff_rejects_unsalvageable_garbage():
    from agentcore.runtime.engine.tool_protocol_sanitize import (
        salvage_handoff_raw_arguments,
    )

    assert salvage_handoff_raw_arguments("not-json-at-all", tool_name="handoff") is None
    assert salvage_handoff_raw_arguments("{@@@}", tool_name="handoff") is None
    assert salvage_handoff_raw_arguments('{"summary":', tool_name="web_search") is None


def test_salvage_handoff_closes_truncated_string_with_summary():
    from agentcore.runtime.engine.tool_protocol_sanitize import (
        salvage_handoff_raw_arguments,
    )

    raw = '{"summary": "交叉验证尚未写完'
    out = salvage_handoff_raw_arguments(raw, tool_name="handoff")
    assert out is not None
    parsed = json.loads(out)
    assert isinstance(parsed, dict)
    assert "summary" in parsed
    assert isinstance(parsed["summary"], str)
    assert "交叉验证" in parsed["summary"]


def test_salvage_handoff_quotes_bare_assumptions_and_summary():
    from agentcore.runtime.engine.tool_protocol_sanitize import (
        salvage_handoff_raw_arguments,
    )

    raw = '{"summary": 一句话结论, "assumptions": 数据可能过时}'
    out = salvage_handoff_raw_arguments(raw, tool_name="handoff")
    assert out is not None
    parsed = json.loads(out)
    assert parsed["summary"] == "一句话结论"
    assert parsed["assumptions"] == "数据可能过时"
