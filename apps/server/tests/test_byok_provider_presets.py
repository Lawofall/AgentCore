"""Unit tests for BYOK vendor presets (Moonshot / Kimi catalog seed)."""

from agentcore.llm.byok_provider_presets import (
    BYOK_OFF_PROTOCOL_MODELS,
    BYOK_PROVIDER_PRESETS,
    canonicalize_byok_api_root,
    chat_completions_seed,
    hide_from_picker_ids_for_vendor,
    is_openai_chat_model_id,
    is_opencode_byok_endpoint,
    is_opencode_go_base_url,
    is_opencode_zen_base_url,
    match_byok_provider_preset,
    normalize_byok_base_url,
    off_protocol_kind,
    parse_vendor_id,
    preset_models_for_vendor,
    seed_model_for_vendor,
    uses_anthropic_messages_wire,
    uses_openai_responses_wire,
)


def test_deepseek_preset_is_v41_flash_only():
    preset = match_byok_provider_preset("https://api.deepseek.com")
    assert preset is not None
    assert preset.id == "deepseek"
    assert preset.default_model == "deepseek-flash"
    assert preset.models == ("deepseek-flash",)
    assert "deepseek-v4-flash" in preset.hide_from_picker
    assert "deepseek-v4-pro" in preset.hide_from_picker
    assert "deepseek-v4-flash" in hide_from_picker_ids_for_vendor("deepseek")
    assert hide_from_picker_ids_for_vendor("opencode_go") == frozenset(
        {
            "deepseek-flash",
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek-v4-flash-vision-exp",
        }
    )
    assert hide_from_picker_ids_for_vendor("opencode_zen") == frozenset(
        {
            "deepseek-flash",
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek-v4-flash-vision-exp",
        }
    )
    assert hide_from_picker_ids_for_vendor("custom") == frozenset()


def test_moonshot_preset_defaults_to_kimi_k26():
    preset = match_byok_provider_preset("https://api.moonshot.cn/v1")
    assert preset is not None
    assert preset.id == "moonshot"
    assert preset.default_model == "kimi-k2.6"
    assert preset.models == ("kimi-k2.6", "kimi-k3", "kimi-k2.5")
    assert "kimi-k2" not in preset.models
    assert "moonshot-v1-8k" not in preset.models


def test_moonshot_alias_yields_same_models():
    alias = match_byok_provider_preset("https://api.moonshot.ai/v1")
    assert alias is not None
    assert alias.id == "moonshot"
    assert preset_models_for_vendor("moonshot") == ("kimi-k2.6", "kimi-k3", "kimi-k2.5")


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
        matched = match_byok_provider_preset(url)
        assert matched is not None and matched.id == "hy", url
    assert preset_models_for_vendor("hy") == ("hy3", "hy3-preview")


def test_opencode_zen_preset_defaults_and_seed():
    preset = match_byok_provider_preset("https://opencode.ai/zen/v1")
    assert preset is not None
    assert preset.id == "opencode_zen"
    assert preset.label == "OpenCode Zen"
    assert preset.default_model == "deepseek-v4.1-flash"
    assert preset.models == ("deepseek-v4.1-flash", "kimi-k2.6", "glm-5.2")


def test_opencode_zen_trailing_slash_matches():
    preset = match_byok_provider_preset("https://opencode.ai/zen/v1/")
    assert preset is not None
    assert preset.id == "opencode_zen"
    assert preset_models_for_vendor("opencode_zen") == (
        "deepseek-v4.1-flash",
        "kimi-k2.6",
        "glm-5.2",
    )


def test_opencode_go_preset_defaults_and_seed():
    preset = match_byok_provider_preset("https://opencode.ai/zen/go/v1")
    assert preset is not None
    assert preset.id == "opencode_go"
    assert preset.label == "OpenCode Go"
    assert preset.default_model == "deepseek-v4.1-flash"
    assert preset.models == (
        "deepseek-v4.1-flash",
        "glm-5.3",
        "glm-5.3-flash",
        "kimi-k3",
    )
    # /responses and /messages catalog ids stay off the chat/completions seed.
    assert "grok-4.5" not in preset.models
    assert "gpt-5.6-luna" not in preset.models
    assert "qwen3.7-max" not in preset.models
    assert "union-alpha" not in preset.models
    assert "deepseek-v4-flash-free" not in preset.models


def test_opencode_go_trailing_slash_matches():
    preset = match_byok_provider_preset("https://opencode.ai/zen/go/v1/")
    assert preset is not None
    assert preset.id == "opencode_go"
    assert preset_models_for_vendor("opencode_go") == (
        "deepseek-v4.1-flash",
        "glm-5.3",
        "glm-5.3-flash",
        "kimi-k3",
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


def test_off_protocol_kind_family_and_exact_overrides():
    from agentcore.llm.catalog import off_protocol_kind as catalog_fn

    # Catalog (BYOK + platform) must read this function, not a twin list.
    assert catalog_fn is off_protocol_kind
    # Exact override (not a family prefix).
    assert BYOK_OFF_PROTOCOL_MODELS == {"union-alpha": "anthropic_messages"}
    assert off_protocol_kind("union-alpha") == "anthropic_messages"
    # Families: new SKUs do not need a table row.
    assert off_protocol_kind("claude-haiku-4-5") == "anthropic_messages"
    assert off_protocol_kind("claude-haiku-5-5") == "anthropic_messages"
    assert off_protocol_kind("claude-haiku-9") == "anthropic_messages"
    assert off_protocol_kind("qwen3.7-max") == "anthropic_messages"
    assert off_protocol_kind("qwen3.8-plus") == "anthropic_messages"
    assert off_protocol_kind("grok-4.5") == "openai_responses"
    assert off_protocol_kind("grok-4.5-fast") == "openai_responses"
    assert off_protocol_kind("gpt-5.6-luna") == "openai_responses"
    assert off_protocol_kind("muse-spark-1.3") == "openai_responses"
    # Zen now serves MiniMax M2.7 on chat/completions.
    assert "minimax-m2.7" not in BYOK_OFF_PROTOCOL_MODELS
    assert off_protocol_kind("minimax-m2.7") is None
    # Bare-id families only — provider slugs and other names stay chat/completions.
    assert off_protocol_kind("x-ai/grok-4.5") is None
    assert off_protocol_kind("anthropic/claude-sonnet-4") is None
    assert off_protocol_kind("qwen-max") is None
    assert off_protocol_kind("deepseek-v4.1-flash") is None
    assert set(BYOK_OFF_PROTOCOL_MODELS.values()) <= {"openai_responses", "anthropic_messages"}


def test_uses_anthropic_messages_wire_only_on_opencode_vendors():
    assert uses_anthropic_messages_wire("claude-haiku-4-5", "opencode_zen") is True
    assert uses_anthropic_messages_wire("claude-haiku-5-5", "opencode_go") is True
    assert uses_anthropic_messages_wire("claude-haiku-9", "opencode_go") is True
    assert uses_anthropic_messages_wire("union-alpha", "opencode_go") is True
    assert uses_anthropic_messages_wire("qwen3.7-max", "opencode_zen") is True
    # Same id on a non-OpenCode vendor stays chat/completions (OpenRouter slugs differ).
    assert uses_anthropic_messages_wire("claude-haiku-4-5", "openrouter") is False
    assert uses_anthropic_messages_wire("claude-haiku-4-5", "custom") is False
    assert uses_anthropic_messages_wire("anthropic/claude-sonnet-4", "opencode_zen") is False
    # /responses ids never take the /messages leaf.
    assert uses_anthropic_messages_wire("grok-4.5", "opencode_zen") is False
    assert uses_anthropic_messages_wire("deepseek-v4.1-flash", "opencode_zen") is False


def test_uses_openai_responses_wire_follows_vendor_identity():
    assert uses_openai_responses_wire("gpt-4o", "openai") is True
    assert uses_openai_responses_wire("o3-mini", "openai") is True
    assert uses_openai_responses_wire("gpt-6-astra", "openai") is True
    # OpenAI vendor: every chat turn, including a relay-hosted alias.
    assert uses_openai_responses_wire("gpt-6.1-sol", "openai") is True
    assert uses_openai_responses_wire("grok-4.5", "opencode_zen") is True
    assert uses_openai_responses_wire("gpt-5.6-luna", "opencode_zen") is True
    assert uses_openai_responses_wire("muse-spark-1.3", "opencode_zen") is True
    assert uses_openai_responses_wire("deepseek-v4.1-flash", "opencode_zen") is False
    assert uses_openai_responses_wire("claude-haiku-4-5", "opencode_zen") is False
    assert uses_openai_responses_wire("grok-4.5", "openrouter") is False
    assert uses_openai_responses_wire("gpt-4o", "openrouter") is False
    assert uses_openai_responses_wire("gpt-4o", "custom") is False


def test_is_openai_chat_model_id_drops_non_chat():
    assert is_openai_chat_model_id("gpt-4o") is True
    assert is_openai_chat_model_id("gpt-5") is True
    assert is_openai_chat_model_id("o3-mini") is True
    assert is_openai_chat_model_id("whisper-1") is False
    assert is_openai_chat_model_id("tts-1") is False
    assert is_openai_chat_model_id("dall-e-3") is False
    assert is_openai_chat_model_id("text-embedding-3-small") is False
    assert is_openai_chat_model_id("gpt-4o-mini-tts") is False
    assert is_openai_chat_model_id("gpt-image-1") is False


def test_chat_completions_seed_is_the_opencode_exclusion_source():
    assert chat_completions_seed("deepseek-v4.1-flash", "grok-4.5", "glm-5.2") == (
        "deepseek-v4.1-flash",
        "glm-5.2",
    )
    assert chat_completions_seed("grok-4.5-fast", "x-ai/grok-4.5") == (
        "x-ai/grok-4.5",
    )
    for preset in BYOK_PROVIDER_PRESETS:
        if preset.id in ("opencode_go", "opencode_zen"):
            assert preset.models == chat_completions_seed(*preset.models)
            assert all(off_protocol_kind(mid) is None for mid in preset.models)
            assert is_opencode_byok_endpoint(preset.base_url) is True
    # Unknown / custom relays are not OpenCode; seed exclusion stays endpoint-gated.
    custom = "https://relay.example/openai/v1"
    assert match_byok_provider_preset(custom) is None
    assert is_opencode_byok_endpoint(custom) is False
    assert preset_models_for_vendor("custom") == ()


def test_preset_table_loads_from_json_next_to_module():
    import json
    from pathlib import Path

    from agentcore.llm import byok_provider_presets as mod

    path = Path(mod.__file__).with_name("byok_provider_presets.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert [row["id"] for row in raw["presets"]] == [p.id for p in BYOK_PROVIDER_PRESETS]
    assert raw["offProtocolModels"] == dict(BYOK_OFF_PROTOCOL_MODELS)
    # UI-only; server catalog does not need it.
    assert all("keyHelpUrl" in row for row in raw["presets"])


def test_seed_model_for_vendor_matches_preset_or_empty():
    assert seed_model_for_vendor("deepseek") == "deepseek-flash"
    assert seed_model_for_vendor("openai") == "gpt-4o"
    assert seed_model_for_vendor("custom") == ""
    assert seed_model_for_vendor("nope") == ""


def test_canonicalize_byok_api_root_appends_v1_to_origin_only():
    assert canonicalize_byok_api_root("https://omyai.vip") == "https://omyai.vip/v1"
    assert canonicalize_byok_api_root("https://omyai.vip/") == "https://omyai.vip/v1"
    assert (
        canonicalize_byok_api_root("https://api.apistokens.com")
        == "https://api.apistokens.com/v1"
    )


def test_canonicalize_byok_api_root_leaves_paths_and_presets():
    assert (
        canonicalize_byok_api_root("https://omyai.vip/v1") == "https://omyai.vip/v1"
    )
    assert (
        canonicalize_byok_api_root("https://relay.example/openai/v1")
        == "https://relay.example/openai/v1"
    )
    assert (
        canonicalize_byok_api_root("https://ark.cn-beijing.volces.com/api/v3")
        == "https://ark.cn-beijing.volces.com/api/v3"
    )
    assert (
        canonicalize_byok_api_root("https://api.deepseek.com")
        == "https://api.deepseek.com"
    )
    assert (
        canonicalize_byok_api_root("https://api.deepseek.com/v1")
        == "https://api.deepseek.com/v1"
    )
    assert canonicalize_byok_api_root("") == ""


def test_parse_vendor_id_accepts_presets_and_custom():
    assert parse_vendor_id("openai") == "openai"
    assert parse_vendor_id("opencode_go") == "opencode_go"
    assert parse_vendor_id("custom") == "custom"
    assert parse_vendor_id("https://api.openai.com/v1") is None
    assert parse_vendor_id("") is None
    assert parse_vendor_id(None) is None
