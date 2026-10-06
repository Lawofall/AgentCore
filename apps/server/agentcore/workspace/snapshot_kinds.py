"""Snapshot kind classification (axis-3 retention + UI grouping).

Mirrors desktop ``snapshotDisplay.ts`` so prune policy and the snapshots panel
agree on what is a user pin vs system artefact.

Industry posture (Time Machine / Dropbox version history / Git reflog-ish):
rolling auto backups, named pins kept, intermediate system checkpoints capped
and aged out — except open handoff Diff bases and each conversation's recent
turn baselines (newest N inside the TTL). Historical ``baseline_snapshot_id``
rows are not pins.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from agentcore.storage.protocol import SnapshotRef

SnapshotKind = Literal["auto", "kept", "baseline", "system"]

# Exact labels written by desktop export / merge / preview flows.
SYSTEM_EXACT_LABELS = frozenset(
    {
        "导出",
        "导出到本地",
        "浏览器预览",
        "合回到本机",
    }
)


def classify_snapshot_label(label: str | None) -> SnapshotKind:
    """Classify one snapshot label for retention + display."""
    if not label:
        return "auto"
    if label.startswith("turn-baseline:"):
        return "baseline"
    if label.startswith("handoff:") or label in SYSTEM_EXACT_LABELS:
        return "system"
    return "kept"


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def system_prune_ids(
    refs: list[SnapshotRef],
    *,
    baseline_max: int,
    other_max: int,
    max_age: timedelta,
    now: datetime | None = None,
    pinned_ids: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Return snapshot ids to delete under D+C (count cap ∧ TTL), newest-first ``refs``.

    User ``kept`` and unlabeled ``auto`` are ignored here (auto has its own cap).
    Within each system bucket, keep only entries that are both among the newest
    ``max`` and younger than ``max_age``; delete the rest.

    ``pinned_ids`` (open handoff Diff bases / each conversation's recent baselines)
    are never returned — callers collect them by storage key before prune.
    """
    if baseline_max < 0 or other_max < 0:
        return []
    clock = _aware(now or datetime.now(UTC))
    baselines: list[SnapshotRef] = []
    others: list[SnapshotRef] = []
    for ref in refs:
        kind = classify_snapshot_label(ref.label)
        if kind == "baseline":
            baselines.append(ref)
        elif kind == "system":
            others.append(ref)

    stale: list[str] = []
    stale.extend(_bucket_prune_ids(baselines, keep=baseline_max, max_age=max_age, now=clock))
    stale.extend(_bucket_prune_ids(others, keep=other_max, max_age=max_age, now=clock))
    if not pinned_ids:
        return stale
    return [sid for sid in stale if sid not in pinned_ids]


def byte_cap_prune_ids(
    refs: list[SnapshotRef],
    *,
    max_bytes: int,
    pinned_ids: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Return snapshot ids to delete so remaining total size fits ``max_bytes``.

    ``refs`` is newest-first (``list_snapshots`` order). Evicts oldest first
    among auto / baseline / system snapshots. User ``kept`` labels and
    ``pinned_ids`` are never returned. ``max_bytes <= 0`` disables. Never
    empties the history: a sole remaining snapshot is kept even if it alone
    exceeds the cap.
    """
    if max_bytes <= 0 or not refs:
        return []
    pinned = pinned_ids or set()
    total = sum(max(0, ref.size_bytes) for ref in refs)
    if total <= max_bytes:
        return []

    stale: list[str] = []
    remaining = len(refs)
    for ref in reversed(refs):
        if total <= max_bytes:
            break
        if remaining <= 1:
            break
        if ref.snapshot_id in pinned:
            continue
        if classify_snapshot_label(ref.label) == "kept":
            continue
        stale.append(ref.snapshot_id)
        total -= max(0, ref.size_bytes)
        remaining -= 1
    return stale


def _bucket_prune_ids(
    refs: list[SnapshotRef],
    *,
    keep: int,
    max_age: timedelta,
    now: datetime,
) -> list[str]:
    """``refs`` must already be newest-first (caller list order)."""
    if keep <= 0:
        return [r.snapshot_id for r in refs]
    out: list[str] = []
    for i, ref in enumerate(refs):
        too_old = now - _aware(ref.created_at) > max_age
        beyond_cap = i >= keep
        if too_old or beyond_cap:
            out.append(ref.snapshot_id)
    return out


def recent_baseline_pin_ids(
    rows: Sequence[tuple[str, str, datetime]],
    *,
    baseline_max: int,
    max_age: timedelta,
    now: datetime | None = None,
) -> set[str]:
    """Ids still owed a restore point: newest ``baseline_max`` per conversation, inside ``max_age``.

    A folder's snapshots share one storage key. The key-level count cap would
    drop a quiet conversation's only recent baseline when a sibling is busier,
    so those ids are pins. Rows outside the window are not: pinning every
    ``messages.baseline_snapshot_id`` exempts the whole history from both caps.

    ``rows`` is ``(conversation_id, snapshot_id, created_at)``. Blank ids are
    ignored. ``baseline_max <= 0`` keeps nothing from this axis. Newest first;
    the first row past ``max_age`` ends that conversation (older rows are older).
    """
    if baseline_max <= 0:
        return set()
    clock = _aware(now or datetime.now(UTC))
    grouped: dict[str, list[tuple[datetime, str]]] = {}
    for conversation_id, snapshot_id, created_at in rows:
        if not snapshot_id or not str(snapshot_id).strip() or created_at is None:
            continue
        grouped.setdefault(conversation_id, []).append(
            (_aware(created_at), str(snapshot_id).strip())
        )
    pinned: set[str] = set()
    for items in grouped.values():
        items.sort(key=lambda item: item[0], reverse=True)
        kept = 0
        seen: set[str] = set()
        for created_at, snapshot_id in items:
            if snapshot_id in seen:
                continue
            seen.add(snapshot_id)
            if clock - created_at > max_age or kept >= baseline_max:
                break
            pinned.add(snapshot_id)
            kept += 1
    return pinned
