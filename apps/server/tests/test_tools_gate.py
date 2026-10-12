"""Unit tests for tool-calling soft gate + runtime graceful message."""

from agentcore.llm.provider.protocol import TokenUsage
from agentcore.llm.tools_gate import (
    TOOLS_SOFT_GATE_WARNING,
    TOOLS_UNAVAILABLE_RUNTIME_MESSAGE,
)
from agentcore.runtime.engine.outcome import RoundOutcome
from agentcore.runtime.engine.round import decide_no_tool_round
from agentcore.runtime.events import FinishReason
from agentcore.runtime.loop_controller import LoopController


def test_tools_unavailable_message_when_tools_offered_and_empty():
    controller = LoopController(
        tool_failure_warn=3,
        tool_failure_disable=5,
        unproductive_threshold=3,
        convergence_finalize_rounds=3,
        investigation_tools=frozenset(),
    )
    outcome = RoundOutcome(content="", reasoning="", usage=TokenUsage())
    directive = decide_no_tool_round(
        outcome,
        final_content="",
        controller=controller,
        annotate_citations=False,
        citation_sink=None,
        finish_guard_reworks=0,
        tools_offered=True,
        supports_tools=False,
    )
    assert directive.finish_reason is FinishReason.ERROR
    assert directive.extra_content == TOOLS_UNAVAILABLE_RUNTIME_MESSAGE


def test_tools_unavailable_not_triggered_when_supports_tools_unknown():
    """None = probe inconclusive; soft gate must not fire the hard error copy."""
    controller = LoopController(
        tool_failure_warn=3,
        tool_failure_disable=5,
        unproductive_threshold=3,
        convergence_finalize_rounds=3,
        investigation_tools=frozenset(),
    )
    outcome = RoundOutcome(content="", reasoning="", usage=TokenUsage())
    directive = decide_no_tool_round(
        outcome,
        final_content="",
        controller=controller,
        annotate_citations=False,
        citation_sink=None,
        finish_guard_reworks=0,
        tools_offered=True,
        supports_tools=None,
    )
    assert directive.finish_reason is FinishReason.DEGRADED
    assert directive.extra_content == ""


def test_tools_unavailable_not_used_when_model_produced_text():
    controller = LoopController(
        tool_failure_warn=3,
        tool_failure_disable=5,
        unproductive_threshold=3,
        convergence_finalize_rounds=3,
        investigation_tools=frozenset(),
    )
    outcome = RoundOutcome(content="hello", reasoning="", usage=TokenUsage())
    directive = decide_no_tool_round(
        outcome,
        final_content="hello",
        controller=controller,
        annotate_citations=False,
        citation_sink=None,
        finish_guard_reworks=0,
        tools_offered=True,
    )
    assert directive.finish_reason is None
    assert directive.extra_content == ""


def test_soft_gate_copy_non_empty():
    assert TOOLS_SOFT_GATE_WARNING
    assert TOOLS_UNAVAILABLE_RUNTIME_MESSAGE
