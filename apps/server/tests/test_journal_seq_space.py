"""Outbox ``ord`` is the shared emission-order fact (Python + desktop TS)."""

from __future__ import annotations

from typing import Any

from agentcore.conversation.store.outbox import journal_entries_from_map
from agentcore.runtime.journal.seq_space import (
    JOURNAL_ENTRY_ORD_KEY,
    JOURNAL_OVERFLOW_SEQ_START,
    map_values_in_emission_order,
    next_overflow_seq,
    replace_prefix_map,
    seqs_from_map,
    stamp_missing_ords,
)


def _fact(kind: str, **payload: Any) -> dict[str, Any]:
    return {"kind": kind, "payload": payload, "ts": None}


def _pause_overflow_resume() -> dict[str, Any]:
    """Seal prefix → overflow terminals → resume live tail (production helpers)."""
    pause = [
        _fact("run_plan"),
        _fact("run_started", id="r1"),
        _fact("checkpoint_required"),
    ]
    resume = [
        *pause,
        _fact("checkpoint_resolved"),
        _fact("run_started", id="r2"),
    ]
    store = replace_prefix_map(pause, {})
    overflow_seq = next_overflow_seq(seqs_from_map(store))
    store[str(overflow_seq)] = _fact("run_completed", id="r1")
    store[str(overflow_seq + 1)] = _fact("execution_completed")
    return replace_prefix_map(resume, store)


def _kinds(entries: list[Any]) -> list[str]:
    return [str(item.get("kind") or "") for item in entries if isinstance(item, dict)]


def test_replace_prefix_stamps_ord_prefix_overflow_then_resume_tail() -> None:
    journal = _pause_overflow_resume()
    by_ord = sorted(
        (
            (item[JOURNAL_ENTRY_ORD_KEY], key, item["kind"])
            for key, item in journal.items()
            if isinstance(item, dict)
        ),
        key=lambda row: int(row[0]),
    )
    assert [kind for _, _, kind in by_ord] == [
        "run_plan",
        "run_started",
        "checkpoint_required",
        "run_completed",
        "execution_completed",
        "checkpoint_resolved",
        "run_started",
    ]
    # Occupancy keys still use the 1e6 overflow band; ``ord`` is the order fact.
    assert str(JOURNAL_OVERFLOW_SEQ_START) in journal
    assert journal[str(JOURNAL_OVERFLOW_SEQ_START)][JOURNAL_ENTRY_ORD_KEY] == 3


def test_ord_read_survives_integer_key_reorder() -> None:
    """JS ``JSON.parse`` / integer-index order would put live tail before overflow."""
    journal = _pause_overflow_resume()
    reordered = {key: journal[key] for key in sorted(journal, key=lambda k: int(k))}
    assert list(reordered) != list(journal)
    assert list(reordered)[3] == "3"  # resume tail occupancy, not overflow
    loaded = map_values_in_emission_order(reordered)
    kinds = _kinds(loaded)
    assert kinds.index("run_completed") < kinds.index("checkpoint_resolved")
    assert kinds[-1] == "run_started"
    payloads = [item.get("payload") for item in loaded if isinstance(item, dict)]
    assert payloads[-1] == {"id": "r2"}


def test_journal_entries_from_map_strips_ord_without_mutating_store() -> None:
    journal = _pause_overflow_resume()
    before = journal["0"][JOURNAL_ENTRY_ORD_KEY]
    loaded = journal_entries_from_map(journal)
    assert loaded is not None
    assert all(JOURNAL_ENTRY_ORD_KEY not in item for item in loaded)
    assert journal["0"][JOURNAL_ENTRY_ORD_KEY] == before
    assert _kinds(loaded)[3:5] == ["run_completed", "execution_completed"]


def test_legacy_map_without_ord_stays_live_then_overflow() -> None:
    """Old files: no ``ord`` → previous seq-asc (overflow after every live key)."""
    journal = {
        "0": _fact("run_plan"),
        "1": _fact("run_started", id="r2"),
        str(JOURNAL_OVERFLOW_SEQ_START): _fact("run_completed", id="r1"),
    }
    kinds = _kinds(map_values_in_emission_order(journal))
    assert kinds == ["run_plan", "run_started", "run_completed"]


def test_stamp_missing_ords_is_idempotent() -> None:
    journal = {"0": _fact("run_plan")}
    stamp_missing_ords(journal)
    stamp_missing_ords(journal)
    assert journal["0"][JOURNAL_ENTRY_ORD_KEY] == 0
    journal["1"] = _fact("run_started")
    stamp_missing_ords(journal)
    assert journal["1"][JOURNAL_ENTRY_ORD_KEY] == 1
    assert journal["0"][JOURNAL_ENTRY_ORD_KEY] == 0
