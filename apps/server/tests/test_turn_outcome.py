"""Turn-level result quality: aggregate existing bits; never invent heuristics."""

from __future__ import annotations

from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.events import (
    EventSink,
    FinishReason,
    delivery_status,
    message_end,
    run_failed,
    tool_use_end,
)
from agentcore.runtime.turn.outcome import (
    coerce_produced_outcome,
    events_have_partial_product,
    last_delegate_tool_output,
    last_delegate_tool_output_from_events,
    resolve_turn_outcome,
    salvage_captain_delegate_reply,
)
from agentcore.tools.file_products import FILE_PRODUCTS_MARKER_PREFIX
from agentcore.tools.protocol import TOOL_AUDIENCE_CEO


def test_coerce_produces_paused_when_explicit():
    assert coerce_produced_outcome("paused") == "paused"
    assert coerce_produced_outcome("ok") == "ok"
    assert coerce_produced_outcome("partial") == "partial"
    assert coerce_produced_outcome("error") == "error"
    assert coerce_produced_outcome("nope") is None
    assert coerce_produced_outcome(None) is None


def test_resolve_running_is_none():
    assert (
        resolve_turn_outcome(
            events=[{"type": "delivery_status", "payload": {"state": "partial"}}],
            running=True,
        )
        is None
    )


def test_resolve_paused_finish_without_explicit_is_none():
    assert resolve_turn_outcome(finish_reason=FinishReason.PAUSED) is None


def test_resolve_explicit_paused_wins():
    assert resolve_turn_outcome(explicit="paused", finish_reason="paused") == "paused"
    assert resolve_turn_outcome(explicit="paused", has_error=True) == "paused"


def test_resolve_explicit_wins_over_error():
    events = [{"type": "error", "payload": {"code": "LLM_RATE_LIMIT", "message": "x"}}]
    assert (
        resolve_turn_outcome(events=events, explicit="partial", has_error=True)
        == "partial"
    )


def test_resolve_delivery_status_partial():
    events = [{"type": "delivery_status", "payload": {"state": "partial"}}]
    assert events_have_partial_product(events)
    assert resolve_turn_outcome(events=events, finish_reason=FinishReason.END_TURN) == (
        "partial"
    )


def test_resolve_product_landed():
    events = [{"type": "run_failed", "payload": {"product_landed": True}}]
    assert resolve_turn_outcome(events=events, finish_reason=FinishReason.ERROR) == (
        "partial"
    )


def test_resolve_delegate_partial_failure():
    events = [{"type": "tool_use_end", "payload": {"partial_failure": True}}]
    assert resolve_turn_outcome(events=events) == "partial"


def test_resolve_error_without_partial_bits():
    assert (
        resolve_turn_outcome(finish_reason=FinishReason.ERROR, has_error=True) == "error"
    )


def test_resolve_ok_on_clean_end():
    assert resolve_turn_outcome(finish_reason=FinishReason.END_TURN) == "ok"


def test_salvage_from_messages_strips_markers():
    body = (
        f"已落盘 订单.csv。\n{FILE_PRODUCTS_MARKER_PREFIX}[]-->"
        "<!--agentcore:tool_failed-->"
    )
    messages = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="dc1",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
        ),
        LLMMessage(role="tool", content=body, tool_call_id="dc1"),
    ]
    assert last_delegate_tool_output(messages) == "已落盘 订单.csv。"
    assert (
        salvage_captain_delegate_reply(
            final_content="", messages=messages, role="captain"
        )
        == "已落盘 订单.csv。"
    )
    assert (
        salvage_captain_delegate_reply(
            final_content="CEO 已开口", messages=messages, role="captain"
        )
        == ""
    )
    assert (
        salvage_captain_delegate_reply(final_content="", messages=messages, role="worker")
        == ""
    )


def test_salvage_from_events_last_delegate_only():
    sink = EventSink()
    sink.emit(tool_use_end("t1", "web_search", success=True, output="搜索摘要"))
    sink.emit(
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output="已落盘 订单.csv、明细.csv、汇总.csv。",
            partial_failure=True,
        )
    )
    assert last_delegate_tool_output_from_events(sink.history_snapshot()).startswith(
        "已落盘"
    )


def test_salvage_refuses_ceo_audience_delegate_echo():
    """Coordination host echo is CEO-audience — not a user-bubble deliverable."""
    echo = (
        "【团队已启动·协调模式】已派出 2 名队员（调研、写手）；"
        "系统已豁免——派完若结束本回合。"
    )
    messages = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="dc1",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content=echo,
            tool_call_id="dc1",
            audience=TOOL_AUDIENCE_CEO,
        ),
    ]
    assert last_delegate_tool_output(messages) == ""
    assert (
        salvage_captain_delegate_reply(
            final_content="", messages=messages, role="captain"
        )
        == ""
    )

    sink = EventSink()
    sink.emit(
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output=echo,
            audience=TOOL_AUDIENCE_CEO,
        )
    )
    assert last_delegate_tool_output_from_events(sink.history_snapshot()) == ""


def test_salvage_does_not_fall_back_past_ceo_audience_delegate():
    """Last non-empty delegate is orchestration — do not reuse an older synthesis."""
    messages = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="old",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                ),
                ToolCall(
                    id="new",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                ),
            ],
        ),
        LLMMessage(role="tool", content="已落盘 订单.csv。", tool_call_id="old"),
        LLMMessage(
            role="tool",
            content="【团队已启动·协调模式】系统已豁免",
            tool_call_id="new",
            audience=TOOL_AUDIENCE_CEO,
        ),
    ]
    assert last_delegate_tool_output(messages) == ""

    sink = EventSink()
    sink.emit(
        tool_use_end(
            "old", "delegate", success=True, output="已落盘 订单.csv。"
        )
    )
    sink.emit(
        tool_use_end(
            "new",
            "delegate",
            success=True,
            output="【团队已启动·协调模式】系统已豁免",
            audience=TOOL_AUDIENCE_CEO,
        )
    )
    assert last_delegate_tool_output_from_events(sink.history_snapshot()) == ""


def test_tool_use_end_omits_audience_unless_ceo():
    from agentcore.runtime.events.payloads.chat import ToolUseEndPayload

    plain = tool_use_end("t1", "delegate", success=True, output="已落盘 订单.csv。")
    assert "audience" not in plain.payload
    ToolUseEndPayload.model_validate(plain.payload)
    ceo = tool_use_end(
        "t1",
        "delegate",
        success=True,
        output="【团队已启动】",
        audience=TOOL_AUDIENCE_CEO,
    )
    assert ceo.payload["audience"] == TOOL_AUDIENCE_CEO
    ToolUseEndPayload.model_validate(ceo.payload)


def test_resolve_from_live_sse_objects():
    sink = EventSink()
    sink.emit(
        delivery_status(
            execution_id="e1",
            state="partial",
            summary="已交付 3 个文件；1 项未完成",
            delivered_files=["订单.csv"],
            gaps=[{"role": "数据分析", "description": "限流"}],
            actions=[],
        )
    )
    sink.emit(run_failed("r1", "w1", "429", product_landed=True))
    sink.emit(message_end(FinishReason.DEGRADED, outcome="partial"))
    assert resolve_turn_outcome(events=sink.history_snapshot()) == "partial"
