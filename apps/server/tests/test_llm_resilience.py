"""LLM resilience protocol: layer order + existing-event mapping."""

from __future__ import annotations

from agentcore.llm.resilience import (
    COUNT_KEYS,
    LAYER_ORDER,
    count_key_for_event,
    format_degradation_summary,
    layer_for_event,
    summarize_degradation,
)


def test_layer_order_is_stable() -> None:
    assert LAYER_ORDER == (
        "admission",
        "credential_pool",
        "cooldown",
        "leaf_retry",
        "stream_stall_precommit",
    )
    assert COUNT_KEYS[: len(LAYER_ORDER)] == LAYER_ORDER


def test_event_maps_to_layer() -> None:
    assert layer_for_event({"event": "llm.turn_auth_dead"}) == "admission"
    assert layer_for_event({"event": "billing.call_quota_refused"}) == "admission"
    assert layer_for_event({"event": "llm.rate_limit_no_retry"}) == "cooldown"
    assert layer_for_event({"event": "platform_pool.failover"}) == "credential_pool"
    assert layer_for_event({"event": "llm.call_retried"}) == "leaf_retry"
    assert layer_for_event({"event": "llm.call_retried", "reason": "LLMRateLimitError"}) == (
        "leaf_retry"
    )
    assert layer_for_event({"event": "llm.call_retried", "reason": "stream_stall"}) == (
        "stream_stall_precommit"
    )
    assert layer_for_event({"event": "llm.stream_stalled", "committed": False}) == (
        "stream_stall_precommit"
    )
    assert layer_for_event({"event": "llm.stream_stalled"}) == "stream_stall_precommit"


def test_event_maps_to_extra_or_nothing() -> None:
    assert layer_for_event({"event": "llm.stream_stalled", "committed": True}) is None
    assert count_key_for_event({"event": "llm.stream_stalled", "committed": True}) == (
        "stream_stall_salvage"
    )
    assert layer_for_event({"event": "llm.empty_response"}) is None
    assert count_key_for_event({"event": "llm.empty_response"}) == "empty_response"
    assert count_key_for_event({"event": "tool.web_search_cloud_fallback"}) == (
        "web_search_cloud_fallback"
    )
    assert count_key_for_event({"event": "tool.web_search_cloud_fallback_failed"}) == (
        "web_search_cloud_fallback_failed"
    )
    assert count_key_for_event({"event": "llm.call"}) is None
    assert layer_for_event({"event": "chat.turn_start"}) is None


def test_empty_events_have_no_summary() -> None:
    assert summarize_degradation([]) is None
    assert (
        summarize_degradation([{"event": "llm.call"}, {"event": "chat.turn_complete"}])
        is None
    )
    assert format_degradation_summary({key: 0 for key in COUNT_KEYS}) == ""


def test_degradation_counts_from_events() -> None:
    block = summarize_degradation(
        [
            {"event": "llm.call"},
            {"event": "llm.call_retried", "reason": "upstream_502"},
            {"event": "llm.call_retried", "reason": "stream_stall"},
            {"event": "llm.rate_limit_no_retry"},
            {"event": "platform_pool.failover"},
            {"event": "llm.empty_response"},
            {"event": "llm.stream_stalled", "committed": False},
            {"event": "llm.stream_stalled", "committed": True},
            {"event": "tool.web_search_cloud_fallback"},
            {"event": "tool.web_search_cloud_fallback_failed"},
        ]
    )
    assert block is not None
    assert block["layers"] == list(LAYER_ORDER)
    assert set(block["counts"]) == set(COUNT_KEYS)
    counts = block["counts"]
    assert counts["admission"] == 0
    assert counts["cooldown"] == 1
    assert counts["credential_pool"] == 1
    assert counts["leaf_retry"] == 1
    assert counts["stream_stall_precommit"] == 2  # retried(reason=stall) + stalled(pre)
    assert counts["stream_stall_salvage"] == 1
    assert counts["empty_response"] == 1
    assert counts["web_search_cloud_fallback"] == 1
    assert counts["web_search_cloud_fallback_failed"] == 1
    assert "admission=" not in block["summary"]
    assert block["summary"] == (
        "credential_pool=1 cooldown=1 leaf_retry=1 stream_stall_precommit=2 "
        "stream_stall_salvage=1 empty_response=1 web_search_cloud_fallback=1 "
        "web_search_cloud_fallback_failed=1"
    )
