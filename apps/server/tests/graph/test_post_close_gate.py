"""收口后冷开整团重派硬闸（与同图 replan 补跑闸分轨）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentcore.runtime.graph.admit.post_close_gate import (
    EXECUTION_HARVEST_ORIGIN,
    POST_CLOSE_REJECT_COLD_OPEN,
    POST_CLOSE_REJECT_GAP_FILL,
    post_close_cold_open_error,
    post_close_reject,
)
from agentcore.runtime.runs.constants import MAX_GAP_FILL_ADDS
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunSpec


def _tool(*, origin: str = "", depth: int = 0) -> MagicMock:
    t = MagicMock()
    t._user_message_origin = origin
    t._depth = depth
    t._conversation_id = "conv-post-close"
    t._base_tool_context = SimpleNamespace(execution_id="exec-post-close")
    return t


def _substantial_unnamed(*, n: int = 3) -> RunPlan:
    return RunPlan(
        nodes=[
            RunSpec(run_id=f"n{i}", role=f"R{i}", task=f"task {i}") for i in range(n)
        ]
    )


def _named_replaces(gap_ids: list[str]) -> RunPlan:
    return RunPlan(
        nodes=[
            RunSpec(
                run_id=f"retry_{gid}",
                role=f"R_{gid}",
                task=f"retry {gid}",
                replaces_run_id=gid,
            )
            for gid in gap_ids
        ]
    )


def test_post_close_admits_one_next_segment_hanging_off_a_conclusion():
    err = post_close_cold_open_error(
        _tool(origin=EXECUTION_HARVEST_ORIGIN),
        RunPlan(
            nodes=[
                RunSpec(
                    run_id="next",
                    role="next",
                    task="接着做",
                    depends_on=["w1"],
                )
            ]
        ),
    )
    assert err is None


def test_post_close_rejects_unnamed_substantial_fanout():
    """三元组①：收口后无缺口/未点名大扇出 → 拒。"""
    reject = post_close_reject(
        _tool(origin=EXECUTION_HARVEST_ORIGIN),
        _substantial_unnamed(n=3),
    )
    assert reject is not None
    assert reject.kind == POST_CLOSE_REJECT_COLD_OPEN
    err = reject.message
    assert "太多" in err
    assert "graph" in err
    assert "continue_from_run_id" not in err
    assert "replaces_run_id" not in err
    assert "<近期团队图>" not in err
    assert "已有结论" not in err
    assert 'force=["post_close"]' not in err
    assert "force=true" not in err


def test_post_close_cold_open_reject_lists_prior_roster_candidates():
    """收口后冷开拒：附上轮 live_plan 的 run_id/role/status，不抄 task、不点名近期团队图。"""
    from agentcore.runtime.graph.session.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-post-close", total_workers=2)
    session.conversation_id = "conv-post-close"
    session.completed_run_ids = {"w1", "w2"}
    session.active = False
    session.live_plan = RunPlan(
        nodes=[
            RunSpec(run_id="w1", role="调研员", task="不要出现在拒文里的长任务"),
            RunSpec(run_id="w2", role="撰写员", task="另一条也不该出现"),
        ]
    )
    set_active_coordination(session)
    try:
        reject = post_close_reject(
            _tool(origin=EXECUTION_HARVEST_ORIGIN),
            _substantial_unnamed(n=3),
        )
        assert reject is not None
        err = reject.message
        assert "已有结论" in err
        assert "run_id=w1" in err
        assert "role=调研员" in err
        assert "run_id=w2" in err
        assert "不要出现在拒文里的长任务" not in err
        assert "<近期团队图>" not in err
        assert "n0" not in err  # 新冷开 plan 不进候选
    finally:
        clear_active_coordination("exec-post-close")


def test_post_close_allows_named_gap_fill_within_cap(monkeypatch):
    """三元组②：有缺口 + 点名补 ≤ MAX_GAP_FILL_ADDS → 放行。"""
    from agentcore.runtime.graph.session.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-post-close", total_workers=4)
    session.conversation_id = "conv-post-close"
    session.completed_run_ids = {"f1", "f2", "ok"}
    session.failed_run_ids = {"f1", "f2"}
    session.active = False
    set_active_coordination(session)
    try:
        gaps = ["f1", "f2"]
        assert len(gaps) <= MAX_GAP_FILL_ADDS
        err = post_close_cold_open_error(
            _tool(origin=EXECUTION_HARVEST_ORIGIN),
            _named_replaces(gaps),
        )
        assert err is None
    finally:
        clear_active_coordination("exec-post-close")


def test_post_close_rejects_named_over_cap(monkeypatch):
    """三元组③：点名补超过 min(|gaps|, MAX_GAP_FILL_ADDS) → 拒。"""
    from agentcore.runtime.graph.session.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )

    clear_active_coordination()
    n_gaps = MAX_GAP_FILL_ADDS + 1
    gap_ids = [f"f{i}" for i in range(1, n_gaps + 1)]
    session = CoordinationSession(execution_id="exec-post-close", total_workers=n_gaps)
    session.conversation_id = "conv-post-close"
    session.completed_run_ids = set(gap_ids)
    session.failed_run_ids = set(gap_ids)
    session.active = False
    set_active_coordination(session)
    try:
        reject = post_close_reject(
            _tool(origin=EXECUTION_HARVEST_ORIGIN),
            _named_replaces(gap_ids),
        )
        assert reject is not None
        assert reject.kind == POST_CLOSE_REJECT_GAP_FILL
        err = reject.message
        assert "补跑一次最多" in err
        assert str(MAX_GAP_FILL_ADDS) in err
    finally:
        clear_active_coordination("exec-post-close")


def test_non_harvest_human_first_delegate_not_blocked():
    """真人首派：非 harvest origin 不误伤。"""
    err = post_close_cold_open_error(
        _tool(origin=""),
        _substantial_unnamed(n=5),
    )
    assert err is None


def test_trivial_batch_not_gated():
    """非 substantial（≤2 且无依赖）收口后仍可冷开。"""
    plan = RunPlan(nodes=[RunSpec(run_id="a", role="A", task="polish")])
    err = post_close_cold_open_error(
        _tool(origin=EXECUTION_HARVEST_ORIGIN),
        plan,
    )
    assert err is None


@pytest.mark.asyncio
async def test_same_graph_replan_gap_fill_unchanged():
    """同图 replan 补跑闸不回归（仍复用 MAX_GAP_FILL_ADDS）。"""
    from agentcore.runtime.graph.drive.supervised import apply_replan
    from agentcore.runtime.runs.types import RunPhase, RunState

    class _FakeTools:
        def list_all(self):
            return []

    class _FakeDelegate:
        _tools = _FakeTools()
        _captain_run_id = "cap"
        _depth = 0
        _folder_id = "test_birth"

        def effective_default_target_folder_id(self) -> str | None:
            return None

    plan = RunPlan(
        nodes=[
            RunSpec(run_id="ok", role="A", task="done"),
            *[RunSpec(run_id=f"f{i}", role=f"B{i}", task=f"fail{i}") for i in range(1, 5)],
        ]
    )
    completed = {
        "ok": RunState(phase=RunPhase.COMPLETED, content="ok"),
        **{f"f{i}": RunState(phase=RunPhase.FAILED, error="e") for i in range(1, 5)},
    }
    too_many = [
        {"role": f"R{i}", "task": f"retry {i}", "replaces_run_id": f"f{i}"}
        for i in range(1, 5)
    ]
    err = await apply_replan(
        _FakeDelegate(), plan, completed, steers=[], adds=too_many
    )
    assert err
    assert any("补跑一次最多" in e for e in err)
    assert any(str(MAX_GAP_FILL_ADDS) in e for e in err)


@pytest.mark.asyncio
async def test_drive_coordinated_skips_post_close_reentry_on_empty_roster():
    """后台带 session：不重跑 post_close（空名册会把已准入的 replaces 误拒）。"""
    from structlog.testing import capture_logs

    from agentcore.runtime.graph.session.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.graph.drive.drive import drive_coordinated
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.runs import build_run_plan
    from tests.delegate.conftest import tool
    from tests.delegate.test_coordination_secondary_delegate import _SlowWorkers

    clear_active_coordination()
    session = CoordinationSession(execution_id="e", total_workers=1)
    session.conversation_id = "c"
    set_active_coordination(session)
    try:
        sink = EventSink()
        t = tool(_SlowWorkers(["retry-ok"], delay=0.01), sink=sink)
        t._user_message_origin = EXECUTION_HARVEST_ORIGIN
        plan, errors = build_run_plan(
            [{"role": "A", "task": "retry f1", "replaces_run_id": "f1"}]
        )
        assert not errors
        with capture_logs() as logs:
            result = await drive_coordinated(
                t,
                plan,
                execution_id="e",
                seed_completed=None,
                complexity_hint="standard",
                call_idx=0,
                session=session,
            )
        events = [e.get("event") for e in logs]
        assert "delegate.post_close_gap_fill_rejected" not in events
        assert result.success is True
        assert len(session.completed_run_ids) >= 1
    finally:
        clear_active_coordination("e")
