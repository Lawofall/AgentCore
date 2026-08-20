"""Seq key space for the turn journal: live prefix vs post-seal overflow.

Postgres occupancy is ``(turn_id, band, seq)`` with ``band ∈ {live, overflow}`` —
see ``TurnJournalRepository``. This module is the **outbox JSON** twin: a flat
``seq(str) → {kind,payload,ts,ord?}`` map whose keys cannot be a composite PK. Live
keys stay ``0..n-1``; overflow keys stay in ``[JOURNAL_OVERFLOW_SEQ_START, ∞)``
so a later, longer prefix rewrite cannot grow into them. That encoding is the
on-disk outbox shape (``Record<string, unknown>``) and must not change.

Emission order is a fact on the entry: ``ord`` (int, assigned at first write,
preserved on prefix rewrite — outbox twin of Postgres ``created_at``). ``band``
alone cannot order: overflow is emitted *between* the seal prefix and the resume
live tail. ``ts`` may be null. Both Python and desktop TS read ``ord``; they must
not each re-derive order from keys. JS ``JSON.parse`` reorders integer-like keys,
so map insertion order is not a shared fact.

Legacy entries that lack ``ord`` keep the previous behaviour: trust insertion
when live keys appear on both sides of overflow, else live-then-overflow.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from agentcore.core.logging import get_logger

logger = get_logger(__name__)

JOURNAL_OVERFLOW_SEQ_START = 1_000_000

# Remaining live seqs at or below this (including already past the split) emit
# ``journal.live_seq_near_overflow``. Observation only — allocation is unchanged.
JOURNAL_LIVE_BAND_WARN_HEADROOM = 1_024

# On-disk emission index (outbox twin of ``turn_journal.created_at``). Desktop
# ``journalEntriesFromMap`` reads this same key — do not rename one side.
JOURNAL_ENTRY_ORD_KEY = "ord"


def is_overflow_seq(seq: int) -> bool:
    return seq >= JOURNAL_OVERFLOW_SEQ_START


def _int_seq(key: object) -> int | None:
    if isinstance(key, bool) or not isinstance(key, (int, str)):
        return None
    try:
        return int(key)
    except (TypeError, ValueError):
        return None


def _entry_ord(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    raw = value.get(JOURNAL_ENTRY_ORD_KEY)
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    return None


def _copy_existing_ords(out: dict[str, Any], existing: Mapping[str, Any]) -> None:
    """Keep ``ord`` on occupancy rewrite (Postgres copies ``created_at`` by seq)."""
    for key, value in out.items():
        if not isinstance(value, dict) or _entry_ord(value) is not None:
            continue
        src_ord = _entry_ord(existing.get(key))
        if src_ord is None:
            continue
        copied = dict(value)
        copied[JOURNAL_ENTRY_ORD_KEY] = src_ord
        out[key] = copied


def stamp_missing_ords(journal: dict[str, Any]) -> None:
    """Assign ``ord`` in current iteration order to entries that lack it.

    Callers must first arrange the dict in emission order (``replace_prefix_map``,
    append-at-end). Existing ``ord`` is never rewritten.
    """
    present = [_entry_ord(value) for value in journal.values()]
    known = [item for item in present if item is not None]
    next_ord = (max(known) + 1) if known else 0
    for key, value in list(journal.items()):
        if not isinstance(value, dict) or _entry_ord(value) is not None:
            continue
        stamped = dict(value)
        stamped[JOURNAL_ENTRY_ORD_KEY] = next_ord
        journal[key] = stamped
        next_ord += 1


def strip_entry_ord(value: Any) -> Any:
    """Drop storage ``ord`` so the wire list stays ``{kind, payload, ts}``."""
    if not isinstance(value, dict) or JOURNAL_ENTRY_ORD_KEY not in value:
        return value
    return {k: v for k, v in value.items() if k != JOURNAL_ENTRY_ORD_KEY}


def warn_live_seq_near_overflow(
    seq: int,
    *,
    op: str,
    turn_id: str | None = None,
) -> None:
    """Log when a live-band seq is near or past the overflow split.

    ``seq`` is the next live seq to allocate, or prefix occupancy (``len``).
    Does not change the value — callers still write whatever they computed.
    """
    remaining = JOURNAL_OVERFLOW_SEQ_START - seq
    if remaining > JOURNAL_LIVE_BAND_WARN_HEADROOM:
        return
    fields: dict[str, Any] = {
        "seq": seq,
        "overflow_start": JOURNAL_OVERFLOW_SEQ_START,
        "remaining": remaining,
        "op": op,
    }
    if turn_id:
        fields["turn_id"] = turn_id
    logger.warning("journal.live_seq_near_overflow", **fields)


def seqs_from_map(journal: Mapping[str, Any] | None) -> list[int]:
    """Integer keys of an outbox ``journal`` map (ignore non-numeric keys)."""
    out: list[int] = []
    for key in journal or ():
        seq = _int_seq(key)
        if seq is not None:
            out.append(seq)
    return out


def next_live_seq(seqs: Iterable[int]) -> int:
    """Next seq in the live/prefix band (ignores overflow-band keys)."""
    live = [s for s in seqs if 0 <= s < JOURNAL_OVERFLOW_SEQ_START]
    nxt = (max(live) + 1) if live else 0
    warn_live_seq_near_overflow(nxt, op="next_live_seq")
    return nxt


def next_overflow_seq(seqs: Iterable[int]) -> int:
    """Next seq in the post-seal overflow band."""
    tail = [s for s in seqs if s >= JOURNAL_OVERFLOW_SEQ_START]
    return (max(tail) + 1) if tail else JOURNAL_OVERFLOW_SEQ_START


def prefix_exclusive_end(snapshot_len: int) -> int:
    """Exclusive seq end this snapshot occupies in the live band.

    Empty snapshot: the whole live band (clear prefix, keep overflow).
    Non-empty: ``min(len, band)`` — never claims overflow-band seqs.
    """
    if snapshot_len <= 0:
        return JOURNAL_OVERFLOW_SEQ_START
    return min(snapshot_len, JOURNAL_OVERFLOW_SEQ_START)


def replace_prefix_map(
    snapshot: Sequence[Any],
    existing: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Write ``snapshot`` at live ``0..n-1``; keep overflow and live ``seq >= n``.

    Outbox twin of ``TurnJournalRepository.record``. Insertion order is emission
    order: rewritten seal prefix, then overflow, then the grown live tail, then
    any still-higher live seqs. Non-numeric keys (if any) are preserved at the end.
    Existing ``ord`` is copied by occupancy key (like Postgres ``created_at``);
    new facts get the next ``ord`` in that insertion order.
    """
    entries = [entry for entry in snapshot if isinstance(entry, dict)]
    warn_live_seq_near_overflow(len(entries), op="replace_prefix")
    exclusive_end = prefix_exclusive_end(len(entries))
    existing = existing or {}

    live_existing: list[int] = []
    overflow_pairs: list[tuple[int, Any]] = []
    late_live_pairs: list[tuple[int, Any]] = []
    other: list[tuple[str, Any]] = []
    for key, value in existing.items():
        seq = _int_seq(key)
        if seq is None:
            other.append((str(key), value))
            continue
        if seq >= JOURNAL_OVERFLOW_SEQ_START:
            overflow_pairs.append((seq, value))
        elif 0 <= seq < JOURNAL_OVERFLOW_SEQ_START:
            live_existing.append(seq)
            if seq >= exclusive_end:
                late_live_pairs.append((seq, value))
    overflow_pairs.sort()
    late_live_pairs.sort()
    old_occupancy = (max(live_existing) + 1) if live_existing else 0
    n = len(entries)
    seal_at = min(n, old_occupancy) if n > 0 else 0

    out: dict[str, Any] = {}
    if n <= 0:
        for seq, value in overflow_pairs:
            out[str(seq)] = value
        for seq, value in late_live_pairs:
            out[str(seq)] = value
        for key, value in other:
            out[key] = value
        _copy_existing_ords(out, existing)
        stamp_missing_ords(out)
        return out

    for seq in range(min(seal_at, exclusive_end)):
        out[str(seq)] = entries[seq]
    for seq, value in overflow_pairs:
        out[str(seq)] = value
    for seq in range(seal_at, min(n, exclusive_end)):
        out[str(seq)] = entries[seq]
    for seq, value in late_live_pairs:
        out[str(seq)] = value
    for key, value in other:
        out[key] = value
    _copy_existing_ords(out, existing)
    stamp_missing_ords(out)
    return out


def map_values_in_emission_order(journal: Mapping[str, Any]) -> list[Any]:
    """Outbox map values in emission order (not integer-key ascending).

    When every numeric-key dict has ``ord``, sort by that (JS-safe; same field
    as desktop ``journalEntriesFromMap``). Otherwise: new maps from
    :func:`replace_prefix_map` interleave overflow between the seal prefix and
    the grown live tail (live keys appear both before and after overflow in
    insertion order) — trust that order. Legacy maps that never did so fall
    back to live-then-overflow — the previous seq-asc behaviour.
    """
    numeric_dicts = [
        value
        for key, value in journal.items()
        if _int_seq(key) is not None and isinstance(value, dict)
    ]
    if numeric_dicts and all(_entry_ord(value) is not None for value in numeric_dicts):
        numbered: list[tuple[int, int, Any]] = []
        non_numeric: list[Any] = []
        for key, value in journal.items():
            seq = _int_seq(key)
            if seq is None:
                non_numeric.append(value)
                continue
            emit_ord = _entry_ord(value)
            numbered.append((emit_ord if emit_ord is not None else seq, seq, value))
        numbered.sort()
        return [value for _, _, value in numbered] + non_numeric

    live_before = False
    live_after = False
    saw_overflow = False
    for key in journal:
        seq = _int_seq(key)
        if seq is None:
            continue
        if is_overflow_seq(seq):
            saw_overflow = True
        elif saw_overflow:
            live_after = True
        else:
            live_before = True
        if live_before and live_after:
            return [journal[k] for k in journal]

    live: list[tuple[int, Any]] = []
    overflow: list[tuple[int, Any]] = []
    other: list[Any] = []
    for key, value in journal.items():
        seq = _int_seq(key)
        if seq is None:
            other.append(value)
            continue
        if is_overflow_seq(seq):
            overflow.append((seq, value))
        else:
            live.append((seq, value))
    live.sort()
    overflow.sort()
    return [v for _, v in live] + [v for _, v in overflow] + other


def projection_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    """``{kind, payload, ts}`` without storage seq."""
    return {
        "kind": row.get("kind"),
        "payload": row.get("payload"),
        "ts": row.get("ts"),
    }


def split_live_and_overflow_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split ``load_after`` rows into live-band vs overflow-band facts.

    Prefers the ``band`` column; falls back to the legacy integer-axis split for
    rows that predate the column (tests / stale shapes).
    """
    live: list[dict[str, Any]] = []
    tail: list[dict[str, Any]] = []
    for row in rows:
        item = projection_entry(row)
        band = row.get("band")
        if band is None:
            raw = row.get("seq")
            try:
                seq = int(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                seq = 0
            band = "overflow" if is_overflow_seq(seq) else "live"
        if band == "overflow":
            tail.append(item)
        else:
            live.append(item)
    return live, tail
