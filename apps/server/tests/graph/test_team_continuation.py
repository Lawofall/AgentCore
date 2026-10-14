"""同队续派一等入口：收口后走续派 / 补缺口不再被冷开闸拒。

此前 N>3 人的纯续派必被限流打回。补跑上限是为了拦「整团重开」，续派不是重开。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agentcore.runtime.graph.admit.post_close_gate import (
    EXECUTION_HARVEST_ORIGIN,
    post_close_cold_open_error,
)
from agentcore.runtime.graph.admit.team_continuation import classify_batch
from agentcore.runtime.runs.constants import MAX_GAP_FILL_ADDS
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState

_EXEC = "exec-continue"
_CONV = "conv-continue"


def _tool() -> MagicMock:
    t = MagicMock()
    t._user_message_origin = EXECUTION_HARVEST_ORIGIN
    t._depth = 0
    t._conversation_id = _CONV
    t._base_tool_context = SimpleNamespace(execution_id=_EXEC)
    return t


def _closed_session(*, done: list[str], failed: list[str]):
    """Register an inactive coordination session as the post-close roster."""
    from agentcore.runtime.graph.session.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    clear_active_coordination()
    session = CoordinationSession(
        execution_id=_EXEC, total_workers=len(done) + len(failed)
    )
    session.conversation_id = _CONV
    session.completed_run_ids = {*done, *failed}
    session.failed_run_ids = set(failed)
    session.active = False
    set_active_coordination(session)
    return session


def test_post_close_cold_subset_still_rejected():
    """真·整团重派仍拒：冷开子集自身达到 substantial。"""
    from agentcore.runtime.graph.session.session import clear_active_coordination

    _closed_session(done=["ok1"], failed=["gap1"])
    try:
        plan = RunPlan(
            nodes=[
                RunSpec(run_id="c1", role="A", task="补缺口", replaces_run_id="gap1"),
                *[RunSpec(run_id=f"n{i}", role=f"N{i}", task=f"新活 {i}") for i in range(3)],
            ]
        )
        err = post_close_cold_open_error(_tool(), plan)
        assert err is not None
        assert "收口后一次开了 3 段" in err
    finally:
        clear_active_coordination(_EXEC)


def test_post_close_gap_fill_still_capped():
    """补缺口那一堆的限流不放宽（与同图 replan 补跑闸同判定）。"""
    from agentcore.runtime.graph.session.session import clear_active_coordination

    gaps = [f"f{i}" for i in range(MAX_GAP_FILL_ADDS + 1)]
    _closed_session(done=[], failed=gaps)
    try:
        plan = RunPlan(
            nodes=[
                RunSpec(
                    run_id=f"retry_{g}", role=f"R{g}", task=f"补 {g}", replaces_run_id=g
                )
                for g in gaps
            ]
        )
        err = post_close_cold_open_error(_tool(), plan)
        assert err is not None
        assert "补跑一次最多" in err
    finally:
        clear_active_coordination(_EXEC)


def test_classify_batch_splits_by_structure_only():
    """补缺口看 replaces_run_id，其余是冷开。"""
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="c", role="C", task="t", replaces_run_id="gap1"),
            RunSpec(run_id="d", role="D", task="t"),
        ]
    )
    completed = {
        "gap1": RunState(phase=RunPhase.FAILED, error="e"),
    }
    shape = classify_batch(plan, completed)
    assert [n.run_id for n in shape.gap_fill] == ["c"]
    assert [n.run_id for n in shape.cold] == ["d"]
    assert shape.is_pure_continuation is False


def test_format_continuation_candidates_skips_captain_and_task():
    from agentcore.runtime.graph.admit.team_continuation import format_continuation_candidates
    from agentcore.runtime.runs.types import RunKind

    plan = RunPlan(
        nodes=[
            RunSpec(run_id="cap", role="CEO", task="主持", kind=RunKind.CAPTAIN),
            RunSpec(run_id="w1", role="调研员", task="秘密任务"),
        ]
    )
    completed = {"w1": RunState(phase=RunPhase.COMPLETED, content="ok")}
    text = format_continuation_candidates(plan=plan, completed=completed)
    assert "run_id=w1" in text
    assert "role=调研员" in text
    assert "status=completed" in text
    assert "秘密任务" not in text
    assert "run_id=cap" not in text


def test_cold_open_reject_message_omits_retired_graph_tag():
    from agentcore.runtime.graph.admit.team_continuation import (
        ContinuationShape,
        cold_open_reject_message,
    )

    shape = ContinuationShape(cold=(object(), object(), object()))
    err = cold_open_reject_message(shape)
    assert "<近期团队图>" not in err
    assert "graph" in err
    assert "continue_from_run_id" not in err
    assert "replaces_run_id" not in err
    with_c = cold_open_reject_message(
        shape, candidates="- run_id=w1; role=A; status=completed"
    )
    assert "已有结论" in with_c
    assert "run_id=w1" in with_c
    assert "continue_from_run_id" not in with_c
    assert "<近期团队图>" not in with_c
