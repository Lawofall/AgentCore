"""Export the conformance vectors + their oracle-projected golden to the shared package.

Run from the server app: ``python -m agentcore.conformance.export``. Writes one
``<name>.json`` per vector into ``packages/protocol-conformance/fixtures/`` as
``{name, description, events, projected}`` (plus optional ``turnVerdict`` sidecar)
— the single source the frontend folds are asserted against (``pnpm conformance``).
Re-run after changing a vector or the oracle (then the frontends turn red until
aligned, per protocol-conformance.mdc).

Timestamps are assigned deterministically from ``run_completed.duration_ms``
(wall-clock; projection ignores them) so the committed golden does not churn
and desktop card 「用时」matches per-run durations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentcore.conformance.projection import project_turn
from agentcore.conformance.recording_cut import RECORDED_FIXTURE_PREFIX
from agentcore.conformance.timestamps import (
    format_stable_timestamp,
    wall_clock_ms_sequence,
)
from agentcore.conformance.turn_verdict import project_turn_verdict
from agentcore.conformance.vectors import VECTORS
from agentcore.runtime.events import SSEEvent

# apps/server/agentcore/conformance/export.py → repo root is parents[4].
_FIXTURES_DIR = (
    Path(__file__).resolve().parents[4] / "packages" / "protocol-conformance" / "fixtures"
)


def _serialize_events(events: list[SSEEvent]) -> list[dict[str, Any]]:
    """SSEEvent list → wire dicts with stable wall-clock timestamps."""
    pairs = [(ev.type.value, ev.payload) for ev in events]
    stamps = wall_clock_ms_sequence(pairs)
    return [
        {
            "type": typ,
            "payload": payload,
            "timestamp": format_stable_timestamp(ms),
        }
        for (typ, payload), ms in zip(pairs, stamps, strict=True)
    ]


def build_fixtures() -> list[dict[str, Any]]:
    """Project every vector into a committable fixture (vector + golden)."""
    fixtures: list[dict[str, Any]] = []
    for name, (description, builder) in VECTORS.items():
        events = _serialize_events(list(builder()))
        projected = project_turn(events)
        fixture: dict[str, Any] = {
            "name": name,
            "description": description,
            "events": events,
            "projected": projected,
        }
        turn_verdict = project_turn_verdict(name, projected)
        if turn_verdict is not None:
            fixture["turnVerdict"] = turn_verdict
        fixtures.append(fixture)
    return fixtures


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    fixtures = build_fixtures()
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    # Drop stale vector goldens this exporter OWNS (hand-authored VECTORS); preserve
    # recording-cut vectors (recording_cut.py's ``recorded_`` prefix — a separate
    # source, swept only by its own pipeline). Two sources, one judge: neither may
    # delete the other's files.
    for stale in _FIXTURES_DIR.glob("*.json"):
        if stale.name.startswith(RECORDED_FIXTURE_PREFIX):
            continue
        stale.unlink()
    for fx in fixtures:
        _write_json(_FIXTURES_DIR / f"{fx['name']}.json", fx)
    print(f"conformance: wrote {len(fixtures)} vector fixtures → {_FIXTURES_DIR}")


if __name__ == "__main__":
    main()
