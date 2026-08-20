"""Late overflow terminals vs post-resume live facts must not poison projection.

Store layout (two-band seq, as production writes it today):

1. Live prefix, dense from seq 0 — ``run_plan`` / first ``run_started`` / pause gate.
2. During seal, the background worker finishes: ``run_completed`` and
   ``execution_completed`` land in the overflow band (``JOURNAL_OVERFLOW_SEQ_START+``).
3. After resume the same agent starts again: a live-band ``run_started`` whose seq
   is still far below the overflow split.

``TurnJournalRepository.load`` / ``journal_entries_from_map`` sort by seq ascending,
so (2) is folded *after* (3). ``project_turn`` then last-wins ``run_completed`` onto
the agent (``status="completed"``, ``currentRunId=None``) even though the causal
timeline has them working on the post-resume run. Mobile ``fold.ts`` last-wins
``execution_completed`` the same way.

This file is the ratchet: expectations are hand-derived from wall-clock order, never
from seq-fold output or a golden. Existing overflow tests only assert the rows
survive rewrite; they never fold. No conformance vector — live SSE is causal; the
defect is the reload read path.
"""

from __future__ import annotations

from typing import Any

from agentcore.conformance.projection import project_turn
from agentcore.conversation.store.outbox import journal_entries_from_map
from agentcore.runtime.events import (
    SSEEvent,
    checkpoint_required,
    checkpoint_resolved,
    execution_completed,
    run_completed,
    run_plan,
    run_started,
)
from agentcore.runtime.journal.fold import runs_from_entries
from agentcore.runtime.journal.seq_space import (
    JOURNAL_OVERFLOW_SEQ_START,
    next_overflow_seq,
    replace_prefix_map,
    seqs_from_map,
)

_CONV = "conv_demo"
_USAGE = {
    "input": 1200,
    "output": 300,
    "reasoning": 120,
    "cache_hit": 800,
    "cache_miss": 400,
}
_COST = {
    "input": 240_000,
    "cached": 64_000,
    "output": 120_000,
    "total": 360_000,
    "currency": "CNY",
}


def _entry(event: SSEEvent, *, ts: str) -> dict[str, Any]:
    return {"kind": event.type.value, "payload": event.payload, "ts": ts}


def _plan_events() -> tuple[SSEEvent, SSEEvent, SSEEvent]:
    """One agent, two sequential runs — r2 is the post-resume rerun of w1."""
    agents = [{"id": "w1", "role": "研究员", "thinking": True}]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "先调研", "depends_on": []},
        {"id": "r2", "agent_id": "w1", "task": "resume 后续挖", "depends_on": ["r1"]},
    ]
    plan = run_plan(
        execution_id="exec-bg",
        plan_type="multi_agent",
        task_summary="调研后同人续挖",
        agents=agents,
        runs=plan_runs,
    )
    start_r1 = run_started("r1", "w1", kind="agent")
    start_r2 = run_started("r2", "w1", kind="agent")
    return plan, start_r1, start_r2


def _pause_gate() -> SSEEvent:
    return checkpoint_required(
        checkpoint_id="cp-seal",
        conversation_id=_CONV,
        question="后台还在跑，先停一下？",
        context="队员已开工，封盘期间仍可能写完。",
        intent="kickoff",
    )


def _overflow_terminals() -> tuple[dict[str, Any], dict[str, Any]]:
    completed = _entry(
        run_completed(
            "r1",
            "w1",
            output_summary="调研完成",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            execution_id="exec-bg",
        ),
        ts="t-overflow-run",
    )
    exec_done = _entry(
        execution_completed(
            execution_id="exec-bg",
            conversation_id=_CONV,
            completed=1,
            total=1,
            status="completed",
            host_turn_id="m1",
        ),
        ts="t-overflow-exec",
    )
    return completed, exec_done


def _two_band_journal() -> dict[str, dict[str, Any]]:
    """Pause prefix + overflow terminals + grown resume prefix (production write helpers)."""
    plan, start_r1, start_r2 = _plan_events()
    pause_prefix = [
        _entry(plan, ts="t0"),
        _entry(start_r1, ts="t1"),
        _entry(_pause_gate(), ts="t2"),
    ]
    resume_prefix = [
        *pause_prefix,
        _entry(
            checkpoint_resolved(checkpoint_id="cp-seal", decision="continue"),
            ts="t5",
        ),
        _entry(start_r2, ts="t6"),
    ]
    store = replace_prefix_map(pause_prefix, {})
    overflow_seq = next_overflow_seq(seqs_from_map(store))
    completed, exec_done = _overflow_terminals()
    store[str(overflow_seq)] = completed
    store[str(overflow_seq + 1)] = exec_done
    return replace_prefix_map(resume_prefix, store)


def _load_seq_asc(journal: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Outbox twin of ``TurnJournalRepository.load`` (``ORDER BY seq ASC``)."""
    loaded = journal_entries_from_map(journal)
    assert loaded is not None
    return loaded


def _project(entries: list[dict[str, Any]]) -> dict[str, Any]:
    folded = runs_from_entries(entries)
    assert folded is not None
    return project_turn(folded["events"])


def _assert_causal_timeline(projected: dict[str, Any]) -> None:
    """True wall-clock: r1 finished during the pause; after resume w1 is on r2.

    Not copied from seq-fold / golden — that path last-wins the overflow
    ``run_completed`` onto w1 and would agree with itself.
    """
    assert projected["status"] == "running"
    assert projected["finishReason"] is None
    w1 = next(a for a in projected["agents"] if a["id"] == "w1")
    # Pair so a red run shows both clobbers (status + currentRunId) in one diff.
    assert (w1["status"], w1["currentRunId"]) == ("working", "r2")
    by_id = {r["id"]: r for r in projected["runs"]}
    assert by_id["r1"]["status"] == "completed"
    assert by_id["r2"]["status"] == "running"


def test_causal_entry_order_projects_the_rerun_agent_as_working() -> None:
    """Control: the hand-derived timeline is what fold produces when order is causal."""
    plan, start_r1, start_r2 = _plan_events()
    completed, exec_done = _overflow_terminals()
    causal = [
        _entry(plan, ts="t0"),
        _entry(start_r1, ts="t1"),
        _entry(_pause_gate(), ts="t2"),
        completed,
        exec_done,
        _entry(
            checkpoint_resolved(checkpoint_id="cp-seal", decision="continue"),
            ts="t5",
        ),
        _entry(start_r2, ts="t6"),
    ]
    _assert_causal_timeline(_project(causal))


def test_seq_asc_load_of_overflow_terminals_must_match_causal_timeline() -> None:
    """Reload path: two-band seq journal → seq-asc load → fold → causal projection.

    Expected RED until load (or fold) stops letting overflow terminals clobber a
    later live ``run_started`` for the same agent.
    """
    journal = _two_band_journal()
    assert str(JOURNAL_OVERFLOW_SEQ_START) in journal
    assert str(JOURNAL_OVERFLOW_SEQ_START + 1) in journal
    loaded = _load_seq_asc(journal)
    _assert_causal_timeline(_project(loaded))
