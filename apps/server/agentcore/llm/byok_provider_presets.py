"""BYOK vendor presets — catalog seed + off-protocol kind.

Table source: ``byok_provider_presets.json`` next to this module (desktop form
loads the same file). Catalog merge matches providers by saved ``vendor_id``;
``custom`` vendors get discovery only. URL matching is only for filling a
preset's default endpoint and OpenCode session headers.

:func:`off_protocol_kind` is the single lookup for OpenCode seed exclusion,
platform-allowlist greying, and protocol family. Family rules cover OpenCode
SKU churn (``claude-*`` → ``/messages``; ``gpt-`` / ``grok-`` / ``muse-*`` →
``/responses``; Qwen 3.x plus/max → ``/messages``).
:data:`BYOK_OFF_PROTOCOL_MODELS` is exact overrides only (ids that are not a
family, e.g. ``union-alpha``). Bare ids only — a ``provider/`` slug never
matches a family (OpenRouter ``anthropic/…`` stays chat/completions). Dispatch
uses saved vendor identity, not ``base_url``: OpenAI vendor always
``/responses``; OpenCode lists ``/messages`` and ``/responses`` as selectable.
Platform greys unpriced off-protocol ids; priced allowlist rows stay
Chat Completions selectable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast
from urllib.parse import urlparse

OffProtocolKind = Literal["openai_responses", "anthropic_messages"]

_DATA_PATH = Path(__file__).resolve().with_name("byok_provider_presets.json")
_ALLOWED_OFF_PROTOCOL: frozenset[str] = frozenset(
    {"openai_responses", "anthropic_messages"}
)
_OPENCODE_PRESET_IDS = frozenset({"opencode_go", "opencode_zen"})


def _load_raw() -> dict[str, Any]:
    if not _DATA_PATH.is_file():
        raise FileNotFoundError(
            f"BYOK preset table missing: {_DATA_PATH}. "
            "The JSON must ship next to this module (hatch force-include)."
        )
    payload = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("byok_provider_presets.json: root must be an object")
    return payload


def _off_protocol_from_raw(raw: Mapping[str, Any]) -> dict[str, OffProtocolKind]:
    models = raw.get("offProtocolModels")
    if not isinstance(models, dict):
        raise ValueError("byok_provider_presets.json: offProtocolModels must be an object")
    out: dict[str, OffProtocolKind] = {}
    for mid, kind in models.items():
        if not isinstance(mid, str) or kind not in _ALLOWED_OFF_PROTOCOL:
            raise ValueError(
                f"byok_provider_presets.json: bad off-protocol entry {mid!r}={kind!r}"
            )
        out[mid] = cast(OffProtocolKind, kind)
    return out


_RAW = _load_raw()

# Exact overrides only. Family rules in :func:`off_protocol_kind` are the
# default; a row here wins when the id is not a family (or must disagree).
BYOK_OFF_PROTOCOL_MODELS: Mapping[str, OffProtocolKind] = MappingProxyType(
    _off_protocol_from_raw(_RAW)
)

_FAMILY_RESPONSES_PREFIXES = ("gpt-", "grok-", "muse-")


def _family_off_protocol(model_id: str) -> OffProtocolKind | None:
    """Bare-id family → protocol. Slugs with ``/`` never match."""
    key = (model_id or "").strip().lower()
    if not key or "/" in key:
        return None
    if key.startswith("claude-"):
        return "anthropic_messages"
    if key.startswith(_FAMILY_RESPONSES_PREFIXES):
        return "openai_responses"
    if key.startswith("qwen3.") and key.endswith(("-plus", "-max")):
        return "anthropic_messages"
    return None


def off_protocol_kind(model_id: str) -> OffProtocolKind | None:
    """Required upstream protocol for ``model_id``, or None for chat/completions.

    Exact JSON overrides win; otherwise a bare-id family. Origin-agnostic
    (BYOK OpenCode rows and platform allowlist). Dispatch still gates
    ``/messages`` on the OpenCode endpoint.
    """
    mid = (model_id or "").strip()
    if not mid:
        return None
    exact = BYOK_OFF_PROTOCOL_MODELS.get(mid)
    if exact is not None:
        return exact
    return _family_off_protocol(mid)


BYOK_CUSTOM_VENDOR_ID = "custom"


def is_opencode_vendor(vendor_id: str) -> bool:
    """True when this 服务商 was saved as OpenCode Zen or Go."""
    return (vendor_id or "").strip() in _OPENCODE_PRESET_IDS


def uses_anthropic_messages_wire(model_id: str, vendor_id: str) -> bool:
    """True when this OpenCode leaf must POST ``/messages`` for ``model_id``.

    Protocol follows vendor identity, not the stored URL. OpenCode BYOK lists
    the same ids as selectable. Dispatch uses this so a Zen/Go key can serve
    Flash via ``/chat/completions`` and Claude via ``/messages``. OpenRouter
    and custom vendors stay on chat completions even if the id coincides.
    """
    if off_protocol_kind(model_id) != "anthropic_messages":
        return False
    return is_opencode_vendor(vendor_id)


def uses_openai_responses_wire(model_id: str, vendor_id: str) -> bool:
    """True when this leaf must POST ``/responses`` for ``model_id``.

    Protocol follows vendor identity (LiteLLM ``openai/`` + ``api_base``), not
    whether ``base_url`` equals ``api.openai.com``. OpenAI vendor: every chat
    turn. OpenCode Zen/Go: the ``gpt-`` / ``grok-`` / ``muse-*`` family.
    OpenRouter and custom vendors stay on chat completions.
    """
    vid = (vendor_id or "").strip()
    if vid == "openai":
        return True
    if off_protocol_kind(model_id) != "openai_responses":
        return False
    return is_opencode_vendor(vid)


_OPENAI_NON_CHAT_MARKERS = (
    "-transcribe",
    "-tts",
    "-audio",
    "-realtime",
    "-search-preview",
    "gpt-image",
    "chatgpt-image",
)


def is_openai_chat_model_id(model_id: str) -> bool:
    """True for official OpenAI chat/reasoning ids (drop whisper / TTS / image)."""
    key = (model_id or "").strip().lower()
    if not key or "/" in key:
        return False
    if key.startswith(
        (
            "whisper",
            "tts-",
            "dall-e",
            "text-embedding",
            "davinci",
            "babbage",
            "omni-moderation",
            "sora",
        )
    ):
        return False
    if any(marker in key for marker in _OPENAI_NON_CHAT_MARKERS):
        return False
    if key.startswith(("gpt-", "chatgpt-")):
        return True
    return key in {"o1", "o3", "o4"} or key.startswith(("o1-", "o3-", "o4-"))


def chat_completions_seed(*model_ids: str) -> tuple[str, ...]:
    """Drop off-protocol ids from a seed. OpenCode Go/Zen seeds use this."""
    return tuple(mid for mid in model_ids if off_protocol_kind(mid) is None)


@dataclass(frozen=True)
class ByokProviderPreset:
    id: str
    label: str
    base_url: str
    default_model: str
    models: tuple[str, ...]
    base_url_aliases: tuple[str, ...] = ()
    # Exact wire ids omitted from the chat picker after seed ∪ discovery.
    hide_from_picker: tuple[str, ...] = ()


def _str_field(row: Mapping[str, Any], key: str) -> str:
    val = row.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"byok_provider_presets.json: preset missing {key}")
    return val


def _str_tuple(row: Mapping[str, Any], key: str) -> tuple[str, ...]:
    val = row.get(key)
    if val is None:
        return ()
    if not isinstance(val, list) or any(not isinstance(item, str) for item in val):
        raise ValueError(f"byok_provider_presets.json: preset {key} must be a string array")
    return tuple(val)


def _presets_from_raw(raw: Mapping[str, Any]) -> tuple[ByokProviderPreset, ...]:
    rows = raw.get("presets")
    if not isinstance(rows, list) or not rows:
        raise ValueError("byok_provider_presets.json: presets must be a non-empty array")
    out: list[ByokProviderPreset] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("byok_provider_presets.json: each preset must be an object")
        preset_id = _str_field(row, "id")
        models = _str_tuple(row, "models")
        if preset_id in _OPENCODE_PRESET_IDS:
            models = chat_completions_seed(*models)
        out.append(
            ByokProviderPreset(
                id=preset_id,
                label=_str_field(row, "label"),
                base_url=_str_field(row, "baseUrl"),
                default_model=_str_field(row, "defaultModel"),
                models=models,
                base_url_aliases=_str_tuple(row, "baseUrlAliases"),
                hide_from_picker=_str_tuple(row, "hideFromPicker"),
            )
        )
    return tuple(out)


BYOK_PROVIDER_PRESETS: tuple[ByokProviderPreset, ...] = _presets_from_raw(_RAW)

_PRESET_BY_ID: dict[str, ByokProviderPreset] = {
    preset.id: preset for preset in BYOK_PROVIDER_PRESETS
}
BYOK_VENDOR_IDS: frozenset[str] = frozenset(_PRESET_BY_ID) | {BYOK_CUSTOM_VENDOR_ID}


def parse_vendor_id(raw: str | None) -> str | None:
    """Canonical vendor id, or None when missing / unknown."""
    vid = (raw or "").strip()
    if vid in BYOK_VENDOR_IDS:
        return vid
    return None


def preset_for_vendor(vendor_id: str) -> ByokProviderPreset | None:
    """Preset row for a saved vendor id. ``custom`` and unknowns are None."""
    return _PRESET_BY_ID.get((vendor_id or "").strip())


def normalize_byok_base_url(url: str) -> str:
    """Normalize base_url for preset matching (case, trailing slashes)."""
    normalized = url.strip().lower()
    while normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def canonicalize_byok_api_root(url: str) -> str:
    """Turn a console origin into the OpenAI-compat API root.

    Relays often copy ``https://host`` (the website). Outbound calls are
    ``{root}/models`` and ``{root}/chat/completions``, so an empty path is
    stored and used as ``{origin}/v1``. Preset official endpoints (DeepSeek
    origin, Ark ``/api/v3``, …) stay as-is. A path that is already present
    is not rewritten. Trailing slashes are stripped. This is the write /
    outbound rule — not a dual-path retry.
    """
    stripped = url.strip()
    if not stripped:
        return stripped
    while stripped.endswith("/"):
        stripped = stripped[:-1]
        if not stripped:
            return url.strip()
    if match_byok_provider_preset(stripped) is not None:
        return stripped
    parsed = urlparse(stripped)
    if parsed.scheme and parsed.netloc and parsed.path in ("", "/"):
        return f"{stripped}/v1"
    return stripped


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


def seed_model_for_vendor(vendor_id: str) -> str:
    """Preset default model for this vendor, or empty when custom/unknown."""
    preset = preset_for_vendor(vendor_id)
    if preset is None:
        return ""
    return (preset.default_model or "").strip()


def preset_models_for_vendor(vendor_id: str) -> tuple[str, ...]:
    """Model ids from this vendor's preset, or empty when custom/unknown.

    OpenCode Go/Zen seeds are filtered through :func:`chat_completions_seed` so a
    leaked off-protocol id in the tuple cannot re-enter the catalog via presets.
    """
    preset = preset_for_vendor(vendor_id)
    if preset is None:
        return ()
    if is_opencode_vendor(vendor_id):
        return chat_completions_seed(*preset.models)
    return preset.models


def hide_from_picker_ids_for_vendor(vendor_id: str) -> frozenset[str]:
    """Exact ids this vendor omits from new picker rows (empty if custom)."""
    preset = preset_for_vendor(vendor_id)
    if preset is None:
        return frozenset()
    return frozenset(preset.hide_from_picker)
