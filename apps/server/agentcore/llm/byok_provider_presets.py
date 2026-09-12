"""BYOK vendor presets — catalog seed + off-protocol ids.

Table source: ``byok_provider_presets.json`` next to this module (desktop form
loads the same file). Catalog merge matches providers by normalized ``base_url``;
unknown endpoints get no preset rows.

Off-protocol model ids (need ``/responses`` or ``/messages``; this gateway only
speaks ``chat/completions``) live in :data:`BYOK_OFF_PROTOCOL_MODELS` — the single
exact-id map for OpenCode seed exclusion, BYOK catalog unavailability, **and**
platform-allowlist catalog unavailability. Name kept (historical); both origins
call :func:`off_protocol_kind`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

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

# Exact ids only — never substring / regex. Shared by BYOK (OpenCode Go/Zen
# discovery) and platform (operator allowlist). OpenCode ``GET /models`` still
# returns these; they stay out of chat/completions seeds and are listed-but-
# unselectable in the catalog merge (not dropped at discovery / allowlist).
BYOK_OFF_PROTOCOL_MODELS: Mapping[str, OffProtocolKind] = MappingProxyType(
    _off_protocol_from_raw(_RAW)
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
    # Exact wire ids omitted from the chat picker after seed ∪ discovery.
    # Probe ``default_model`` is not a picker source when a preset matches.
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


def hide_from_picker_ids(base_url: str) -> frozenset[str]:
    """Exact ids a matched preset omits from new picker rows (empty if unmatched)."""
    preset = match_byok_provider_preset(base_url)
    if preset is None:
        return frozenset()
    return frozenset(preset.hide_from_picker)
