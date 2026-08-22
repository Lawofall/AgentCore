"""Unit tests for model display enrichment (exact / family / derived)."""

from agentcore.llm.model_metadata import (
    _METADATA,
    CAPABILITY_REASONING,
    CAPABILITY_TOOLS,
    CAPABILITY_VISION,
    model_has_curated_vision,
    model_metadata_for,
)


def test_kimi_k26_exact_display_not_family_k2():
    """kimi-k2.6 must not inherit family-prefix「Kimi K2」."""
    meta = model_metadata_for("kimi-k2.6")
    assert meta.display_name == "Kimi K2.6"
    assert meta.vendor == "Moonshot"
    assert meta.capabilities == frozenset(
        {CAPABILITY_VISION, CAPABILITY_TOOLS, CAPABILITY_REASONING}
    )
    assert meta.context_length == 256_000


def test_kimi_k3_exact_display():
    meta = model_metadata_for("kimi-k3")
    assert meta.display_name == "Kimi K3"
    assert meta.vendor == "Moonshot"
    assert meta.capabilities == frozenset(
        {CAPABILITY_VISION, CAPABILITY_TOOLS, CAPABILITY_REASONING}
    )
    assert meta.context_length == 1_000_000


def test_kimi_k25_unchanged():
    meta = model_metadata_for("kimi-k2.5")
    assert meta.display_name == "Kimi K2.5"


def test_hy3_exact_display():
    meta = model_metadata_for("hy3")
    assert meta.display_name == "Hy3"
    assert meta.vendor == "腾讯 Hy"
    assert meta.capabilities == frozenset({CAPABILITY_TOOLS, CAPABILITY_REASONING})
    assert meta.context_length == 256_000


def test_hy3_preview_exact_display_not_family_hy3():
    """hy3-preview must not inherit family-prefix「Hy3」."""
    meta = model_metadata_for("hy3-preview")
    assert meta.display_name == "Hy3 Preview"
    assert meta.vendor == "腾讯 Hy"
    assert meta.capabilities == frozenset({CAPABILITY_TOOLS, CAPABILITY_REASONING})
    assert meta.context_length == 256_000


def test_family_variant_appends_qualifier_not_identical_label():
    """Dated / channel siblings must not share the family's bare display_name."""
    base = model_metadata_for("deepseek-v4-flash")
    assert base.display_name == "DeepSeek V4 Flash"
    assert base.badge is None

    dated = model_metadata_for("deepseek-v4-flash-0731")
    assert dated.display_name == "DeepSeek V4 Flash · 0731"
    assert dated.vendor == base.vendor
    assert dated.capabilities == base.capabilities
    assert dated.context_length == base.context_length
    assert dated.badge is None

    free = model_metadata_for("deepseek/deepseek-v4-flash-free")
    assert free.display_name == "DeepSeek V4 Flash"
    assert free.badge == "免费额度"
    assert free.vendor == "DeepSeek"
    assert free.capabilities == base.capabilities
    # Free SKU must NOT inherit Flash's native 1M — Zen caps this id at 200K.
    assert free.context_length == 200_000
    assert base.context_length == 1_000_000


def test_deepseek_v4_windows_follow_sku_not_family():
    """Native Flash/Pro = 1M; Zen free-tier id keeps the 200K gateway cap.

    Dated paid variants inherit 1M; a ``-free-…`` suffix must keep 200K (longest
    family key is ``deepseek-v4-flash-free``, not bare ``flash``).
    """
    assert model_metadata_for("deepseek-v4-flash").context_length == 1_000_000
    assert model_metadata_for("deepseek-v4-pro").context_length == 1_000_000
    assert model_metadata_for("deepseek-v4-flash-free").context_length == 200_000
    assert model_metadata_for("deepseek-v4-flash-0731").context_length == 1_000_000
    assert model_metadata_for("deepseek-v4-flash-free-0731").context_length == 200_000
    # Window is SKU-keyed: paying Flash on Go (no ``-free`` catalog) stays 1M.


def test_curated_display_name_badge_pairs_are_unique():
    """Curated uniqueness is (display_name, badge), not display_name alone."""
    pairs = [(meta.display_name, meta.badge) for meta in _METADATA.values()]
    assert len(pairs) == len(set(pairs))
    # Same brand base name is allowed when badge distinguishes the free SKU.
    assert model_metadata_for("deepseek-v4-flash").display_name == (
        model_metadata_for("deepseek-v4-flash-free").display_name
    )
    assert model_metadata_for("deepseek-v4-flash").badge is None
    assert model_metadata_for("deepseek-v4-flash-free").badge == "免费额度"


def test_family_variant_doubao_seed_and_o3_mini():
    """Other presets that previously collapsed to identical family labels."""
    seed = model_metadata_for("doubao/doubao-seed-2-1-turbo-260628")
    assert seed.display_name == "豆包 Seed · 2-1-turbo-260628"
    assert seed.vendor == "豆包 (火山方舟)"

    o3_mini = model_metadata_for("o3-mini")
    assert o3_mini.display_name == "OpenAI o3 · mini"
    assert CAPABILITY_REASONING in o3_mini.capabilities


def test_exact_curated_branding_beats_auto_qualifier():
    """Exact rows keep curated labels (not auto「· preview」)."""
    assert model_metadata_for("hy3-preview").display_name == "Hy3 Preview"
    assert model_metadata_for("gpt-4o-mini").display_name == "GPT-4o mini"
    # Alias catalog ids inherit the family display via · qualifier (no branded row).
    assert model_metadata_for("glm-5.2-alt").display_name == "GLM-5.2 · alt"


def test_family_prefix_requires_separator_boundary():
    """Bare startswith without a separator must not claim a longer sibling id."""
    # No curated ``gpt-4`` row today; still guard the helper contract via a
    # non-boundary case that would wrongly inherit if we used raw startswith.
    mystery = model_metadata_for("deepseek-v4-flashy")
    assert mystery.display_name != "DeepSeek V4 Flash"
    assert "flashy" in mystery.display_name.lower() or "Flashy" in mystery.display_name


def test_model_has_curated_vision_ignores_keyword_derive():
    """Native multimodal gate must not trust keyword-inferred vision tags."""
    assert model_has_curated_vision("gpt-4o") is True
    assert model_has_curated_vision("kimi-k2.5") is True
    assert model_has_curated_vision("deepseek-v4-pro") is False
    # Family-prefix dated variant still counts as curated for the gate.
    assert model_has_curated_vision("gpt-4o-custom-build") is True
    # Keyword-derived catalog may tag these, but curated gate stays closed.
    assert CAPABILITY_VISION in model_metadata_for("acme-vl-special").capabilities
    assert model_has_curated_vision("acme-vl-special") is False
    assert model_has_curated_vision("mystery-4o-clone") is False
