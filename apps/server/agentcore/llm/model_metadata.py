"""Lightweight display metadata that ENRICHES a model id — never an allow-list.

Discovery decides WHICH models a user can pick: BYOK proxies the user's own
``GET /models`` (llm/catalog.py); the platform set is the operator's configured
models. This module only maps a known id → nicer catalog fields (display name /
vendor / capability tags / context length). An unknown id still gets a best-effort
derived entry, so the catalog always returns something usable — the goal is
enhancement, not gatekeeping (never hardcode「可选模型清单」to replace discovery).

Capability tags are a subset of ``{"vision", "tools", "reasoning"}`` — the same
three flags the frontend renders. Context length is the gateway window in tokens
(catalog display **and** compaction near-top = 80% of this number). Prefer the
SKU the user actually hits, not the model's advertised native max: a ``-free``
row that a gateway caps at 200K must not inherit a sibling's 1M.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The three capability flags surfaced in the catalog (contract §1). Kept as a
# module constant so the schema layer and tests share one source of truth.
CAPABILITY_VISION = "vision"
CAPABILITY_TOOLS = "tools"
CAPABILITY_REASONING = "reasoning"
KNOWN_CAPABILITIES = frozenset({CAPABILITY_VISION, CAPABILITY_TOOLS, CAPABILITY_REASONING})


@dataclass(frozen=True)
class ModelMeta:
    """Display enrichment for one model id (all fields best-effort)."""

    display_name: str
    vendor: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    # Gateway window (tokens): catalog + compaction near-top. Not "native max".
    context_length: int | None = None
    # Curated display badge for clients to render as-is (e.g. 「免费额度」); never
    # inferred from id suffixes.
    badge: str | None = None


# Curated enrichment for ids AgentCore commonly sees (platform + popular BYOK
# endpoints). Keys are lowercase, provider-prefix-stripped; matching also does a
# longest-family prefix scan so a dated / channel variant (…-0731, …-free) inherits
# vendor / capabilities / context — but display_name always gets a · qualifier so
# undated/channel siblings stay distinguishable when no curated ``badge`` is set.
# Exact rows still win for curated branding (e.g. hy3-preview).
# Uniqueness is ``(display_name, badge)`` across curated rows — ``display_name``
# alone may repeat when a badge distinguishes the SKU (e.g. Flash +「免费额度」).
# Context length = the window this id actually gets (native vs gateway cap).
_METADATA: dict[str, ModelMeta] = {
    "deepseek-v4-flash": ModelMeta(
        display_name="DeepSeek V4 Flash",
        vendor="DeepSeek",
        capabilities=frozenset({CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=1_000_000,
    ),
    # Limited-free SKU (Zen catalog; Go has no ``-free`` id): brand base name +
    # curated badge (not auto「· free」). Exact row is load-bearing: family-prefix
    # would otherwise inherit Flash's 1M, but this id is capped at 200K. Window
    # follows SKU id, never the OpenCode endpoint.
    "deepseek-v4-flash-free": ModelMeta(
        display_name="DeepSeek V4 Flash",
        vendor="DeepSeek",
        capabilities=frozenset({CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=200_000,
        badge="免费额度",
    ),
    "deepseek-v4-pro": ModelMeta(
        display_name="DeepSeek V4 Pro",
        vendor="DeepSeek",
        capabilities=frozenset({CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=1_000_000,
    ),
    "gpt-4o": ModelMeta(
        display_name="GPT-4o",
        vendor="OpenAI",
        capabilities=frozenset({CAPABILITY_VISION, CAPABILITY_TOOLS}),
        context_length=128_000,
    ),
    "gpt-4o-mini": ModelMeta(
        display_name="GPT-4o mini",
        vendor="OpenAI",
        capabilities=frozenset({CAPABILITY_VISION, CAPABILITY_TOOLS}),
        context_length=128_000,
    ),
    "gpt-4.1": ModelMeta(
        display_name="GPT-4.1",
        vendor="OpenAI",
        capabilities=frozenset({CAPABILITY_VISION, CAPABILITY_TOOLS}),
        context_length=1_000_000,
    ),
    "o3": ModelMeta(
        display_name="OpenAI o3",
        vendor="OpenAI",
        capabilities=frozenset({CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=200_000,
    ),
    "qwen-vl-max": ModelMeta(
        display_name="Qwen-VL-Max",
        vendor="通义千问",
        capabilities=frozenset({CAPABILITY_VISION, CAPABILITY_TOOLS}),
        context_length=32_000,
    ),
    "qwen-max": ModelMeta(
        display_name="Qwen-Max",
        vendor="通义千问",
        capabilities=frozenset({CAPABILITY_TOOLS}),
        context_length=32_000,
    ),
    "doubao-seed": ModelMeta(
        display_name="豆包 Seed",
        vendor="豆包 (火山方舟)",
        capabilities=frozenset({CAPABILITY_TOOLS}),
        context_length=256_000,
    ),
    "kimi-k2": ModelMeta(
        display_name="Kimi K2",
        vendor="Moonshot",
        capabilities=frozenset({CAPABILITY_TOOLS}),
        context_length=128_000,
    ),
    # Platform vision default (VISION_MODEL); curated priced but keep off PLATFORM_MODELS.
    "kimi-k2.5": ModelMeta(
        display_name="Kimi K2.5",
        vendor="Moonshot",
        capabilities=frozenset({CAPABILITY_VISION, CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=256_000,
    ),
    # Exact entries so family-prefix does not collapse to「Kimi K2」.
    "kimi-k2.6": ModelMeta(
        display_name="Kimi K2.6",
        vendor="Moonshot",
        capabilities=frozenset({CAPABILITY_VISION, CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=256_000,
    ),
    "kimi-k3": ModelMeta(
        display_name="Kimi K3",
        vendor="Moonshot",
        capabilities=frozenset({CAPABILITY_VISION, CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=1_000_000,
    ),
    "moonshot-v1-128k": ModelMeta(
        display_name="Moonshot v1 128K",
        vendor="Moonshot",
        capabilities=frozenset({CAPABILITY_TOOLS}),
        context_length=128_000,
    ),
    "glm-4.6": ModelMeta(
        display_name="GLM-4.6",
        vendor="智谱 AI",
        capabilities=frozenset({CAPABILITY_TOOLS}),
        context_length=128_000,
    ),
    "glm-4v": ModelMeta(
        display_name="GLM-4V",
        vendor="智谱 AI",
        capabilities=frozenset({CAPABILITY_VISION, CAPABILITY_TOOLS}),
        context_length=8_000,
    ),
    # Platform relay default id (config/platform.py PLATFORM_MODEL). GLM-5.2 on
    # the operator's中转 upstream; curated as 智谱 AI for catalog display.
    "glm-5.2": ModelMeta(
        display_name="GLM-5.2",
        vendor="智谱 AI",
        capabilities=frozenset({CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=128_000,
    ),
    "grok-4.5": ModelMeta(
        display_name="Grok 4.5",
        vendor="xAI",
        capabilities=frozenset({CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=128_000,
    ),
    "hy3": ModelMeta(
        display_name="Hy3",
        vendor="腾讯 Hy",
        capabilities=frozenset({CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=256_000,
    ),
    # Exact entry so family-prefix does not collapse display to plain「Hy3」.
    "hy3-preview": ModelMeta(
        display_name="Hy3 Preview",
        vendor="腾讯 Hy",
        capabilities=frozenset({CAPABILITY_TOOLS, CAPABILITY_REASONING}),
        context_length=256_000,
    ),
}

# Vendor guesses by leading provider prefix / substring for unknown ids, so a
# derived entry still names a plausible vendor instead of "Unknown".
_VENDOR_HINTS: tuple[tuple[str, str], ...] = (
    ("deepseek", "DeepSeek"),
    ("gpt", "OpenAI"),
    ("o1", "OpenAI"),
    ("o3", "OpenAI"),
    ("o4", "OpenAI"),
    ("claude", "Anthropic"),
    ("gemini", "Google"),
    ("qwen", "通义千问"),
    ("doubao", "豆包 (火山方舟)"),
    ("kimi", "Moonshot"),
    ("moonshot", "Moonshot"),
    ("glm", "智谱 AI"),
    ("yi-", "零一万物"),
    ("mistral", "Mistral"),
    ("llama", "Meta"),
    ("grok", "xAI"),
)


def _normalize(model_id: str) -> str:
    """Lowercase and drop a single leading ``provider/`` route prefix."""
    key = (model_id or "").strip().lower()
    if "/" in key:
        _prefix, _, rest = key.partition("/")
        if rest:
            key = rest
    return key


def _derive_capabilities(key: str) -> frozenset[str]:
    """Best-effort capability tags from id keywords (conservative — omit if unsure)."""
    caps: set[str] = set()
    if any(tok in key for tok in ("-vl", "vision", "-v-", "4o", "4v", "gemini", "omni")):
        caps.add(CAPABILITY_VISION)
    if any(tok in key for tok in ("reason", "think", "-r1", "o1", "o3", "o4", "-r-")):
        caps.add(CAPABILITY_REASONING)
    return frozenset(caps)


def _derive_vendor(key: str) -> str:
    for token, vendor in _VENDOR_HINTS:
        if token in key:
            return vendor
    return "其他"


def _humanize(model_id: str) -> str:
    """A readable display name for an unknown id (keep the raw id, tidy separators)."""
    raw = (model_id or "").strip()
    if not raw:
        return "未知模型"
    tail = raw.rsplit("/", 1)[-1]
    return tail.replace("_", " ").replace("-", " ").strip() or tail


# Separators that mark a family→variant boundary (``flash-0731``, ``k2.6``, ``o3_mini``).
_FAMILY_BOUNDARY = frozenset({"-", "_", "."})


def _longest_family_key(key: str) -> str | None:
    """Longest curated key that ``key`` extends past a separator boundary.

    Requires a boundary char after the family id so ``gpt-4`` cannot claim ``gpt-4o``
    as a "variant". Exact id matches are handled by the caller before this.
    """
    best_key: str | None = None
    for known in _METADATA:
        if len(known) >= len(key) or not key.startswith(known):
            continue
        if key[len(known)] not in _FAMILY_BOUNDARY:
            continue
        if best_key is None or len(known) > len(best_key):
            best_key = known
    return best_key


def _family_variant_meta(family: ModelMeta, key: str, family_key: str) -> ModelMeta:
    """Inherit family enrichment; distinguish display with the leftover qualifier.

    Auto variants do **not** inherit a curated ``badge`` — only exact rows carry
    badges. The · qualifier keeps labels unique when badge is absent.
    """
    qualifier = key[len(family_key) :].lstrip("-_.")
    if not qualifier:
        return family
    return ModelMeta(
        display_name=f"{family.display_name} · {qualifier}",
        vendor=family.vendor,
        capabilities=family.capabilities,
        context_length=family.context_length,
        badge=None,
    )


def model_metadata_for(model_id: str) -> ModelMeta:
    """Enrichment for ``model_id`` — exact, then family-prefix, then derived.

    Never returns ``None``: an unknown id yields a derived entry (humanized name,
    vendor guess, keyword-inferred capabilities) so the catalog stays complete.
    Family-prefix hits keep vendor / caps / context but append a · qualifier to
    ``display_name`` so dated / channel siblings stay distinguishable in pickers.
    """
    key = _normalize(model_id)
    if not key:
        return ModelMeta(display_name=_humanize(model_id), vendor="其他")
    exact = _METADATA.get(key)
    if exact is not None:
        return exact
    best_key = _longest_family_key(key)
    if best_key is not None:
        return _family_variant_meta(_METADATA[best_key], key, best_key)
    return ModelMeta(
        display_name=_humanize(model_id),
        vendor=_derive_vendor(key),
        capabilities=_derive_capabilities(key),
    )


def model_has_curated_vision(model_id: str) -> bool:
    """True only when ``vision`` comes from the curated ``_METADATA`` table.

    Keyword-derived capabilities (``_derive_capabilities``) enrich the catalog UI
    but must **not** open native multimodal routing — a false ``vision`` tag on a
    text-only upstream (e.g. id containing ``vl`` / ``4o`` substrings) would 400
    the turn. Exact id and longest family-prefix hits count as curated.
    """
    key = _normalize(model_id)
    if not key:
        return False
    exact = _METADATA.get(key)
    if exact is not None:
        return CAPABILITY_VISION in exact.capabilities
    best_key = _longest_family_key(key)
    if best_key is None:
        return False
    return CAPABILITY_VISION in _METADATA[best_key].capabilities
