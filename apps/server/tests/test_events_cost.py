"""SSE event-shape tests for the cost fields added to message_end / run_completed.

Pins the wire contract the client folds: the turn total cost rides on
``message_end`` (with the cache split now exposed in usage), and each finished
run lights up a payroll row via ``run_completed`` carrying role / model / usage /
cost. Both completion events stay back-compatible (new fields are additive).
"""

from agentcore.runtime.events import (
    EventType,
    FinishReason,
    message_end,
    run_completed,
)


def test_message_end_exposes_cache_split_and_cost():
    cost = {
        "input": 142_800_000,
        "cached": 2_800_000,
        "output": 280_000_000,
        "total": 422_800_000,
        "currency": "CNY",
    }
    ev = message_end(
        FinishReason.END_TURN,
        input_tokens=2_000_000,
        output_tokens=1_000_000,
        reasoning_tokens=0,
        cache_hit_tokens=1_000_000,
        cache_miss_tokens=1_000_000,
        rounds=3,
        cost=cost,
    )

    assert ev.type is EventType.MESSAGE_END
    usage = ev.payload["usage"]
    # Cache split is now carried (it was dropped before) so the bill is honest.
    assert usage["cache_hit_tokens"] == 1_000_000
    assert usage["cache_miss_tokens"] == 1_000_000
    assert usage["input_tokens"] == 2_000_000
    assert "last_prompt_tokens" not in usage
    # Wire cost is normalized additively: pricing_source rides along (default curated).
    assert ev.payload["cost"] == {**cost, "pricing_source": "curated"}
    assert ev.payload["rounds"] == 3


def test_message_end_carries_captain_last_prompt_when_positive():
    ev = message_end(FinishReason.END_TURN, input_tokens=200_000, last_prompt_tokens=50_000)
    assert ev.payload["usage"]["last_prompt_tokens"] == 50_000
    assert ev.payload["usage"]["input_tokens"] == 200_000


def test_message_end_omits_zero_last_prompt():
    ev = message_end(FinishReason.END_TURN, input_tokens=10)
    assert "last_prompt_tokens" not in ev.payload["usage"]


def test_message_end_stamps_byok_nominal_as_estimated_slice():
    cost = {
        "input": 1000,
        "cached": 0,
        "output": 200,
        "total": 1200,
        "currency": "CNY",
        "pricing_source": "curated",
        "credential_source": "user",
    }
    ev = message_end(FinishReason.END_TURN, cost=cost)
    wired = ev.payload["cost"]
    assert wired["total"] == 1200
    assert wired["estimated_total"] == 1200
    assert wired["estimated_currency"] == "CNY"
    assert "credential_source" not in wired


def test_message_end_omits_usage_when_unmetered():
    """Re-pause / terminal resume must not publish a zero meter."""
    ev = message_end(FinishReason.PAUSED, duration_ms=12, include_usage=False)
    assert "usage" not in ev.payload
    assert "rounds" not in ev.payload
    assert ev.payload["finish_reason"] is FinishReason.PAUSED
    assert ev.payload["cost"] is None
    assert ev.payload["duration_ms"] == 12


def test_message_end_cost_defaults_to_none_on_error_path():
    # The error / not-found paths emit message_end with no cost (no turn ran).
    ev = message_end(FinishReason.ERROR)
    assert ev.payload["cost"] is None
    assert ev.payload["usage"]["cache_hit_tokens"] == 0


def test_message_end_exposes_collab_metrics():
    collab = {
        "boundary_yields": 1,
        "scope_signals": 2,
        "revises": 1,
        "escalations": 3,
        "audit_drops": 2,
    }
    ev = message_end(FinishReason.END_TURN, collab=collab)
    assert ev.payload["collab"] == collab


def test_message_end_omits_collab_when_none():
    ev = message_end(FinishReason.END_TURN)
    assert "collab" not in ev.payload


def test_message_end_carries_explicit_duration_ms():
    ev = message_end(FinishReason.END_TURN, duration_ms=12_345)
    assert ev.payload["duration_ms"] == 12_345


def test_message_end_omits_duration_without_probe():
    # No TurnLatencyProbe bound → field absent (old clients / vectors stay compatible).
    ev = message_end(FinishReason.END_TURN)
    assert "duration_ms" not in ev.payload


def test_message_end_auto_fills_duration_from_probe():
    from agentcore.runtime.turn.latency import bind_turn_latency, reset_turn_latency

    probe, token = bind_turn_latency()
    try:
        probe.anchor_mono -= 2.5  # ~2500ms elapsed
        ev = message_end(FinishReason.END_TURN)
        assert ev.payload["duration_ms"] >= 2500
        assert "generation_ms" not in ev.payload
    finally:
        reset_turn_latency(token)


def test_message_end_auto_fills_generation_from_probe():
    from agentcore.runtime.turn.latency import bind_turn_latency, reset_turn_latency

    probe, token = bind_turn_latency()
    try:
        probe.add_generation_ms(880)
        ev = message_end(FinishReason.END_TURN)
        assert ev.payload["generation_ms"] == 880
    finally:
        reset_turn_latency(token)


def test_message_end_omits_generation_without_probe():
    ev = message_end(FinishReason.END_TURN)
    assert "generation_ms" not in ev.payload


def test_run_completed_carries_role_model_usage_cost():
    usage = {"input": 100, "output": 50, "reasoning": 10, "cache_hit": 60, "cache_miss": 40}
    cost = {"input": 999, "cached": 111, "output": 222, "total": 1221, "currency": "USD"}
    ev = run_completed(
        "run-1",
        "agent-1",
        output_summary="done",
        duration_ms=1234,
        role="member",
        model="deepseek-v4-pro",
        usage=usage,
        cost=cost,
    )

    assert ev.type is EventType.RUN_COMPLETED
    assert ev.payload["role"] == "member"
    assert ev.payload["model"] == "deepseek-v4-pro"
    assert ev.payload["usage"] == usage
    assert ev.payload["cost"] == {**cost, "pricing_source": "curated"}


def test_run_completed_keeps_last_prompt_from_token_usage():
    usage = {
        "input": 100,
        "output": 10,
        "reasoning": 0,
        "cache_hit": 0,
        "cache_miss": 100,
        "last_prompt": 80,
    }
    ev = run_completed("r1", "a1", output_summary="ok", duration_ms=1, usage=usage)
    assert ev.payload["usage"]["last_prompt"] == 80
    from agentcore.runtime.events.payloads.shared import UsageBreakdown

    UsageBreakdown.model_validate(ev.payload["usage"])


def test_run_completed_stamps_byok_nominal_as_estimated_slice():
    cost = {
        "input": 1000,
        "cached": 0,
        "output": 200,
        "total": 1200,
        "currency": "CNY",
        "pricing_source": "curated",
        "credential_source": "user",
    }
    ev = run_completed(
        "run-1",
        "agent-1",
        output_summary="done",
        duration_ms=1,
        cost=cost,
    )
    wired = ev.payload["cost"]
    assert wired["total"] == 1200
    assert wired["estimated_total"] == 1200
    assert wired["estimated_currency"] == "CNY"
    assert wired["pricing_source"] == "curated"
    assert "credential_source" not in wired


def test_run_completed_defaults_to_full_zeroed_shapes():
    # A synthetic / un-metered run still yields a complete, typed object (zeros),
    # never a bare {} — the client renders zeros as「—」(§七5) without guarding.
    ev = run_completed("s1", "a1", output_summary="ok", duration_ms=1)

    assert ev.payload["role"] == "member"
    assert ev.payload["model"] == ""
    assert ev.payload["usage"] == {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache_hit": 0,
        "cache_miss": 0,
    }
    assert ev.payload["cost"] == {
        "input": 0,
        "cached": 0,
        "output": 0,
        "total": 0,
        "currency": "CNY",
        "pricing_source": "curated",
    }


def test_run_completed_carries_output_files_when_present():
    ev = run_completed(
        "run-1",
        "agent-1",
        output_summary="报告就绪",
        duration_ms=10,
        output_files=["draft.md", "out/report.md"],
    )
    assert ev.payload["output_files"] == ["draft.md", "out/report.md"]
    assert "output_files" not in run_completed(
        "r", "a", output_summary="", duration_ms=1
    ).payload


def test_run_completed_carries_reasoning_effort_when_present():
    ev = run_completed(
        "run-1",
        "agent-1",
        output_summary="ok",
        duration_ms=10,
        reasoning_effort="low",
    )
    assert ev.payload["reasoning_effort"] == "low"
    assert "reasoning_effort" not in run_completed(
        "r", "a", output_summary="", duration_ms=1
    ).payload
