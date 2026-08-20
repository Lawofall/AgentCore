"""Unit tests for blocking ask_user content absorption and args parse honesty."""

import json
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from agentcore.core.types import ToolCategory, ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.engine.ask_user_absorb import (
    absorb_blocking_ask_user_content,
    prepare_blocking_ask_user_tool_calls,
)
from agentcore.runtime.engine.tool_exec import execute_tools
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.facts import FactKind, LlmCallFact, TurnFactLog, current_fact_log
from agentcore.runtime.loop_controller import ToolAttempt
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

# Extra data class of trace 0a71145959634304a690a5649c31ada5:
# legal JSON object + a trailing `}` (json.loads → Extra data; 3 options + default).
_TRACE_TRAILING_BRACE_ARGS = (
    '{"message":"先确认这三件事再开工——","questions":['
    '{"id":"q1","prompt":"主体是谁？","kind":"choice",'
    '"options":[{"label":"A公司"},{"label":"B公司"}],"default":"A公司"},'
    '{"id":"q2","prompt":"交付形态？","kind":"choice",'
    '"options":[{"label":"调研报告"},{"label":"简报"}],"default":"调研报告"},'
    '{"id":"q3","prompt":"篇幅？","kind":"choice",'
    '"options":[{"label":"约3千字"},{"label":"约8千字"}],"default":"约3千字"}'
    '],"default":"A公司"}'
    "}"
)


def _ask_user_call(*, message: str = "") -> ToolCall:
    args: dict = {}
    if message:
        args["message"] = message
    return ToolCall(
        id="call_ask",
        function=ToolCallFunction(name="ask_user", arguments=json.dumps(args)),
    )


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


class _AskUserStub:
    def __init__(self) -> None:
        self.executed = False

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="ask_user",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        self.executed = True
        return ToolResult(
            tool_call_id="",
            success=True,
            output="",
            effect=ToolEffect.SUSPEND,
        )


def test_prepare_injects_round_content_when_message_empty():
    calls, folded = prepare_blocking_ask_user_tool_calls(
        [_ask_user_call()],
        "帮你分析一下选项：",
    )
    args = json.loads(calls[0].function.arguments)
    assert args["message"] == "帮你分析一下选项："
    assert folded is True


def test_prepare_keeps_dispatch_started_framing_verbatim():
    """方案 2：引擎不改卡文案；假开工话术交给提示词。"""
    kickoff = "好，派 3 个 worker 开工高规格版："
    calls, folded = prepare_blocking_ask_user_tool_calls(
        [_ask_user_call()],
        kickoff,
    )
    args = json.loads(calls[0].function.arguments)
    assert args["message"] == kickoff
    assert folded is True


def test_prepare_keeps_install_ready_framing_verbatim():
    ready = "依赖已经装完，派两个队员"
    calls, folded = prepare_blocking_ask_user_tool_calls(
        [_ask_user_call()],
        ready,
    )
    args = json.loads(calls[0].function.arguments)
    assert args["message"] == ready
    assert folded is True


def test_prepare_leaves_explicit_dispatch_message():
    calls, folded = prepare_blocking_ask_user_tool_calls(
        [_ask_user_call(message="已派出团队开工")],
        "正文铺垫",
    )
    args = json.loads(calls[0].function.arguments)
    assert args["message"] == "已派出团队开工"
    assert folded is False


def test_prepare_leaves_explicit_message():
    calls, folded = prepare_blocking_ask_user_tool_calls(
        [_ask_user_call(message="卡片文案")],
        "正文铺垫",
    )
    args = json.loads(calls[0].function.arguments)
    assert args["message"] == "卡片文案"
    assert folded is False


def test_trailing_extra_brace_keeps_questions_and_default():
    """Legal object + trailing `}` must not drop options / default / questions."""
    tc = ToolCall(
        id="call_ask",
        function=ToolCallFunction(name="ask_user", arguments=_TRACE_TRAILING_BRACE_ARGS),
    )
    calls, folded = prepare_blocking_ask_user_tool_calls([tc], "这段正文不得覆盖参数")
    assert folded is False
    args = json.loads(calls[0].function.arguments)
    assert args["message"] == "先确认这三件事再开工——"
    assert args["default"] == "A公司"
    assert len(args["questions"]) == 3
    assert [q["id"] for q in args["questions"]] == ["q1", "q2", "q3"]
    assert args["questions"][0]["default"] == "A公司"


@pytest.mark.asyncio
async def test_unparseable_ask_user_does_not_overwrite_or_suspend():
    """Unrepairable args stay untouched and fail honestly — no body-overwrite hang."""
    raw = "{@@@}"
    tc = ToolCall(
        id="call_ask",
        function=ToolCallFunction(name="ask_user", arguments=raw),
    )
    calls, folded = prepare_blocking_ask_user_tool_calls([tc], "这段正文不得覆盖参数")
    assert folded is False
    assert calls[0].function.arguments == raw

    stub = _AskUserStub()
    reg = ToolRegistry()
    reg.register(stub)
    sink = EventSink()
    with capture_logs() as logs:
        messages, terminal, attempts = await execute_tools(
            calls,
            reg,
            _ctx(),
            sink,
            approval_gate=None,
            run_id="r1",
        )
    assert terminal is None
    assert stub.executed is False
    assert attempts[0].parse_failure is True
    assert attempts[0].success is False
    assert "不是合法 JSON" in (messages[0].content or "")
    starts = [e for e in sink._history if e.type == EventType.TOOL_USE_START]  # noqa: SLF001
    assert starts[0].payload.get("arguments", {}).get("__args_parse_failed__") is True
    assert any(entry.get("event") == "tool.args_parse_failed" for entry in logs)


def test_absorb_only_when_engine_folded_message():
    """Model-owned message keeps the bubble; empty message folds prose and clears it."""
    guide = "先看这三件事再选："
    calls, folded = prepare_blocking_ask_user_tool_calls(
        [_ask_user_call(message="卡片文案")],
        guide,
    )
    assert folded is False
    messages = [
        LLMMessage(role="assistant", content=guide, tool_calls=calls),
    ]
    absorbed = absorb_blocking_ask_user_content(
        messages=messages,
        tool_calls=calls,
        attempts=[ToolAttempt("fp", "ask_user", True)],
        terminal_effect=ToolEffect.SUSPEND,
        emit_reset=lambda _reason: None,
        content_folded=folded,
    )
    assert absorbed is False
    assert messages[-1].content == guide

    calls2, folded2 = prepare_blocking_ask_user_tool_calls([_ask_user_call()], guide)
    assert folded2 is True
    log = TurnFactLog()
    log.record_fact(
        LlmCallFact(
            run_id="cap",
            round_idx=0,
            content=guide,
            tool_calls=[
                {
                    "id": "call_ask",
                    "type": "function",
                    "function": {"name": "ask_user", "arguments": calls2[0].function.arguments},
                }
            ],
        ).to_fact()
    )
    messages2 = [
        LLMMessage(role="user", content="?"),
        LLMMessage(role="assistant", content=guide, tool_calls=calls2),
    ]
    resets: list[str] = []
    token = current_fact_log.set(log)
    try:
        absorbed2 = absorb_blocking_ask_user_content(
            messages=messages2,
            tool_calls=calls2,
            attempts=[ToolAttempt("fp", "ask_user", True)],
            terminal_effect=ToolEffect.SUSPEND,
            emit_reset=resets.append,
            content_folded=folded2,
        )
    finally:
        current_fact_log.reset(token)
    assert absorbed2 is True
    assert messages2[-1].content is None
    assert resets == ["ask_user"]
    llm_facts = [f for f in log.entries() if f["kind"] == FactKind.LLM_CALL.value]
    assert llm_facts[-1]["payload"]["content"] == ""


def test_absorb_clears_assistant_content_and_journal_on_suspend():
    log = TurnFactLog()
    log.record_fact(
        LlmCallFact(
            run_id="cap",
            round_idx=0,
            content="正文铺垫",
            tool_calls=[
                {
                    "id": "call_ask",
                    "type": "function",
                    "function": {"name": "ask_user", "arguments": '{"message": "正文铺垫"}'},
                }
            ],
        ).to_fact()
    )
    messages = [
        LLMMessage(role="user", content="?"),
        LLMMessage(
            role="assistant",
            content="正文铺垫",
            tool_calls=[_ask_user_call(message="正文铺垫")],
        ),
    ]
    resets: list[str] = []

    token = current_fact_log.set(log)
    try:
        absorbed = absorb_blocking_ask_user_content(
            messages=messages,
            tool_calls=[_ask_user_call(message="正文铺垫")],
            attempts=[ToolAttempt("fp", "ask_user", True)],
            terminal_effect=ToolEffect.SUSPEND,
            emit_reset=resets.append,
            content_folded=True,
        )
    finally:
        current_fact_log.reset(token)

    assert absorbed is True
    assert messages[-1].content is None
    assert resets == ["ask_user"]
    llm_facts = [f for f in log.entries() if f["kind"] == FactKind.LLM_CALL.value]
    assert llm_facts[-1]["payload"]["content"] == ""


def test_absorb_noop_when_ask_user_failed():
    messages = [
        LLMMessage(
            role="assistant",
            content="正文",
            tool_calls=[_ask_user_call()],
        ),
    ]
    absorbed = absorb_blocking_ask_user_content(
        messages=messages,
        tool_calls=[_ask_user_call()],
        attempts=[ToolAttempt("fp", "ask_user", False)],
        terminal_effect=ToolEffect.SUSPEND,
        emit_reset=lambda _reason: None,
        content_folded=True,
    )
    assert absorbed is False
    assert messages[-1].content == "正文"
