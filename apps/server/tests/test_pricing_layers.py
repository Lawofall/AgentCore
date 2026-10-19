"""Pricing layers: one curated CNY card; Flash platform sells at official × 0.25."""

from __future__ import annotations

from agentcore.llm.model_selection import _chrome_model
from agentcore.llm.pricing import PLATFORM_RELAY_GLM_52, calculate_cost
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.costing import priced_call_cost


def _usage(**kw: int) -> TokenUsage:
    return TokenUsage(**kw)


def test_user_unknown_model_is_unpriced_not_default_fallback():
    usage = _usage(input_tokens=1_000_000, cache_miss_tokens=1_000_000, output_tokens=1000)
    cost = calculate_cost("totally-unknown-xyz", usage, credential_source="user")
    assert cost.pricing_source == "unpriced"
    assert cost.total == 0
    assert cost.credential_source == "user"
    # Platform falls back to glm-5.2 (CNY curated default).
    platform = calculate_cost("totally-unknown-xyz", usage, credential_source="platform")
    assert platform.total == calculate_cost(PLATFORM_RELAY_GLM_52, usage).total
    assert platform.pricing_source == "curated"


def test_user_known_model_uses_curated_nominal():
    usage = _usage(input_tokens=1_000_000, cache_miss_tokens=1_000_000, output_tokens=1_000_000)
    cost = calculate_cost(DEEPSEEK_V4_FLASH, usage, credential_source="user")
    platform = calculate_cost(DEEPSEEK_V4_FLASH, usage, credential_source="platform")
    assert cost.pricing_source == "curated"
    assert cost.currency == "CNY"
    assert platform.pricing_source == "curated"
    # BYOK meters the official card; platform Flash sells at official × 0.25.
    assert cost.total == 4 * platform.total
    assert platform.total > 0


def test_user_nominal_does_not_enter_billed_nano():
    usage = _usage(input_tokens=10_000, cache_miss_tokens=10_000, output_tokens=100)
    call = priced_call_cost(
        model=DEEPSEEK_V4_FLASH, usage=usage, role="captain", credential_source="user"
    )
    assert call.cost_total_nano == 0
    assert call.cost_estimated_nano > 0
    assert call.cost["pricing_source"] == "curated"
    assert call.cost["total"] == call.cost_estimated_nano
    platform = priced_call_cost(
        model=PLATFORM_RELAY_GLM_52, usage=usage, role="captain", credential_source="platform"
    )
    assert platform.cost_total_nano > 0
    assert platform.cost_estimated_nano == 0
    assert platform.cost["pricing_source"] == "curated"


def test_chrome_model_pins_background_then_platform(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "platform_background_model", "platform-bg")
    monkeypatch.setattr(settings, "platform_model", "chat-model")
    assert _chrome_model() == "platform-bg"
    monkeypatch.setattr(settings, "platform_background_model", "")
    assert _chrome_model() == "chat-model"
