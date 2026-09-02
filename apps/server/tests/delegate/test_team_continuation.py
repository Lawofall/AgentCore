"""同队续派一等入口：收口后走续派 / 补缺口不再被冷开闸拒。

此前 N>3 人的纯续派必被限流打回。补跑上限是为了拦「整团重开」，续派不是重开。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agentcore.runtime.delegate.post_close_gate import (
    EXECUTION_HARVEST_ORIGIN,
    post_close_cold_open_error,
)
from agentcore.runtime.delegate.team_continuation import classify_batch
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
    from agentcore.runtime.coordination.session import (
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


def _continue_nodes(targets: list[str]) -> RunPlan:
    return RunPlan(
        nodes=[
            RunSpec(
                run_id=f"cont_{rid}",
                role=f"R_{rid}",
                task=f"接着干 {rid}",
                continue_from_run_id=rid,
            )
            for rid in targets
        ]
    )


# ── 一等入口：收口后同队续派 ─────────────────────────────────────────────────


def test_post_close_same_person_continuation_beyond_gap_cap_admitted():
    """核心回归：收口后续派 5 名已完成队员不再被补跑上限打回。

    补跑上限是为了拦「整团重开」，续派不是重开——旧实现把所有点名节点一起限流，
    于是超过 MAX_GAP_FILL_ADDS 人的团队根本无法整体续派。
    """
    from agentcore.runtime.coordination.session import clear_active_coordination

    done = [f"ok{i}" for i in range(5)]
    assert len(done) > MAX_GAP_FILL_ADDS
    _closed_session(done=done, failed=[])
    try:
        err = post_close_cold_open_error(_tool(), _continue_nodes(done))
        assert err is None
    finally:
        clear_active_coordination(_EXEC)


def test_post_close_continuation_admitted_without_known_roster():
    """名册查不到（跨回合内存已清）也不得因此拒续派——存在性由续派执行层如实拒。"""
    from agentcore.runtime.coordination.session import clear_active_coordination

    clear_active_coordination()
    err = post_close_cold_open_error(
        _tool(), _continue_nodes([f"ok{i}" for i in range(5)])
    )
    assert err is None


def test_post_close_mixed_batch_only_gates_the_cold_subset():
    """路由而非一刀切：续派节点不把整批算成「整团重派」。"""
    from agentcore.runtime.coordination.session import clear_active_coordination

    _closed_session(done=["ok1", "ok2", "ok3"], failed=[])
    try:
        plan = RunPlan(
            nodes=[
                RunSpec(
                    run_id="c1", role="A", task="接着干", continue_from_run_id="ok1"
                ),
                RunSpec(
                    run_id="c2", role="B", task="接着干", continue_from_run_id="ok2"
                ),
                RunSpec(run_id="n1", role="C", task="顺手补一小块"),
            ]
        )
        # 整批 3 个节点（旧实现算 substantial 且未全员点名 → 拒），冷开子集只有 1 个。
        assert post_close_cold_open_error(_tool(), plan) is None
    finally:
        clear_active_coordination(_EXEC)


def test_post_close_cold_subset_still_rejected():
    """真·整团重派仍拒：冷开子集自身达到 substantial。"""
    from agentcore.runtime.coordination.session import clear_active_coordination

    _closed_session(done=["ok1"], failed=[])
    try:
        plan = RunPlan(
            nodes=[
                RunSpec(
                    run_id="c1", role="A", task="接着干", continue_from_run_id="ok1"
                ),
                *[RunSpec(run_id=f"n{i}", role=f"N{i}", task=f"新活 {i}") for i in range(3)],
            ]
        )
        err = post_close_cold_open_error(_tool(), plan)
        assert err is not None
        assert "3 个既不续派、也不补缺口的冷开节点" in err
    finally:
        clear_active_coordination(_EXEC)


def test_post_close_gap_fill_still_capped():
    """补缺口那一堆的限流不放宽（与同图 replan 补跑闸同判定）。"""
    from agentcore.runtime.coordination.session import clear_active_coordination

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
    """三堆归属只看结构字段；continue 指向缺口算补跑、指向已成功算续派。"""
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="a", role="A", task="t", continue_from_run_id="ok1"),
            RunSpec(run_id="b", role="B", task="t", continue_from_run_id="gap1"),
            RunSpec(run_id="c", role="C", task="t", replaces_run_id="gap1"),
            RunSpec(run_id="d", role="D", task="t"),
        ]
    )
    completed = {
        "ok1": RunState(phase=RunPhase.COMPLETED, content="ok"),
        "gap1": RunState(phase=RunPhase.FAILED, error="e"),
    }
    shape = classify_batch(plan, completed)
    assert [n.run_id for n in shape.same_person] == ["a"]
    assert [n.run_id for n in shape.gap_fill] == ["b", "c"]
    assert [n.run_id for n in shape.cold] == ["d"]
    assert shape.is_pure_continuation is False


def test_format_continuation_candidates_skips_captain_and_task():
    from agentcore.runtime.delegate.team_continuation import format_continuation_candidates
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
    from agentcore.runtime.delegate.team_continuation import (
        ContinuationShape,
        cold_open_reject_message,
    )

    shape = ContinuationShape(cold=(object(), object(), object()))
    err = cold_open_reject_message(shape)
    assert "<近期团队图>" not in err
    assert "队员终态名册" in err
    with_c = cold_open_reject_message(
        shape, candidates="- run_id=w1; role=A; status=completed"
    )
    assert "可续候选" in with_c
    assert "run_id=w1" in with_c
    assert "<近期团队图>" not in with_c
