"""Account-chosen display names for connected (BYOK) catalog rows.

Platform rows keep the product brand title. Inference / logs still use the
wire id. Picker membership is :mod:`model_visibility`.
"""

from __future__ import annotations

from agentcore.llm.model_ref import format_model_ref, parse_model_input

MAX_MODEL_DISPLAY_NAMES = 200
MAX_MODEL_DISPLAY_NAME_LEN = 64


class DisplayNameRefError(ValueError):
    """``ref`` is not a writable BYOK catalog identity."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DisplayNameCapError(ValueError):
    """Adding a new alias would exceed :data:`MAX_MODEL_DISPLAY_NAMES`."""


def normalize_display_name(raw: object) -> str | None:
    """Collapse whitespace; empty / non-str → ``None`` (revert to catalog brand)."""
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    return text[:MAX_MODEL_DISPLAY_NAME_LEN]


def normalize_model_display_names(raw: object) -> dict[str, str]:
    """Keep canonical ``@byok/…`` keys; drop junk, platform refs, empty names."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        parsed = parse_model_input(key)
        if parsed.kind != "ref" or parsed.origin != "byok":
            continue
        ref = format_model_ref(parsed.origin, parsed.model, parsed.provider_id or None)
        name = normalize_display_name(value)
        if not ref or not name or ref in out:
            continue
        out[ref] = name
        if len(out) >= MAX_MODEL_DISPLAY_NAMES:
            break
    return out


def apply_model_display_name(
    current: dict[str, str],
    ref: str,
    display_name: object,
) -> tuple[str, str | None, dict[str, str]]:
    """Upsert or delete one BYOK alias. Returns ``(canonical_ref, stored_or_none, map)``."""
    parsed = parse_model_input(ref)
    if parsed.kind != "ref":
        raise DisplayNameRefError("invalid")
    canonical = format_model_ref(
        parsed.origin, parsed.model, parsed.provider_id or None
    )
    if parsed.origin != "byok" or not canonical:
        raise DisplayNameRefError(
            "platform" if parsed.origin == "platform" else "invalid"
        )
    mapping = dict(current)
    name = normalize_display_name(display_name)
    if name is None:
        mapping.pop(canonical, None)
        return canonical, None, mapping
    if canonical not in mapping and len(mapping) >= MAX_MODEL_DISPLAY_NAMES:
        raise DisplayNameCapError()
    mapping[canonical] = name
    return canonical, name, mapping


def overlay_display_name(
    *,
    origin: str,
    ref: str,
    display_name: str,
    names: dict[str, str] | None,
) -> str:
    """BYOK alias wins; platform / missing alias keep the catalog brand."""
    if (origin or "").strip().lower() != "byok":
        return display_name
    alias = (names or {}).get(ref, "")
    return alias or display_name
