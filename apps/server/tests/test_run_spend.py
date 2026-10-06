"""Cumulative run_spend is the sum of per-call prices, and it is not journaled."""

from agentcore.llm.pricing import PLATFORM_RELAY_GLM_52, calculate_cost
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.engine.run_spend import note_run_spend
from agentcore.runtime.events.sink import EventSink
from agentcore.runtime.events.types import EventType


def test_note_run_spend_sums_calls_and_skips_journal():
    sink = EventSink()
    first = TokenUsage(input_tokens=1_000, output_tokens=10)
    spent = note_run_spend(
        sink,
        run_id="run-1",
        agent_id="worker",
        role="member",
        model=PLATFORM_RELAY_GLM_52,
        call_usage=first,
        total_usage=first,
        spent=None,
    )
    second = TokenUsage(input_tokens=2_000, output_tokens=20)
    total = first + second
    spent = note_run_spend(
        sink,
        run_id="run-1",
        agent_id="worker",
        role="member",
        model=PLATFORM_RELAY_GLM_52,
        call_usage=second,
        total_usage=total,
        spent=spent,
    )
    frames = [e for e in sink.history_snapshot() if e.type is EventType.RUN_SPEND]
    assert len(frames) == 2
    assert frames[0].payload["usage"]["input"] == 1_000
    assert frames[1].payload["usage"]["input"] == 3_000
    assert frames[1].payload["usage"]["output"] == 30
    expected = calculate_cost(PLATFORM_RELAY_GLM_52, first).total + calculate_cost(
        PLATFORM_RELAY_GLM_52, second
    ).total
    assert frames[1].payload["cost"]["total"] == expected
    assert spent is not None and spent.total == expected
    journal = sink.execution_journal() or []
    assert EventType.RUN_SPEND.value not in [row["type"] for row in journal]


def test_note_run_spend_without_run_id_is_silent():
    sink = EventSink()
    spent = note_run_spend(
        sink,
        run_id="",
        agent_id="",
        role="",
        model=PLATFORM_RELAY_GLM_52,
        call_usage=TokenUsage(input_tokens=10),
        total_usage=TokenUsage(input_tokens=10),
        spent=None,
    )
    assert spent is None
    assert sink.history_snapshot() == []
