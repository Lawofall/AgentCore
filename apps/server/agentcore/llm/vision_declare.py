"""Account-declared image accept for connected (BYOK) catalog rows.

SKU truth stays in :mod:`agentcore.llm.image_accept`. A BYOK person may declare
that one catalog identity accepts native image parts when the table has not
listed that wire id. Platform rows are not writable. Process-local negative
examples still win.
"""

from __future__ import annotations

from collections.abc import Iterable

from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.model_ref import format_model_ref, parse_model_input

MAX_VISION_ACCEPT_REFS = 200


class VisionDeclareRefError(ValueError):
    """``ref`` is not a writable BYOK catalog identity."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class VisionDeclareCapError(ValueError):
    """Adding a new declare would exceed :data:`MAX_VISION_ACCEPT_REFS`."""


def normalize_vision_accept_refs(raw: object) -> list[str]:
    """Keep canonical ``@byok/…`` refs; drop junk and platform identities."""
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        parsed = parse_model_input(item)
        if parsed.kind != "ref" or parsed.origin != "byok":
            continue
        ref = format_model_ref(
            parsed.origin, parsed.model, parsed.provider_id or None
        )
        if not ref or ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
        if len(out) >= MAX_VISION_ACCEPT_REFS:
            break
    return out


def apply_vision_accept(
    current: Iterable[str],
    ref: str,
    accept: bool,
) -> tuple[str, bool, list[str]]:
    """Upsert or delete one BYOK declare. Returns ``(canonical_ref, stored, list)``."""
    parsed = parse_model_input(ref)
    if parsed.kind != "ref":
        raise VisionDeclareRefError("invalid")
    canonical = format_model_ref(
        parsed.origin, parsed.model, parsed.provider_id or None
    )
    if parsed.origin != "byok" or not canonical:
        raise VisionDeclareRefError(
            "platform" if parsed.origin == "platform" else "invalid"
        )
    mapping = normalize_vision_accept_refs(list(current))
    present = canonical in mapping
    if accept:
        if not present:
            if len(mapping) >= MAX_VISION_ACCEPT_REFS:
                raise VisionDeclareCapError()
            mapping.append(canonical)
        return canonical, True, mapping
    if present:
        mapping = [item for item in mapping if item != canonical]
    return canonical, False, mapping


def vision_declared(
    model_id: str,
    credentials: LLMCredentials | None,
    refs: Iterable[str],
) -> bool:
    """True when this turn's BYOK catalog identity is in the account declare list."""
    if credentials is None or credentials.source != "user":
        return False
    ref = format_model_ref("byok", model_id, credentials.provider_id)
    if not ref:
        return False
    return ref in set(normalize_vision_accept_refs(list(refs)))
