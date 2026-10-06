"""Unit tests for snapshot kind classification + D+C system prune selection."""

from datetime import UTC, datetime, timedelta

from agentcore.storage.protocol import SnapshotRef
from agentcore.workspace.snapshot_kinds import (
    byte_cap_prune_ids,
    classify_snapshot_label,
    recent_baseline_pin_ids,
    system_prune_ids,
)


def _ref(
    sid: str,
    label: str | None,
    *,
    days_ago: float = 0,
    now: datetime | None = None,
    size_bytes: int = 1,
) -> SnapshotRef:
    clock = now or datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    return SnapshotRef(
        snapshot_id=sid,
        label=label,
        created_at=clock - timedelta(days=days_ago),
        size_bytes=size_bytes,
    )


def test_classify_kinds():
    assert classify_snapshot_label(None) == "auto"
    assert classify_snapshot_label("发版前") == "kept"
    assert classify_snapshot_label("turn-baseline:m1") == "baseline"
    assert classify_snapshot_label("handoff:2026-01-01T00:00:00Z") == "system"
    assert classify_snapshot_label("导出") == "system"
    assert classify_snapshot_label("合回到本机") == "system"


def test_system_prune_respects_baseline_and_other_caps():
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    # Newest first (list_snapshots order).
    refs = [
        _ref("b6", "turn-baseline:6", now=now),
        _ref("b5", "turn-baseline:5", now=now),
        _ref("b4", "turn-baseline:4", now=now),
        _ref("b3", "turn-baseline:3", now=now),
        _ref("b2", "turn-baseline:2", now=now),
        _ref("b1", "turn-baseline:1", now=now),
        _ref("e3", "导出", now=now),
        _ref("e2", "导出到本地", now=now),
        _ref("e1", "合回到本机", now=now),
        _ref("kept", "发版前", now=now),
        _ref("auto", None, now=now),
    ]
    stale = system_prune_ids(
        refs,
        baseline_max=5,
        other_max=2,
        max_age=timedelta(days=30),
        now=now,
    )
    assert set(stale) == {"b1", "e1"}  # 6th baseline + 3rd other
    assert "kept" not in stale
    assert "auto" not in stale


def test_system_prune_ttl_deletes_even_under_cap():
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    refs = [
        _ref("fresh", "turn-baseline:new", days_ago=1, now=now),
        _ref("old", "turn-baseline:old", days_ago=31, now=now),
        _ref("old_export", "导出", days_ago=40, now=now),
        _ref("pin", "重要", days_ago=90, now=now),
    ]
    stale = system_prune_ids(
        refs,
        baseline_max=5,
        other_max=10,
        max_age=timedelta(days=30),
        now=now,
    )
    assert set(stale) == {"old", "old_export"}
    assert "pin" not in stale
    assert "fresh" not in stale


def test_system_prune_skips_pinned_ids():
    """Referenced handoff base / turn baseline ids must not be pruned."""
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    refs = [
        _ref("b3", "turn-baseline:3", now=now),
        _ref("b2", "turn-baseline:2", now=now),
        _ref("b1", "turn-baseline:1", now=now),
        _ref("h2", "handoff:2026-01-02T00:00:00Z", now=now),
        _ref("h1", "handoff:2026-01-01T00:00:00Z", now=now),
    ]
    # Cap would delete b1 + h1; pin both referenced ids → neither pruned.
    stale = system_prune_ids(
        refs,
        baseline_max=2,
        other_max=1,
        max_age=timedelta(days=30),
        now=now,
        pinned_ids={"b1", "h1"},
    )
    assert stale == []


def test_system_prune_still_deletes_unpinned_over_cap():
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    refs = [
        _ref("b3", "turn-baseline:3", now=now),
        _ref("b2", "turn-baseline:2", now=now),
        _ref("b1", "turn-baseline:1", now=now),
        _ref("h2", "handoff:2026-01-02T00:00:00Z", now=now),
        _ref("h1", "handoff:2026-01-01T00:00:00Z", now=now),
    ]
    stale = system_prune_ids(
        refs,
        baseline_max=2,
        other_max=1,
        max_age=timedelta(days=30),
        now=now,
        pinned_ids={"b2"},  # only mid baseline pinned; b1 + h1 still go
    )
    assert set(stale) == {"b1", "h1"}


def test_byte_cap_prunes_oldest():
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    refs = [
        _ref("n3", None, days_ago=0, now=now, size_bytes=100),
        _ref("n2", None, days_ago=1, now=now, size_bytes=100),
        _ref("n1", None, days_ago=2, now=now, size_bytes=100),
    ]
    stale = byte_cap_prune_ids(refs, max_bytes=250)
    assert stale == ["n1"]


def test_byte_cap_skips_kept_label():
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    refs = [
        _ref("auto2", None, days_ago=0, now=now, size_bytes=100),
        _ref("auto1", None, days_ago=1, now=now, size_bytes=100),
        _ref("kept", "发版前", days_ago=2, now=now, size_bytes=100),
    ]
    stale = byte_cap_prune_ids(refs, max_bytes=150)
    assert "kept" not in stale
    assert set(stale) == {"auto1", "auto2"}


def test_recent_baseline_pins_are_per_conversation_and_inside_ttl():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    rows = [
        ("c-busy", "b3", now - timedelta(days=1)),
        ("c-busy", "b2", now - timedelta(days=2)),
        ("c-busy", "b1", now - timedelta(days=3)),
        ("c-busy", "old", now - timedelta(days=40)),
        ("c-quiet", "q1", now - timedelta(days=4)),
        ("c-blank", "", now),
    ]
    pinned = recent_baseline_pin_ids(
        rows, baseline_max=2, max_age=timedelta(days=30), now=now
    )
    assert pinned == {"b3", "b2", "q1"}


def test_recent_baseline_pins_empty_when_cap_disabled():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    rows = [("c1", "b1", now)]
    assert recent_baseline_pin_ids(rows, baseline_max=0, max_age=timedelta(days=30), now=now) == set()


def test_byte_cap_skips_pinned_ids():
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    refs = [
        _ref("new", None, days_ago=0, now=now, size_bytes=100),
        _ref("mid", None, days_ago=1, now=now, size_bytes=100),
        _ref("pinned", "turn-baseline:old", days_ago=2, now=now, size_bytes=100),
    ]
    stale = byte_cap_prune_ids(refs, max_bytes=250, pinned_ids={"pinned"})
    assert stale == ["mid"]
    assert "pinned" not in stale
