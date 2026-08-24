"""Validate SSE wire contract alignment across Python ↔ TypeScript.

Checks (fail-closed, run as part of ``pnpm gen:types``):

1. ``runtime.events.types.EventType`` values == keys of ``SSEPayloadMap`` in
   ``packages/contract-types/src/events.generated.ts``.
2. ``eventTypes.generated.ts`` union == ``EventType``.
3. Every ``EventType`` has a payload wire model in ``payloads/__init__.py``.
4. ``interactionKinds.generated.ts`` == ``InteractionKind`` + ``INTERACTION_KIND_SPECS``.
5. ``errorCodes.generated.ts`` == ``ErrorCode``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from agentcore.core.error_codes import ErrorCode
from agentcore.runtime.events.payloads import EVENT_PAYLOAD_MODELS
from agentcore.runtime.events.types import RETIRED_EVENT_TYPE_VALUES, EventType
from agentcore.runtime.interaction import INTERACTION_KIND_SPECS, InteractionKind

ROOT = Path(__file__).resolve().parents[3]
GENERATED_EVENTS = ROOT / "packages" / "contract-types" / "src" / "events.generated.ts"
GENERATED_TYPES = ROOT / "packages" / "contract-types" / "src" / "eventTypes.generated.ts"
GENERATED_INTERACTION_KINDS = (
    ROOT / "packages" / "contract-types" / "src" / "interactionKinds.generated.ts"
)
GENERATED_ERROR_CODES = ROOT / "packages" / "contract-types" / "src" / "errorCodes.generated.ts"


def _event_type_values() -> set[str]:
    return {e.value for e in EventType}


def _parse_payload_map_keys(text: str) -> set[str]:
    """Extract keys from ``export type SSEPayloadMap = { ... }``."""
    m = re.search(
        r"export type SSEPayloadMap\s*=\s*\{([^}]+)\}",
        text,
        re.DOTALL,
    )
    if not m:
        raise RuntimeError("SSEPayloadMap block not found in events.generated.ts")
    keys: set[str] = set()
    for line in m.group(1).splitlines():
        hit = re.match(r'\s*(?:"([^"]+)"|([a-z_][a-z0-9_]*))\s*:', line)
        if hit:
            keys.add(hit.group(1) or hit.group(2))
    return keys


def _parse_generated_union(text: str) -> set[str]:
    return set(re.findall(r'"([^"]+)"', text))


def main() -> None:
    errors: list[str] = []
    py_events = _event_type_values()

    generated_events_text = GENERATED_EVENTS.read_text(encoding="utf-8")
    payload_keys = _parse_payload_map_keys(generated_events_text)

    only_py = sorted(py_events - payload_keys)
    only_ts = sorted(payload_keys - py_events)
    if only_py:
        errors.append(f"EventType missing from SSEPayloadMap: {', '.join(only_py)}")
    if only_ts:
        errors.append(f"SSEPayloadMap keys missing from EventType: {', '.join(only_ts)}")

    generated_types_text = GENERATED_TYPES.read_text(encoding="utf-8")
    gen_events = _parse_generated_union(generated_types_text)
    only_py_gen = sorted(py_events - gen_events)
    only_gen = sorted(gen_events - py_events)
    if only_py_gen:
        errors.append(f"EventType missing from eventTypes.generated.ts: {', '.join(only_py_gen)}")
    if only_gen:
        errors.append(f"eventTypes.generated.ts extras not in EventType: {', '.join(only_gen)}")

    model_missing = sorted(e for e in EventType if e not in EVENT_PAYLOAD_MODELS)
    if model_missing:
        errors.append(
            "EventType missing payload wire model: "
            + ", ".join(e.value for e in model_missing)
        )

    # InteractionKind + wire table
    ik_text = GENERATED_INTERACTION_KINDS.read_text(encoding="utf-8")
    py_ik = {e.value for e in InteractionKind}
    gen_ik = _parse_generated_union(
        # First union in the file is InteractionKind (full enum).
        ik_text.split("export type UserInteractionKind", 1)[0]
    )
    only_py_ik = sorted(py_ik - gen_ik)
    only_gen_ik = sorted(gen_ik - py_ik)
    if only_py_ik:
        errors.append(
            f"InteractionKind missing from interactionKinds.generated.ts: {', '.join(only_py_ik)}"
        )
    if only_gen_ik:
        errors.append(
            f"interactionKinds.generated.ts extras not in InteractionKind: {', '.join(only_gen_ik)}"
        )
    py_wire = {k.value for k in INTERACTION_KIND_SPECS}
    # Second union = UserInteractionKind
    user_block = ik_text.split("export type UserInteractionKind", 1)[-1]
    user_block = user_block.split("export const USER_INTERACTION_KIND_VALUES", 1)[0]
    gen_wire = set(re.findall(r'"([^"]+)"', user_block))
    only_py_wire = sorted(py_wire - gen_wire)
    only_gen_wire = sorted(gen_wire - py_wire)
    if only_py_wire:
        errors.append(
            f"INTERACTION_KIND_SPECS missing from UserInteractionKind: {', '.join(only_py_wire)}"
        )
    if only_gen_wire:
        errors.append(
            f"UserInteractionKind extras not in INTERACTION_KIND_SPECS: {', '.join(only_gen_wire)}"
        )
    # Leftover kinds (e.g. team_preview → 410) keep wire names after the
    # events themselves enter RETIRED_EVENT_TYPE_VALUES.
    known_wire = py_events | RETIRED_EVENT_TYPE_VALUES
    for kind, spec in INTERACTION_KIND_SPECS.items():
        if spec.required_event not in known_wire:
            errors.append(
                f"INTERACTION_KIND_SPECS[{kind.value}].required_event "
                f"{spec.required_event!r} not in EventType or RETIRED_EVENT_TYPE_VALUES"
            )
        if spec.resolved_event is not None and spec.resolved_event not in known_wire:
            errors.append(
                f"INTERACTION_KIND_SPECS[{kind.value}].resolved_event "
                f"{spec.resolved_event!r} not in EventType or RETIRED_EVENT_TYPE_VALUES"
            )

    # ErrorCode catalog
    ec_text = GENERATED_ERROR_CODES.read_text(encoding="utf-8")
    py_ec = {e.value for e in ErrorCode}
    gen_ec = _parse_generated_union(ec_text)
    only_py_ec = sorted(py_ec - gen_ec)
    only_gen_ec = sorted(gen_ec - py_ec)
    if only_py_ec:
        errors.append(
            f"ErrorCode missing from errorCodes.generated.ts: {', '.join(only_py_ec)}"
        )
    if only_gen_ec:
        errors.append(
            f"errorCodes.generated.ts extras not in ErrorCode: {', '.join(only_gen_ec)}"
        )

    if errors:
        for e in errors:
            print(f"validate_sse_contract: {e}", file=sys.stderr)
        sys.exit(1)

    print(
        f"validate_sse_contract: OK ({len(py_events)} event types, "
        f"{len(py_ik)} interaction kinds, {len(py_ec)} error codes, "
        f"payload models + SSEPayloadMap + generated unions aligned)"
    )


if __name__ == "__main__":
    main()
