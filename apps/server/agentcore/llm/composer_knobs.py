"""Account last knobs per composer model.

The current last-used snapshot stays a complete four-key row. Switching
models restores that leaf's last effort / context instead of carrying the
previous model's knobs. Missing key = vendor default / full window.
"""

from __future__ import annotations

from agentcore.llm.model_ref import format_model_ref, parse_model_input

KNOBS_KEY = "knobs_by_model"
MAX_COMPOSER_MODEL_KNOBS = 200


def slot_pointer(slot: object) -> str:
    if not isinstance(slot, dict):
        return ""
    provider = slot.get("provider_id")
    return format_model_ref(
        str(slot.get("origin") or ""),
        str(slot.get("model") or ""),
        provider if isinstance(provider, str) else None,
    )


def knob_entry(snapshot: dict) -> dict[str, str | int | None]:
    effort = snapshot.get("reasoning_effort")
    budget = snapshot.get("context_budget")
    effort_token = effort.strip() if isinstance(effort, str) else ""
    return {
        "reasoning_effort": effort_token or None,
        "context_budget": budget if isinstance(budget, int) and budget > 0 else None,
    }


def knobs_by_model(raw: dict | None) -> dict[str, dict[str, str | int | None]]:
    """Last effort / budget per catalog ref. Seeds the current snapshot's main."""
    if not isinstance(raw, dict):
        return {}
    blob = raw.get(KNOBS_KEY)
    out: dict[str, dict[str, str | int | None]] = {}
    if isinstance(blob, dict):
        for key, val in blob.items():
            if not isinstance(key, str) or not isinstance(val, dict):
                continue
            parsed = parse_model_input(key)
            if parsed.kind != "ref":
                continue
            ref = format_model_ref(
                parsed.origin, parsed.model, parsed.provider_id or None
            )
            if not ref or ref in out:
                continue
            out[ref] = knob_entry(val)
            if len(out) >= MAX_COMPOSER_MODEL_KNOBS:
                break
    pointer = slot_pointer(raw.get("main"))
    if pointer and pointer not in out:
        out[pointer] = knob_entry(raw)
        if len(out) > MAX_COMPOSER_MODEL_KNOBS:
            out.pop(next(iter(out)))
    return out


def snapshot_without_knobs(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {key: value for key, value in raw.items() if key != KNOBS_KEY}


def merge_composer_last_used(existing: dict | None, snapshot: dict) -> dict:
    """Write the last-used snapshot and upsert this main's knobs."""
    knobs = knobs_by_model(existing)
    pointer = slot_pointer(snapshot.get("main"))
    if pointer:
        knobs.pop(pointer, None)
        knobs[pointer] = knob_entry(snapshot)
        while len(knobs) > MAX_COMPOSER_MODEL_KNOBS:
            knobs.pop(next(iter(knobs)))
    stored = {key: value for key, value in snapshot.items() if key != KNOBS_KEY}
    stored[KNOBS_KEY] = knobs
    return stored
