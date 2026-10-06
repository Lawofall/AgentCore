"""Tests for the single pricing function (llm.pricing).

Pins the money math: input is split by cache hit/miss, output includes
reasoning, everything is integer nano-CNY, and an unknown model degrades to
glm-5.2 instead of crashing. Curated CNY cards: glm-5.2 (bigmodel.cn) /
doubao-seed turbo (火山 0–32K). Flash = official DeepSeek CNY (peak/off-peak).
"""

from datetime import UTC, datetime

import pytest
from structlog.testing import capture_logs

from agentcore.config import settings
from agentcore.llm.pricing import (
    DOUBAO_SEED_TURBO,
    NANO_PER_CNY,
    PLATFORM_GPT_4O,
    PLATFORM_RELAY_GLM_52,
    PLATFORM_RELAY_GROK_45,
    PLATFORM_RELAY_KIMI_K25,
    cache_savings,
    calculate_cost,
    curated_pricing_for,
    has_curated_pricing,
    nano_to_yuan,
    pricing_for_model,
    project_cache_miss_tokens,
    reconcile_cache_miss_tokens,
)
from agentcore.llm.profiles import (
    DEEPSEEK_V4_FLASH,
    DEEPSEEK_V4_FLASH_FREE,
    DEEPSEEK_V4_PRO,
    DEEPSEEK_V41_FLASH,
    OPENCODE_GO_V41_FLASH,
)
from agentcore.llm.provider.protocol import TokenUsage


@pytest.fixture(autouse=True)
def _platform_billing(monkeypatch):
    """Platform pricing table tests assume operator billing, not BYOK zero-cost."""
    monkeypatch.setattr(settings, "billing_mode", "platform")


def _usage(**kw: int) -> TokenUsage:
    return TokenUsage(**kw)


# --- calculate_cost: per-1M CNY prices land on exact nano-CNY ---


def test_glm_one_million_each_line():
    # 1M cache_miss @ ¥8, 1M output @ ¥28 → exact nano-CNY.
    usage = _usage(
        input_tokens=1_000_000,
        cache_miss_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    cost = calculate_cost(PLATFORM_RELAY_GLM_52, usage)
    assert cost.input == 8_000_000_000  # ¥8
    assert cost.cached == 0
    assert cost.output == 28_000_000_000  # ¥28
    assert cost.total == 36_000_000_000  # ¥36
    assert cost.currency == "CNY"
    assert cost.pricing_source == "curated"


def test_input_splits_cache_hit_vs_miss():
    # 1M hit @ ¥2 + 1M miss @ ¥8. `cached` re-states just the hit portion.
    usage = _usage(
        input_tokens=2_000_000,
        cache_hit_tokens=1_000_000,
        cache_miss_tokens=1_000_000,
    )
    cost = calculate_cost(PLATFORM_RELAY_GLM_52, usage)
    assert cost.cached == 2_000_000_000  # ¥2
    assert cost.input == 2_000_000_000 + 8_000_000_000
    assert cost.output == 0
    assert cost.total == cost.input


def test_unknown_model_falls_back_to_glm():
    usage = _usage(output_tokens=1_000_000)
    assert calculate_cost("totally-unknown", usage) == calculate_cost(PLATFORM_RELAY_GLM_52, usage)


def test_doubao_priced_at_vendor_cny_not_glm():
    # 豆包 must price at Volcengine 0–32K CNY rates, NOT degrade to glm.
    usage = _usage(input_tokens=1_000_000, cache_miss_tokens=1_000_000, output_tokens=1_000_000)
    cost = calculate_cost(DOUBAO_SEED_TURBO, usage)
    assert cost.input == 800_000_000  # ¥0.8/1M
    assert cost.cached == 0
    assert cost.output == 8_000_000_000  # ¥8/1M
    assert cost != calculate_cost(PLATFORM_RELAY_GLM_52, usage)


def test_doubao_does_not_log_pricing_fallback():
    with capture_logs() as logs:
        calculate_cost(DOUBAO_SEED_TURBO, _usage(input_tokens=26163, output_tokens=1499))
    assert _fallback_logs(logs) == []


# --- curated date-stem match (M14): dated revision keeps sibling price ---


def test_curated_exact_match_kind():
    card, kind, matched = curated_pricing_for(DOUBAO_SEED_TURBO)
    assert card is not None
    assert kind == "exact"
    assert matched == DOUBAO_SEED_TURBO


def test_doubao_dated_revision_keeps_curated_price():
    # Newer Volcengine date suffix must NOT silently degrade to glm-5.2.
    newer = "doubao/doubao-seed-2-1-turbo-260715"
    usage = _usage(input_tokens=1_000_000, cache_miss_tokens=1_000_000, output_tokens=1_000_000)
    exact = calculate_cost(DOUBAO_SEED_TURBO, usage)
    stemmed = calculate_cost(newer, usage)
    assert stemmed == exact
    assert stemmed.pricing_source == "curated"
    assert stemmed != calculate_cost(PLATFORM_RELAY_GLM_52, usage)
    card, kind, matched = curated_pricing_for(newer)
    assert kind == "date_stem"
    assert matched == DOUBAO_SEED_TURBO
    assert card == curated_pricing_for(DOUBAO_SEED_TURBO)[0]
    assert has_curated_pricing(newer)


def test_doubao_date_stem_logs_prefix_match_not_fallback():
    newer = "doubao/doubao-seed-2-1-turbo-260715"
    with capture_logs() as logs:
        calculate_cost(newer, _usage(input_tokens=100, output_tokens=50))
    assert _fallback_logs(logs) == []
    prefix = [e for e in logs if e["event"] == "cost.pricing_prefix_match"]
    assert len(prefix) == 1
    assert prefix[0]["model"] == newer
    assert prefix[0]["matched_key"] == DOUBAO_SEED_TURBO
    assert prefix[0]["match_kind"] == "date_stem"


def test_undated_curated_does_not_absorb_fake_date_suffix():
    # glm-5.2 has no date in the curated key → stem index must not claim glm-5.2-*.
    fake = "glm-5.2-260715"
    assert curated_pricing_for(fake) == (None, None, None)
    assert not has_curated_pricing(fake)
    with capture_logs() as logs:
        calculate_cost(fake, _usage(input_tokens=100, output_tokens=50))
    assert len(_fallback_logs(logs)) == 1


def test_unknown_model_still_falls_back_after_stem_miss():
    usage = _usage(output_tokens=1_000_000)
    assert calculate_cost("totally-unknown-260715", usage) == calculate_cost(
        PLATFORM_RELAY_GLM_52, usage
    )
    assert curated_pricing_for("totally-unknown-260715") == (None, None, None)


# --- cache-split reconciliation: the input bill always matches the prompt ---


def test_cache_split_missing_bills_whole_prompt_as_miss():
    usage = _usage(input_tokens=1_000_000, cache_hit_tokens=0, cache_miss_tokens=0)
    cost = calculate_cost(PLATFORM_RELAY_GLM_52, usage)
    assert cost.cached == 0
    assert cost.input == 8_000_000_000  # 1M @ ¥8 miss, not 0
    assert cost.total == 8_000_000_000


def test_cache_split_partial_reconciles_remainder_as_miss():
    usage = _usage(input_tokens=1_000_000, cache_hit_tokens=300_000, cache_miss_tokens=0)
    cost = calculate_cost(PLATFORM_RELAY_GLM_52, usage)
    cached = calculate_cost(PLATFORM_RELAY_GLM_52, _usage(cache_hit_tokens=300_000)).cached
    miss = calculate_cost(PLATFORM_RELAY_GLM_52, _usage(cache_miss_tokens=700_000)).input
    assert cost.cached == cached
    assert cost.input == cached + miss


def test_native_cache_split_is_a_noop():
    with_input = calculate_cost(
        PLATFORM_RELAY_GLM_52,
        _usage(input_tokens=2_000_000, cache_hit_tokens=1_000_000, cache_miss_tokens=1_000_000),
    )
    split_only = calculate_cost(
        PLATFORM_RELAY_GLM_52,
        _usage(cache_hit_tokens=1_000_000, cache_miss_tokens=1_000_000),
    )
    assert with_input == split_only


def test_project_cache_miss_omitted_split_uses_pricing_guard():
    """BYOK gpt-5.6-sol shape: hit=0 miss=0 input>0 → display miss = whole prompt."""
    assert project_cache_miss_tokens(800, 0, 0) == reconcile_cache_miss_tokens(800, 0, 0)
    assert project_cache_miss_tokens(800, 0, 0) == 800


def test_project_cache_miss_deepseek_true_zero_hit_unchanged():
    """Native DeepSeek 真 0 命中 reports miss=input; projection must not rewrite it."""
    assert project_cache_miss_tokens(800, 0, 800) == 800
    assert project_cache_miss_tokens(100, 20, 80) == 80
    assert project_cache_miss_tokens(100, 60, 0) == 0


def test_from_openai_wire_keeps_omitted_split_as_zero_zero():
    """Parse layer must not fill 0/0 — that shape is the billing-fairness tripwire."""
    usage = TokenUsage.from_openai_wire({"prompt_tokens": 800, "completion_tokens": 40})
    assert usage.cache_hit_tokens == 0
    assert usage.cache_miss_tokens == 0
    assert usage.input_tokens == 800
    assert usage.usage_keys == "completion_tokens,prompt_tokens"


def test_from_openai_wire_records_nested_usage_key_names():
    usage = TokenUsage.from_openai_wire(
        {
            "prompt_tokens": 800,
            "completion_tokens": 40,
            "prompt_tokens_details": {"cached_tokens": 0},
        }
    )
    assert usage.cache_hit_tokens == 0
    assert "prompt_tokens_details.cached_tokens" in usage.usage_keys.split(",")


# --- pricing fallback is observable, not silent ---


def _fallback_logs(logs: list[dict]) -> list[dict]:
    return [e for e in logs if e["event"] == "cost.pricing_fallback"]


def test_unknown_model_logs_pricing_fallback():
    with capture_logs() as logs:
        calculate_cost("totally-unknown", _usage(input_tokens=100, output_tokens=50))
    events = _fallback_logs(logs)
    assert len(events) == 1
    assert events[0]["model"] == "totally-unknown"
    assert events[0]["fallback"] == PLATFORM_RELAY_GLM_52
    assert events[0]["log_level"] == "warning"


def test_known_model_does_not_log_fallback():
    with capture_logs() as logs:
        calculate_cost(PLATFORM_RELAY_GLM_52, _usage(input_tokens=100, output_tokens=50))
    assert _fallback_logs(logs) == []


def test_unknown_model_zero_usage_is_silent():
    with capture_logs() as logs:
        calculate_cost("totally-unknown", _usage())
    assert _fallback_logs(logs) == []


def test_zero_usage_is_zero_cost():
    cost = calculate_cost(PLATFORM_RELAY_GLM_52, _usage())
    assert (cost.input, cost.cached, cost.output, cost.total) == (0, 0, 0, 0)


def test_byok_uses_same_curated_card_not_community():
    usage = _usage(input_tokens=1_000_000, cache_miss_tokens=1_000_000, output_tokens=1_000_000)
    byok = calculate_cost(OPENCODE_GO_V41_FLASH, usage, billing_mode="byok")
    platform = calculate_cost(OPENCODE_GO_V41_FLASH, usage, billing_mode="platform")
    assert byok.pricing_source == "curated"
    assert byok.credential_source == "user"
    assert byok.currency == "CNY"
    assert byok.total == platform.total
    assert platform.pricing_source == "curated"
    user = calculate_cost(OPENCODE_GO_V41_FLASH, usage, credential_source="user")
    assert user.pricing_source == "curated"
    assert user.total == platform.total
    assert calculate_cost(
        OPENCODE_GO_V41_FLASH, usage, credential_source="platform"
    ).pricing_source == ("curated")


def test_pricing_for_model_user_returns_curated_or_none():
    assert pricing_for_model(DEEPSEEK_V4_FLASH, billing_mode="byok") is not None
    assert pricing_for_model(PLATFORM_RELAY_GLM_52, billing_mode="platform") is not None
    assert pricing_for_model(DEEPSEEK_V4_FLASH, credential_source="user") is not None
    assert pricing_for_model("totally-unknown-xyz", credential_source="user") is None
    assert pricing_for_model(PLATFORM_RELAY_GLM_52, credential_source="platform") is not None


def test_small_token_counts_round_half_up():
    # 100 output tokens @ ¥28/1M = 2_800_000 nano exactly; 1 token rounds.
    assert calculate_cost(PLATFORM_RELAY_GLM_52, _usage(output_tokens=100)).output == 2_800_000
    # 1 token: 28 * 1000 = 28_000 nano.
    assert calculate_cost(PLATFORM_RELAY_GLM_52, _usage(output_tokens=1)).output == 28_000


def test_deepseek_flash_official_cny():
    """Flash 名义价 = DeepSeek 中文官价（谷 ¥0.02 / ¥1 / ¥4）。"""
    at = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    usage = _usage(
        input_tokens=1_000_000,
        cache_miss_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    flash = calculate_cost(
        DEEPSEEK_V4_FLASH, usage, credential_source="platform", at=at
    )
    assert flash.input == 1_000_000_000  # ¥1
    assert flash.output == 4_000_000_000  # ¥4
    assert flash.pricing_source == "curated"
    hit = calculate_cost(
        DEEPSEEK_V4_FLASH,
        _usage(input_tokens=1_000_000, cache_hit_tokens=1_000_000),
        credential_source="platform",
        at=at,
    )
    assert hit.cached == 20_000_000  # ¥0.02
    # Zen free SKU meters at official Flash (upstream free; product quota anti-abuse).
    free = calculate_cost(
        DEEPSEEK_V4_FLASH_FREE, usage, credential_source="platform", at=at
    )
    assert free.pricing_source == "curated"
    assert free.input == flash.input
    assert free.output == flash.output
    assert free.total == flash.total
    assert free.total > 0
    go_v41 = calculate_cost(
        OPENCODE_GO_V41_FLASH, usage, credential_source="platform", at=at
    )
    assert go_v41.pricing_source == "curated"
    assert go_v41.total == flash.total
    official = calculate_cost(
        DEEPSEEK_V41_FLASH, usage, credential_source="user", at=at
    )
    assert official.pricing_source == "curated"
    assert official.total == flash.total
    assert official.credential_source == "user"
    go_user = calculate_cost(
        OPENCODE_GO_V41_FLASH, usage, credential_source="user", at=at
    )
    assert go_user.pricing_source == "curated"
    assert go_user.total == flash.total
    assert go_user.credential_source == "user"
    # Retired Pro: no product card. Platform falls back to glm; user is unpriced.
    pro_platform = calculate_cost(DEEPSEEK_V4_PRO, usage, credential_source="platform")
    assert pro_platform.total == calculate_cost(PLATFORM_RELAY_GLM_52, usage).total
    pro_user = calculate_cost(DEEPSEEK_V4_PRO, usage, credential_source="user")
    assert pro_user.pricing_source == "unpriced"
    assert pro_user.total == 0


def test_deepseek_flash_peak_is_double_off_peak():
    usage = _usage(input_tokens=1_000_000, cache_miss_tokens=1_000_000)
    off = calculate_cost(
        DEEPSEEK_V4_FLASH,
        usage,
        credential_source="platform",
        at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    )
    peak = calculate_cost(
        DEEPSEEK_V4_FLASH,
        usage,
        credential_source="platform",
        at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
    )
    assert off.input == 1_000_000_000
    assert peak.input == 2_000_000_000
    assert peak.input == off.input * 2


def test_deepseek_flash_weekend_is_off_peak():
    usage = _usage(input_tokens=1_000_000, cache_miss_tokens=1_000_000)
    saturday_peak_hour = calculate_cost(
        OPENCODE_GO_V41_FLASH,
        usage,
        credential_source="platform",
        at=datetime(2026, 8, 22, 2, 0, tzinfo=UTC),
    )
    weekday_off = calculate_cost(
        OPENCODE_GO_V41_FLASH,
        usage,
        credential_source="platform",
        at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    )
    assert saturday_peak_hour.input == weekday_off.input == 1_000_000_000


def test_flash_go_cost_multiplier_does_not_scale_cny_card(monkeypatch):
    usage = _usage(input_tokens=1_000_000, cache_miss_tokens=1_000_000)
    at = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    one = calculate_cost(DEEPSEEK_V4_FLASH, usage, credential_source="platform", at=at)
    monkeypatch.setattr(settings, "go_cost_multiplier", "4")
    four = calculate_cost(DEEPSEEK_V4_FLASH, usage, credential_source="platform", at=at)
    assert four.input == one.input


def test_non_deepseek_usd_curated_still_withdrawn():
    """gpt-4o / grok / qwen-vl — still no curated CNY card (不上架)."""
    assert has_curated_pricing(DEEPSEEK_V4_FLASH)
    assert has_curated_pricing(DEEPSEEK_V41_FLASH)
    assert has_curated_pricing(OPENCODE_GO_V41_FLASH)
    assert has_curated_pricing(DEEPSEEK_V4_FLASH_FREE)
    assert not has_curated_pricing(DEEPSEEK_V4_PRO)
    assert not has_curated_pricing(PLATFORM_GPT_4O)
    assert not has_curated_pricing(PLATFORM_RELAY_GROK_45)
    assert not has_curated_pricing("qwen-vl-max")
    assert has_curated_pricing(PLATFORM_RELAY_GLM_52)
    # Alias catalog ids (upstream_model remap) do not get their own curated card.
    assert not has_curated_pricing("glm-5.2-alt")
    assert has_curated_pricing(DOUBAO_SEED_TURBO)
    assert has_curated_pricing(PLATFORM_RELAY_KIMI_K25)


def test_kimi_k25_relay_vision_cny_list_price():
    """kimi-k2.5 curated = relay effective ¥0.8 / ¥4 / ¥21 — vision role pricing."""
    usage = _usage(
        input_tokens=2_000_000,
        cache_hit_tokens=1_000_000,
        cache_miss_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    cost = calculate_cost(PLATFORM_RELAY_KIMI_K25, usage, credential_source="platform")
    assert cost.cached == 800_000_000  # ¥0.8
    assert cost.input == 800_000_000 + 4_000_000_000  # hit + miss ¥4
    assert cost.output == 21_000_000_000  # ¥21
    assert cost.total == cost.input + cost.output
    assert cost.pricing_source == "curated"
    """glm-5.2 curated = bigmodel.cn ¥2 / ¥8 / ¥28 — not FX-from-USD."""
    usage = _usage(
        input_tokens=2_000_000,
        cache_hit_tokens=1_000_000,
        cache_miss_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    cost = calculate_cost(PLATFORM_RELAY_GLM_52, usage, credential_source="platform")
    assert cost.cached == 2_000_000_000  # ¥2
    assert cost.input == 2_000_000_000 + 8_000_000_000  # hit + miss ¥8
    assert cost.output == 28_000_000_000  # ¥28
    assert cost.total == cost.input + cost.output
    assert cost.pricing_source == "curated"
    assert cost.currency == "CNY"


def test_nano_per_cny_scale():
    assert NANO_PER_CNY == 1_000_000_000


# --- cache_savings: the「省了多少」彩蛋 ---


def test_cache_savings_is_hit_tokens_times_price_gap():
    usage = _usage(cache_hit_tokens=1_000_000)
    # miss(¥8) − hit(¥2) = ¥6 over 1M tokens.
    assert cache_savings(PLATFORM_RELAY_GLM_52, usage) == 8_000_000_000 - 2_000_000_000


def test_no_cache_hits_means_no_savings():
    assert cache_savings(PLATFORM_RELAY_GLM_52, _usage(cache_miss_tokens=1_000_000)) == 0


# --- nano_to_yuan: nano-CNY → 元展示 ---


def test_nano_to_yuan_rounds_to_fen():
    assert nano_to_yuan(NANO_PER_CNY) == 1.0
    assert nano_to_yuan(140_000_000) == 0.14


def test_nano_to_yuan_zero():
    assert nano_to_yuan(0) == 0.0
