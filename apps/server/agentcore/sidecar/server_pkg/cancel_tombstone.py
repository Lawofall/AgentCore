"""In-process cancel tombstones: a stop that arrived before startTurn registered.

Desktop ``startTurn`` awaits an in-flight warm (one cloud HTTP) before sending
the startTurn RPC. A click-stop in that window reaches sidecar with an unknown
``turnId``. Remember it so startTurn refuses — same idempotency idea as cloud
``POST …/stop`` (the stop is recorded; a later start of that turn is a no-op).

Bounded by TTL + max size so the map cannot grow without bound. Hits do not
consume the mark (same turnId retried after refuse must still be refused).
"""

from __future__ import annotations

import time

# Long enough for a slow account-warm HTTP + a startTurn retry; short enough
# that a stop whose startTurn never arrives does not linger for the process life.
CANCEL_TOMBSTONE_TTL_S = 120.0
CANCEL_TOMBSTONE_MAX = 256


def prune_cancel_tombstones(
    tombstones: dict[str, float], *, now: float | None = None
) -> None:
    """Drop expired entries, then evict soonest-expiring if over the cap."""
    deadline_now = time.monotonic() if now is None else now
    for turn_id in [tid for tid, exp in tombstones.items() if exp <= deadline_now]:
        tombstones.pop(turn_id, None)
    while len(tombstones) > CANCEL_TOMBSTONE_MAX:
        oldest = min(tombstones, key=tombstones.__getitem__)
        tombstones.pop(oldest, None)


def mark_cancel_tombstone(tombstones: dict[str, float], turn_id: str) -> None:
    """Record or refresh a cancel for ``turn_id`` (no-op when blank)."""
    tid = (turn_id or "").strip()
    if not tid:
        return
    now = time.monotonic()
    prune_cancel_tombstones(tombstones, now=now)
    tombstones[tid] = now + CANCEL_TOMBSTONE_TTL_S
    prune_cancel_tombstones(tombstones, now=now)


def cancel_tombstone_blocks(tombstones: dict[str, float], turn_id: str) -> bool:
    """True when startTurn must refuse this ``turn_id``."""
    tid = (turn_id or "").strip()
    if not tid:
        return False
    prune_cancel_tombstones(tombstones)
    return tid in tombstones
