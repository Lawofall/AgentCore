"""同队续派一等入口 + 逐闸 force（审计 D3 / ORCH-A2）。

两条验收：
1. 批次收口后走续派 / 补缺口入口不再被冷开闸拒（此前 N>3 人的纯续派必被限流打回，
   模型只剩 force 可选）。
2. force 收敛为逐闸开关：点名一道不顺手开另一道；历史布尔一键全开退役；
   上一次 delegate 的 force 不再泄漏进 replan。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentcore.runtime.delegate.force_scopes import (
    EMPTY_FORCE_SCOPES,
    FORCE_GATES,
    GATE_ISOMORPHIC,
    GATE_POST_CLOSE,
    GATE_SEAT_OVERLAP,
    GATE_THRASH,
    ForceScopes,
    force_allows,
    parse_force_scopes,
)
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


def _tool(*, scopes: ForceScopes = EMPTY_FORCE_SCOPES) -> MagicMock:
    t = MagicMock()
    t._user_message_origin = EXECUTION_HARVEST_ORIGIN
    t._force_scopes = scopes
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
    于是超过 MAX_GAP_FILL_ADDS 人的团队根本无法整体续派，模型只剩 force 可用。
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


# ── 逐闸 force ───────────────────────────────────────────────────────────────


def test_parse_force_scopes_named_gates():
    scopes = parse_force_scopes([GATE_THRASH, GATE_POST_CLOSE])
    assert scopes.allows(GATE_THRASH)
    assert scopes.allows(GATE_POST_CLOSE)
    assert not scopes.allows(GATE_ISOMORPHIC)
    assert not scopes.allows(GATE_SEAT_OVERLAP)


def test_parse_force_scopes_ignores_unknown_and_empty():
    assert not bool(parse_force_scopes(["all", "", "nope"]))
    assert not bool(parse_force_scopes(None))
    assert not bool(parse_force_scopes({"gate": "thrash"}))
    # 单个闸名字符串也认（模型少写一层数组不至于变成全不放行）。
    assert parse_force_scopes(GATE_THRASH).allows(GATE_THRASH)


@pytest.mark.asyncio
async def test_delegate_force_scope_does_not_leak_into_replan():
    """ORCH-A2：上一次 delegate 的 force 不得被后续 replan 读到。"""
    from agentcore.runtime.coordination.session import clear_active_coordination
    from agentcore.runtime.events import EventSink
    from tests.delegate.conftest import ctx, tool
    from tests.delegate.test_coordination_secondary_delegate import _SlowWorkers

    clear_active_coordination()
    t = tool(_SlowWorkers(["ok"], delay=0.01), sink=EventSink())

    await t.execute(
        {
            "tasks": [{"role": "A", "task": "一件事"}],
            "coordinate": False,
            "force": [GATE_SEAT_OVERLAP],
        },
        ctx(),
    )
    assert force_allows(t, GATE_SEAT_OVERLAP)

    # replan 不带 force → 入口重解析成空集，拿不到上一次的放行。
    await t.replan({"stop": True})
    assert not force_allows(t, GATE_SEAT_OVERLAP)
    assert not any(force_allows(t, g) for g in FORCE_GATES)
    clear_active_coordination()


@pytest.mark.asyncio
async def test_delegate_force_scope_reset_per_call():
    """同一实例连续两次 delegate：第二次不带 force 就没有任何放行。"""
    from agentcore.runtime.coordination.session import clear_active_coordination
    from agentcore.runtime.events import EventSink
    from tests.delegate.conftest import ctx, tool
    from tests.delegate.test_coordination_secondary_delegate import _SlowWorkers

    clear_active_coordination()
    t = tool(_SlowWorkers(["ok", "ok"], delay=0.01), sink=EventSink())

    await t.execute(
        {
            "tasks": [{"role": "A", "task": "一件事"}],
            "coordinate": False,
            "force": [GATE_ISOMORPHIC],
        },
        ctx(),
    )
    assert force_allows(t, GATE_ISOMORPHIC)

    # 前奏硬拒（空 tasks）也必须先清掉上一次的 scope。
    rejected = await t.execute({"tasks": []}, ctx())
    assert rejected.success is False
    assert not force_allows(t, GATE_ISOMORPHIC)
    clear_active_coordination()
