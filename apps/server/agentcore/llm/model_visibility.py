"""Which catalog refs appear on composer pickers.

The catalog itself is unchanged: membership is a picker filter, not inference
policy. Unknown or malformed refs are dropped on write.

Polarity by origin — the inventory is the full catalog; the picker is a
shortlist:

* ``origin=platform`` — in the picker unless listed in ``hidden_refs``.
* ``origin=byok`` — off the picker unless listed in ``enabled_refs``.
  Connecting a provider therefore does not dump ``GET /models`` into the
  composer. Settings · 模型 is where a person opens the rows they want.

``hidden_refs`` still wins when a ref is in both lists (legacy BYOK denylist
rows stay out until the account writes a clean allowlist).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from agentcore.llm.model_ref import (
    BYOK_REF_PREFIX,
    format_model_ref,
    parse_model_input,
)

MAX_HIDDEN_MODEL_REFS = 200
MAX_PICKER_MODEL_REFS = MAX_HIDDEN_MODEL_REFS


def normalize_hidden_model_refs(raw: object) -> list[str]:
    """Canonical ``@platform/…`` / ``@byok/…`` refs, de-duplicated, capped."""
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        parsed = parse_model_input(item)
        if parsed.kind != "ref":
            continue
        ref = format_model_ref(
            parsed.origin, parsed.model, parsed.provider_id or None
        )
        if not ref or ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
        if len(out) >= MAX_PICKER_MODEL_REFS:
            break
    return out


normalize_picker_model_refs = normalize_hidden_model_refs


def byok_refs_from_slots(*slots: object) -> list[str]:
    """Canonical ``@byok/…`` refs from stored override / profile slot mappings."""
    refs: list[str] = []
    for slot in slots:
        mapping: Mapping[str, object] | None
        if isinstance(slot, Mapping):
            mapping = slot
        elif slot is None:
            continue
        else:
            origin = getattr(slot, "origin", None)
            model = getattr(slot, "model", None)
            if origin is None or model is None:
                continue
            mapping = {
                "origin": origin,
                "model": model,
                "provider_id": getattr(slot, "provider_id", None),
            }
        if str(mapping.get("origin") or "").strip().lower() != "byok":
            continue
        ref = format_model_ref(
            "byok",
            str(mapping.get("model") or ""),
            str(mapping.get("provider_id") or "") or None,
        )
        if ref:
            refs.append(ref)
    return refs


def byok_refs_from_override(payload: Mapping[str, object] | None) -> list[str]:
    """BYOK refs from a conversation / composer override snapshot."""
    if not isinstance(payload, Mapping):
        return []
    return byok_refs_from_slots(
        payload.get("main"),
        payload.get("worker"),
        payload.get("background"),
        payload.get("vision"),
    )


def merge_enabled_picker_refs(
    current: Sequence[str], extra: Sequence[str]
) -> list[str]:
    """Union BYOK refs onto the picker allowlist, preserving order and the cap."""
    base = normalize_picker_model_refs(list(current))
    more = [
        ref
        for ref in normalize_picker_model_refs(list(extra))
        if ref.startswith(BYOK_REF_PREFIX)
    ]
    seen = set(base)
    out = list(base)
    for ref in more:
        if ref in seen:
            continue
        out.append(ref)
        seen.add(ref)
        if len(out) >= MAX_PICKER_MODEL_REFS:
            break
    return out


def hidden_ref_set(raw: Iterable[str] | None) -> frozenset[str]:
    return frozenset(normalize_hidden_model_refs(list(raw or ())))


def picker_ref_enabled(
    ref: str,
    origin: str,
    *,
    hidden_refs: Iterable[str] | None = None,
    enabled_refs: Iterable[str] | None = None,
) -> bool:
    """Whether this catalog identity belongs on a composer / slot picker."""
    hidden = hidden_ref_set(hidden_refs)
    if ref in hidden:
        return False
    if (origin or "").strip().lower() == "platform":
        return True
    return ref in hidden_ref_set(enabled_refs)
