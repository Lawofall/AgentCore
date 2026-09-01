"""BYOK vendor presets — server-side catalog seed aligned with desktop.

Mirrors ``apps/desktop/src/renderer/lib/byokProviderPresets.ts`` (baseUrl / aliases /
models / defaultModel). Catalog merge matches providers by normalized ``base_url``;
unknown endpoints get no preset rows.

Off-protocol model ids (need ``/responses`` or ``/messages``; this gateway only
speaks ``chat/completions``) live in :data:`BYOK_OFF_PROTOCOL_MODELS` — the single
exact-id map for OpenCode seed exclusion, BYOK catalog unavailability, **and**
platform-allowlist catalog unavailability. Name kept (historical); both origins
call :func:`off_protocol_kind`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

OffProtocolKind = Literal["openai_responses", "anthropic_messages"]

# Exact ids only — never substring / regex. Shared by BYOK (OpenCode Go/Zen
# discovery) and platform (operator allowlist). OpenCode ``GET /models`` still
# returns these; they stay out of chat/completions seeds and are listed-but-
# unselectable in the catalog merge (not dropped at discovery / allowlist).
BYOK_OFF_PROTOCOL_MODELS: Mapping[str, OffProtocolKind] = MappingProxyType(
    {
        "grok-4.5": "openai_responses",
        "gpt-5.6-luna": "openai_responses",
        "minimax-m2.7": "anthropic_messages",
        "qwen3.7-max": "anthropic_messages",
    }
)


def off_protocol_kind(model_id: str) -> OffProtocolKind | None:
    """Required upstream protocol if ``model_id`` is a known off-protocol id.

    Origin-agnostic lookup (BYOK OpenCode rows and platform allowlist rows).
    """
    return BYOK_OFF_PROTOCOL_MODELS.get((model_id or "").strip())


def chat_completions_seed(*model_ids: str) -> tuple[str, ...]:
    """Drop known off-protocol ids from a seed. OpenCode Go/Zen seeds use this."""
    return tuple(mid for mid in model_ids if mid not in BYOK_OFF_PROTOCOL_MODELS)


@dataclass(frozen=True)
class ByokProviderPreset:
    id: str
    label: str
    base_url: str
    default_model: str
    models: tuple[str, ...]
    base_url_aliases: tuple[str, ...] = ()


BYOK_PROVIDER_PRESETS: tuple[ByokProviderPreset, ...] = (
    ByokProviderPreset(
        id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        base_url_aliases=("https://api.deepseek.com/v1",),
        default_model="deepseek-v4-flash",
        models=("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"),
    ),
    ByokProviderPreset(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        models=("gpt-4o", "gpt-4o-mini", "o3-mini"),
    ),
    ByokProviderPreset(
        id="moonshot",
        label="Kimi (Moonshot)",
        base_url="https://api.moonshot.cn/v1",
        base_url_aliases=("https://api.moonshot.ai/v1",),
        default_model="kimi-k2.6",
        # kimi-k2 / moonshot-v1-* retired; k2.5 kept for older keys.
        models=("kimi-k2.6", "kimi-k3", "kimi-k2.5"),
    ),
    ByokProviderPreset(
        id="zhipu",
        label="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-plus",
        models=("glm-4-plus", "glm-4-flash", "glm-4-air"),
    ),
    ByokProviderPreset(
        id="doubao",
        label="豆包 (火山方舟)",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="doubao-seed-2-1-turbo-260628",
        # Short seed; doubao-pro/lite-32k retired — use dated seed IDs or ep-… endpoints.
        models=("doubao-seed-2-1-turbo-260628",),
    ),
    ByokProviderPreset(
        id="hy",
        label="腾讯 Hy (TokenHub)",
        base_url="https://tokenhub.tencentmaas.com/v1",
        base_url_aliases=(
            "https://tokenhub.tencentmaas.cn/v1",
            "https://tokenhub-intl.tencentmaas.com/v1",
            "https://tokenhub-intl.tencentmaas.cn/v1",
        ),
        default_model="hy3",
        models=("hy3", "hy3-preview"),
    ),
    ByokProviderPreset(
        id="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="openrouter/auto",
        models=(
            "openrouter/auto",
            "anthropic/claude-sonnet-4",
            "google/gemini-2.5-pro",
        ),
    ),
    ByokProviderPreset(
        id="opencode_zen",
        label="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1",
        default_model="deepseek-v4-flash",
        # Short seed for discovery-miss; full catalog = GET /models union.
        # Off-protocol ids: :data:`BYOK_OFF_PROTOCOL_MODELS` (not this tuple).
        models=chat_completions_seed("deepseek-v4-flash", "kimi-k2.6", "glm-5.2"),
    ),
    ByokProviderPreset(
        id="opencode_go",
        label="OpenCode Go",
        # Sibling of Zen — exact match only. ``…/zen/go/v1`` must never be
        # swallowed by a prefix/contains check on ``…/zen/v1``.
        base_url="https://opencode.ai/zen/go/v1",
        default_model="deepseek-v4-flash",
        # chat/completions seed only; /responses and /messages ids stay off-seed
        # via :func:`chat_completions_seed` / :data:`BYOK_OFF_PROTOCOL_MODELS`.
        models=chat_completions_seed(
            "deepseek-v4-flash", "deepseek-v4-pro", "glm-5.2"
        ),
    ),
)


def normalize_byok_base_url(url: str) -> str:
    """Normalize base_url for preset matching (case, trailing slashes)."""
    normalized = url.strip().lower()
    while normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def _preset_base_urls(preset: ByokProviderPreset) -> tuple[str, ...]:
    return (preset.base_url, *preset.base_url_aliases)


def match_byok_provider_preset(base_url: str) -> ByokProviderPreset | None:
    """Return the preset whose canonical / alias base_url matches, else None.

    Equality after normalize only — never ``in`` / ``startswith``. OpenCode Go
    (``…/zen/go/v1``) and Zen (``…/zen/v1``) would cross-hit under prefix checks.
    """
    normalized = normalize_byok_base_url(base_url)
    if not normalized:
        return None
    for preset in BYOK_PROVIDER_PRESETS:
        if any(
            normalize_byok_base_url(candidate) == normalized
            for candidate in _preset_base_urls(preset)
        ):
            return preset
    return None


def is_opencode_go_base_url(base_url: str) -> bool:
    """True only for the OpenCode Go canonical endpoint (exact preset match)."""
    preset = match_byok_provider_preset(base_url)
    return preset is not None and preset.id == "opencode_go"


def is_opencode_zen_base_url(base_url: str) -> bool:
    """True only for the OpenCode Zen canonical endpoint (exact preset match)."""
    preset = match_byok_provider_preset(base_url)
    return preset is not None and preset.id == "opencode_zen"


def is_opencode_byok_endpoint(base_url: str) -> bool:
    """True for OpenCode Zen or Go canonical endpoints (exact preset match)."""
    preset = match_byok_provider_preset(base_url)
    return preset is not None and preset.id in ("opencode_go", "opencode_zen")


def preset_models_for_base_url(base_url: str) -> tuple[str, ...]:
    """Model ids from the matching vendor preset, or empty when unknown/custom.

    OpenCode Go/Zen seeds are filtered through :func:`chat_completions_seed` so a
    leaked off-protocol id in the tuple cannot re-enter the catalog via presets.
    """
    preset = match_byok_provider_preset(base_url)
    if preset is None:
        return ()
    if is_opencode_byok_endpoint(base_url):
        return chat_completions_seed(*preset.models)
    return preset.models
