"""Unit tests for BYOK vendor presets (Moonshot / Kimi catalog seed)."""

from agentcore.llm.byok_provider_presets import (
    BYOK_OFF_PROTOCOL_MODELS,
    BYOK_PROVIDER_PRESETS,
    chat_completions_seed,
    is_opencode_byok_endpoint,
    is_opencode_go_base_url,
    is_opencode_zen_base_url,
    match_byok_provider_preset,
    normalize_byok_base_url,
    off_protocol_kind,
    preset_models_for_base_url,
)


def test_deepseek_preset_includes_vision_exp():
    preset = match_byok_provider_preset("https://api.deepseek.com")
    assert preset is not None
    assert preset.id == "deepseek"
    assert preset.default_model == "deepseek-v4-flash"
    assert preset.models == (
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-v4-flash-vision-exp",
    )


def test_moonshot_preset_defaults_to_kimi_k26():
    preset = match_byok_provider_preset("https://api.moonshot.cn/v1")
    assert preset is not None
    assert preset.id == "moonshot"
    assert preset.default_model == "kimi-k2.6"
    assert preset.models == ("kimi-k2.6", "kimi-k3", "kimi-k2.5")
    assert "kimi-k2" not in preset.models
    assert "moonshot-v1-8k" not in preset.models


def test_moonshot_alias_yields_same_models():
    models = preset_models_for_base_url("https://api.moonshot.ai/v1")
    assert models == ("kimi-k2.6", "kimi-k3", "kimi-k2.5")


def test_hy_tokenhub_preset_defaults_to_hy3():
    preset = match_byok_provider_preset("https://tokenhub.tencentmaas.com/v1")
    assert preset is not None
    assert preset.id == "hy"
    assert preset.label == "腾讯 Hy (TokenHub)"
    assert preset.default_model == "hy3"
    assert preset.models == ("hy3", "hy3-preview")


def test_hy_tokenhub_aliases_yield_same_models():
    urls = (
        "https://tokenhub.tencentmaas.cn/v1",
        "https://tokenhub-intl.tencentmaas.com/v1",
        "https://tokenhub-intl.tencentmaas.cn/v1",
        "https://tokenhub.tencentmaas.com/v1/",  # trailing slash normalize
    )
    for url in urls:
        models = preset_models_for_base_url(url)
        assert models == ("hy3", "hy3-preview"), url


def test_opencode_zen_preset_defaults_and_seed():
    preset = match_byok_provider_preset("https://opencode.ai/zen/v1")
    assert preset is not None
    assert preset.id == "opencode_zen"
    assert preset.label == "OpenCode Zen"
    assert preset.default_model == "deepseek-v4-flash"
    assert preset.models == ("deepseek-v4-flash", "kimi-k2.6", "glm-5.2")


def test_opencode_zen_trailing_slash_matches():
    preset = match_byok_provider_preset("https://opencode.ai/zen/v1/")
    assert preset is not None
    assert preset.id == "opencode_zen"
    assert preset_models_for_base_url("https://opencode.ai/zen/v1/") == (
        "deepseek-v4-flash",
        "kimi-k2.6",
        "glm-5.2",
    )


def test_opencode_go_preset_defaults_and_seed():
    preset = match_byok_provider_preset("https://opencode.ai/zen/go/v1")
    assert preset is not None
    assert preset.id == "opencode_go"
    assert preset.label == "OpenCode Go"
    assert preset.default_model == "deepseek-v4-flash"
    assert preset.models == ("deepseek-v4-flash", "deepseek-v4-pro", "glm-5.2")
    # /responses and /messages catalog ids stay off the chat/completions seed.
    assert "grok-4.5" not in preset.models
    assert "gpt-5.6-luna" not in preset.models
    assert "minimax-m2.7" not in preset.models
    assert "qwen3.7-max" not in preset.models
    assert "deepseek-v4-flash-free" not in preset.models


def test_opencode_go_trailing_slash_matches():
    preset = match_byok_provider_preset("https://opencode.ai/zen/go/v1/")
    assert preset is not None
    assert preset.id == "opencode_go"
    assert preset_models_for_base_url("https://opencode.ai/zen/go/v1/") == (
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "glm-5.2",
    )


def test_opencode_zen_and_go_urls_do_not_cross_match():
    zen = "https://opencode.ai/zen/v1"
    go = "https://opencode.ai/zen/go/v1"
    assert is_opencode_zen_base_url(zen) is True
    assert is_opencode_go_base_url(go) is True
    assert is_opencode_zen_base_url(go) is False
    assert is_opencode_go_base_url(zen) is False
    assert is_opencode_zen_base_url(go + "/") is False
    assert is_opencode_go_base_url(zen + "/") is False
    # Prefix/contains would lie; equality after normalize must not.
    assert go.startswith(zen) is False
    assert zen not in go
    assert match_byok_provider_preset("https://opencode.ai/zen") is None
    assert match_byok_provider_preset("https://opencode.ai/zen/v1/extra") is None


def test_byok_preset_base_urls_are_unique():
    seen: set[str] = set()
    for preset in BYOK_PROVIDER_PRESETS:
        urls = (preset.base_url, *preset.base_url_aliases)
        for url in urls:
            key = normalize_byok_base_url(url)
            assert key not in seen, key
            seen.add(key)


def test_off_protocol_ids_are_exact_known_catalog_ids():
    from agentcore.llm.catalog import off_protocol_kind as catalog_fn

    # Catalog (BYOK + platform) must read this mapping, not a twin list.
    assert catalog_fn is off_protocol_kind
    assert dict(BYOK_OFF_PROTOCOL_MODELS) == {
        "grok-4.5": "openai_responses",
        "gpt-5.6-luna": "openai_responses",
        "minimax-m2.7": "anthropic_messages",
        "qwen3.7-max": "anthropic_messages",
    }
    assert off_protocol_kind("grok-4.5") == "openai_responses"
    assert off_protocol_kind("minimax-m2.7") == "anthropic_messages"
    # No substring / regex guessing.
    assert off_protocol_kind("grok-4.5-fast") is None
    assert off_protocol_kind("x-ai/grok-4.5") is None
    assert off_protocol_kind("qwen-max") is None


def test_chat_completions_seed_is_the_opencode_exclusion_source():
    assert chat_completions_seed("deepseek-v4-flash", "grok-4.5", "glm-5.2") == (
        "deepseek-v4-flash",
        "glm-5.2",
    )
    assert chat_completions_seed("grok-4.5-fast", "x-ai/grok-4.5") == (
        "grok-4.5-fast",
        "x-ai/grok-4.5",
    )
    for preset in BYOK_PROVIDER_PRESETS:
        if preset.id in ("opencode_go", "opencode_zen"):
            assert preset.models == chat_completions_seed(*preset.models)
            assert set(preset.models).isdisjoint(BYOK_OFF_PROTOCOL_MODELS)
            assert is_opencode_byok_endpoint(preset.base_url) is True
    # Unknown / custom relays are not OpenCode; seed exclusion stays endpoint-gated.
    custom = "https://relay.example/openai/v1"
    assert match_byok_provider_preset(custom) is None
    assert is_opencode_byok_endpoint(custom) is False
    assert preset_models_for_base_url(custom) == ()
