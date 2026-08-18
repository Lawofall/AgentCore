"""TurnState projection + recover_turn + lease sweeper (crash recover).

Pins the single recover primitive: journal → TurnState → seed WaveScheduler
(completed skipped) for crash redrive; resume kinds route through the same path.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink
from agentcore.runtime.recover import recover_turn
from agentcore.runtime.runs import RunPlan, RunSpec
from agentcore.runtime.runs.serialize import plan_snapshot_fact, plan_to_json, run_final_fact
from agentcore.runtime.runs.types import RunPhase, RunState
from agentcore.runtime.turn.state import TurnState
from agentcore.tools.protocol import ToolResult


def _plan_two_nodes() -> RunPlan:
    return RunPlan(
        nodes=[
            RunSpec(run_id="w1", task="done", role="研究员"),
            RunSpec(run_id="w2", task="pending", role="写手"),
        ]
    )


def _partial_journal() -> list[dict]:
    """Plan + one completed worker + run_plan execution_id (no turn_end)."""
    plan = _plan_two_nodes()
    completed = RunState(phase=RunPhase.COMPLETED, content="ok")
    snap = plan_snapshot_fact(plan)
    final = run_final_fact("w1", completed)
    return [
        {
            "kind": "run_plan",
            "payload": {"execution_id": "exec-crash-1"},
            "ts": "t0",
            "seq": 0,
        },
        {**snap.entry(), "seq": 1},
        {**final.entry(), "seq": 2},
    ]


def test_turn_state_from_journal_projects_plan_completed_execution_id():
    entries = _partial_journal()
    state = TurnState.from_journal(entries)
    assert state.execution_id == "exec-crash-1"
    assert state.plan is not None
    assert [n.run_id for n in state.plan.nodes] == ["w1", "w2"]
    assert set(state.completed) == {"w1"}
    assert state.completed["w1"].phase is RunPhase.COMPLETED
    assert state.unfinished_run_ids == ["w2"]


def test_turn_state_upto_seq_time_travel():
    entries = _partial_journal()
    # Before the completed fact — no seed yet, both unfinished.
    early = TurnState.from_journal(entries, upto_seq=1)
    assert early.completed == {}
    assert early.plan is not None
    assert early.unfinished_run_ids == ["w1", "w2"]


async def test_recover_turn_crash_redrives_with_seed_completed():
    state = TurnState.from_journal(_partial_journal())
    sink = EventSink()
    seen: dict = {}

    async def _resume_plan(plan, seed_completed, **kwargs):
        seen["plan_ids"] = [n.run_id for n in plan.nodes]
        seen["seed"] = set(seed_completed)
        seen["decision"] = kwargs.get("decision")
        seen["execution_id"] = kwargs.get("execution_id")
        seen["coordinate"] = kwargs.get("coordinate")
        seen["coordination"] = kwargs.get("coordination")
        return ToolResult(tool_call_id="t1", success=True, output="redriven")

    delegate = MagicMock()
    delegate.resume_plan = _resume_plan

    settled = await recover_turn(
        state=state,
        sink=sink,
        delegate_tool=delegate,
        execution_id="fresh-should-not-win",
    )
    assert settled.output == "redriven"
    assert settled.terminal_text is None
    assert seen["seed"] == {"w1"}
    assert seen["plan_ids"] == ["w1", "w2"]
    assert seen["decision"] is CheckpointDecision.CONTINUE
    assert seen["execution_id"] == "exec-crash-1"
    assert seen["coordinate"] is True
    assert seen["coordination"] == "wall"


async def test_recover_turn_resume_plan_review_routes_through_same_primitive():
    from agentcore.runtime.suspension import PlanReviewSuspension

    state = TurnState.from_journal(_partial_journal())
    sink = EventSink()
    seen: dict = {}

    async def _resume_plan(plan, seed_completed, **kwargs):
        seen["seed"] = set(seed_completed)
        seen["decision"] = kwargs.get("decision")
        seen["ceo_review"] = kwargs.get("ceo_review")
        return ToolResult(tool_call_id="t1", success=True, output="resumed")

    delegate = MagicMock()
    delegate.resume_plan = _resume_plan

    review = {
        "conclusion": "可过",
        "risks": ["r"],
        "suggestions": ["s"],
        "source": "llm",
    }
    suspension = PlanReviewSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="cp1",
        tool_call_id="tc1",
        user_message="task",
        base_system_prompt="sys",
        journal_entries=_partial_journal(),
        plan=state.plan or _plan_two_nodes(),
        completed=dict(state.completed),
        steps=[{"run_id": "w1", "role": "研究员", "summary": "…"}],
        pending=[{"run_id": "w2", "role": "写手"}],
        ceo_review=review,
    )

    settled = await recover_turn(
        state=state,
        sink=sink,
        delegate_tool=delegate,
        execution_id="x",
        suspension=suspension,
        decision=CheckpointDecision.CONTINUE,
        note="",
    )
    assert settled.output == "resumed"
    assert seen["seed"] == {"w1"}
    assert seen["decision"] is CheckpointDecision.CONTINUE
    assert seen["ceo_review"] == review


async def test_recover_turn_plan_review_forwards_batch_coordination():
    """plan_review 帧回灌批次协作参数：恢复用全新 DelegateTool（_coordination 缺省
    none），不转发则复核后续波次的 worker 被剥便签三件套。"""
    from agentcore.runtime.suspension import PlanReviewSuspension

    state = TurnState.from_journal(_partial_journal())
    sink = EventSink()
    seen: dict = {}

    async def _resume_plan(plan, seed_completed, **kwargs):
        seen["coordination"] = kwargs.get("coordination")
        seen["team_brief"] = kwargs.get("team_brief")
        return ToolResult(tool_call_id="t1", success=True, output="resumed")

    delegate = MagicMock()
    delegate.resume_plan = _resume_plan

    suspension = PlanReviewSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="cp1",
        tool_call_id="tc1",
        user_message="task",
        base_system_prompt="sys",
        journal_entries=_partial_journal(),
        plan=state.plan or _plan_two_nodes(),
        completed=dict(state.completed),
        steps=[{"run_id": "w1", "role": "研究员", "summary": "…"}],
        coordination="wall",
        team_brief="口径按 v2",
    )
    await recover_turn(
        state=state,
        sink=sink,
        delegate_tool=delegate,
        execution_id="x",
        suspension=suspension,
        decision=CheckpointDecision.CONTINUE,
        note="",
    )
    assert seen["coordination"] == "wall"
    assert seen["team_brief"] == "口径按 v2"


async def test_recover_turn_team_preview_forwards_batch_coordination():
    """开工卡帧回灌批次协作参数（含 seed_notes 补种）——2026-07-20 P2 手驱真跑抓获：
    不转发则 wall 批恢复后降级 none，worker 无便签三件套、CEO 预贴便签丢失。"""
    from agentcore.runtime.suspension import TeamPreviewSuspension

    state = TurnState.from_journal(_partial_journal())
    sink = EventSink()
    seen: dict = {}

    async def _resume_plan(plan, seed_completed, **kwargs):
        seen["coordination"] = kwargs.get("coordination")
        seen["team_brief"] = kwargs.get("team_brief")
        seen["seed_notes"] = kwargs.get("seed_notes")
        seen["coordinate"] = kwargs.get("coordinate")
        return ToolResult(tool_call_id="t1", success=True, output="kicked")

    delegate = MagicMock()
    delegate.resume_plan = _resume_plan

    suspension = TeamPreviewSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="cp1",
        tool_call_id="tc1",
        user_message="task",
        base_system_prompt="sys",
        journal_entries=_partial_journal(),
        plan=state.plan or _plan_two_nodes(),
        workers=[{"run_id": "w1", "role": "研究员", "task": "调研"}],
        coordination="wall",
        team_brief="统一用中文",
        seed_notes=[{"kind": "heads_up", "text": "接口用 REST"}],
    )
    settled = await recover_turn(
        state=state,
        sink=sink,
        delegate_tool=delegate,
        execution_id="x",
        suspension=suspension,
        decision=CheckpointDecision.CONTINUE,
        note="",
    )
    assert settled.output == "kicked"
    assert seen["coordination"] == "wall"
    assert seen["team_brief"] == "统一用中文"
    assert seen["seed_notes"] == [{"kind": "heads_up", "text": "接口用 REST"}]
    assert seen["coordinate"] is True


async def test_recover_turn_team_preview_suspend_preserves_effect():
    """resume_plan mid-settle SUSPEND must surface on SettledSuspension (cold PAUSED)."""
    from agentcore.core.types import ToolEffect
    from agentcore.runtime.suspension import TeamPreviewSuspension

    state = TurnState.from_journal(_partial_journal())
    sink = EventSink()

    async def _resume_plan(plan, seed_completed, **kwargs):
        return ToolResult(
            tool_call_id="",
            success=True,
            output="",
            effect=ToolEffect.SUSPEND,
        )

    delegate = MagicMock()
    delegate.resume_plan = _resume_plan

    suspension = TeamPreviewSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="cp1",
        tool_call_id="tc1",
        user_message="task",
        base_system_prompt="sys",
        journal_entries=_partial_journal(),
        plan=state.plan or _plan_two_nodes(),
        workers=[{"run_id": "w1", "role": "研究员", "task": "调研"}],
    )
    settled = await recover_turn(
        state=state,
        sink=sink,
        delegate_tool=delegate,
        execution_id="x",
        suspension=suspension,
        decision=CheckpointDecision.CONTINUE,
        note="",
    )
    assert settled.effect is ToolEffect.SUSPEND
    assert settled.terminal_text is None


async def test_recover_turn_plan_review_suspend_preserves_effect():
    """plan_review settle SUSPEND is the same outer contract as team_preview."""
    from agentcore.core.types import ToolEffect
    from agentcore.runtime.suspension import PlanReviewSuspension

    state = TurnState.from_journal(_partial_journal())
    sink = EventSink()

    async def _resume_plan(plan, seed_completed, **kwargs):
        return ToolResult(
            tool_call_id="",
            success=True,
            output="",
            effect=ToolEffect.SUSPEND,
        )

    delegate = MagicMock()
    delegate.resume_plan = _resume_plan

    suspension = PlanReviewSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="cp1",
        tool_call_id="tc1",
        user_message="task",
        base_system_prompt="sys",
        journal_entries=_partial_journal(),
        plan=state.plan or _plan_two_nodes(),
        completed=dict(state.completed),
        steps=[{"run_id": "w1", "role": "研究员", "summary": "…"}],
    )
    settled = await recover_turn(
        state=state,
        sink=sink,
        delegate_tool=delegate,
        execution_id="x",
        suspension=suspension,
        decision=CheckpointDecision.CONTINUE,
        note="",
    )
    assert settled.effect is ToolEffect.SUSPEND
    assert settled.terminal_text is None


async def test_sweeper_claims_expired_lease_and_invokes_recover(monkeypatch):
    """Lease + partial journal + no live process → sweeper starts recover with unfinished DAG."""
    from datetime import UTC, datetime, timedelta

    from agentcore.runtime.leases import sweeper as sweeper_mod

    message_id = "11111111-1111-1111-1111-111111111111"
    conversation_id = "22222222-2222-2222-2222-222222222222"
    user_id = "33333333-3333-3333-3333-333333333333"
    entries = _partial_journal()
    expired_row = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        owner_id="dead-owner",
        phase="running",
        meta={},
        heartbeat_at=datetime.now(UTC) - timedelta(hours=1),
    )
    claimed_row = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        owner_id="new-owner",
        phase="recovering",
        meta={},
        heartbeat_at=datetime.now(UTC),
    )

    recover_calls: list = []

    async def _fake_recover(lease, state):
        recover_calls.append((lease.message_id, set(state.completed), state.unfinished_run_ids))

    class _FakeLeaseRepo:
        def __init__(self, _session):
            pass

        async def list_expired(self, *, before, limit):
            return [expired_row]

        async def claim_expired(self, mid, *, new_owner_id, before, phase="recovering"):
            assert mid == message_id
            return claimed_row

        async def bump_recover_attempts(self, mid, *, owner_id):
            meta = dict(claimed_row.meta) if isinstance(claimed_row.meta, dict) else {}
            attempts = int(meta.get("recover_attempts") or 0) + 1
            meta["recover_attempts"] = attempts
            claimed_row.meta = meta
            return attempts

        async def release(self, mid, *, owner_id=None):
            pass

    class _FakePausedRepo:
        def __init__(self, _session):
            pass

        async def get(self, mid):
            return None

    class _FakeJournalRepo:
        def __init__(self, _session):
            pass

        async def load_owned(self, turn_id, conversation_id):
            return entries

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(sweeper_mod, "TurnLeaseRepository", _FakeLeaseRepo)
    monkeypatch.setattr(sweeper_mod, "PausedTurnRepository", _FakePausedRepo)
    monkeypatch.setattr(sweeper_mod, "TurnJournalRepository", _FakeJournalRepo)
    monkeypatch.setattr(sweeper_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(sweeper_mod.settings, "turn_lease_enabled", True)
    monkeypatch.setattr(
        "agentcore.runtime.recover.recover_expired_lease",
        _fake_recover,
    )
    # Reset process-local recover dedupe between tests.
    sweeper_mod._recovering_message_ids.clear()
    sweeper_mod._recover_tasks.clear()

    pending: list = []

    def _capture_task(coro, name=None):
        pending.append(coro)
        m = MagicMock()
        m.add_done_callback = MagicMock()
        return m

    monkeypatch.setattr(sweeper_mod.asyncio, "create_task", _capture_task)

    started = await sweeper_mod.run_turn_lease_sweep()
    assert started == 1
    assert len(pending) == 1
    await pending[0]
    assert len(recover_calls) == 1
    mid, completed, unfinished = recover_calls[0]
    assert mid == message_id
    assert completed == {"w1"}
    assert unfinished == ["w2"]
    assert claimed_row.meta.get("recover_attempts") == 1


def test_plan_snapshot_round_trip_via_turn_state():
    plan = _plan_two_nodes()
    entries = [{**plan_snapshot_fact(plan).entry()}]
    state = TurnState.from_journal(entries)
    assert plan_to_json(state.plan) == plan_to_json(plan)


async def test_sweeper_claims_orphaned_lease_with_unfinished_dag(monkeypatch):
    """Cancel-path orphan mark (fresh heartbeat) is still reclaimable immediately."""
    from datetime import UTC, datetime

    from agentcore.runtime.leases import sweeper as sweeper_mod

    message_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    conversation_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    user_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    entries = _partial_journal()
    orphaned_row = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        owner_id="dead-owner",
        phase="orphaned",
        meta={"trace_id": "tr-orphan"},
        heartbeat_at=datetime.now(UTC),  # not TTL-stale — phase drives reclaim
    )
    claimed_row = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        owner_id="new-owner",
        phase="recovering",
        meta={"trace_id": "tr-orphan"},
        heartbeat_at=datetime.now(UTC),
    )
    recover_calls: list = []

    async def _fake_recover(lease, state):
        recover_calls.append(lease.message_id)

    class _FakeLeaseRepo:
        def __init__(self, _session):
            pass

        async def list_expired(self, *, before, limit):
            return [orphaned_row]

        async def claim_expired(self, mid, *, new_owner_id, before, phase="recovering"):
            return claimed_row

        async def bump_recover_attempts(self, mid, *, owner_id):
            meta = dict(claimed_row.meta) if isinstance(claimed_row.meta, dict) else {}
            attempts = int(meta.get("recover_attempts") or 0) + 1
            meta["recover_attempts"] = attempts
            claimed_row.meta = meta
            return attempts

        async def release(self, mid, *, owner_id=None):
            pass

    class _FakePausedRepo:
        def __init__(self, _session):
            pass

        async def get(self, mid):
            return None

    class _FakeJournalRepo:
        def __init__(self, _session):
            pass

        async def load_owned(self, turn_id, conversation_id):
            return entries

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(sweeper_mod, "TurnLeaseRepository", _FakeLeaseRepo)
    monkeypatch.setattr(sweeper_mod, "PausedTurnRepository", _FakePausedRepo)
    monkeypatch.setattr(sweeper_mod, "TurnJournalRepository", _FakeJournalRepo)
    monkeypatch.setattr(sweeper_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(sweeper_mod.settings, "turn_lease_enabled", True)
    monkeypatch.setattr(
        "agentcore.runtime.recover.recover_expired_lease",
        _fake_recover,
    )
    sweeper_mod._recovering_message_ids.clear()
    sweeper_mod._recover_tasks.clear()
    pending: list = []

    def _capture_task(coro, name=None):
        pending.append(coro)
        m = MagicMock()
        m.add_done_callback = MagicMock()
        return m

    monkeypatch.setattr(sweeper_mod.asyncio, "create_task", _capture_task)

    started = await sweeper_mod.run_turn_lease_sweep()
    assert started == 1
    await pending[0]
    assert recover_calls == [message_id]


async def test_build_crash_delegate_tool_warns_when_unwired(monkeypatch):
    """未接线必须 warning，禁止静默 info 跳过。"""
    from agentcore.runtime import recover_hooks as hooks
    from tests.conftest import LogSpy

    hooks.set_crash_delegate_factory(None)
    spy = LogSpy()

    def _info_forbidden(event, *args, **kwargs):
        raise AssertionError(f"unwired path must not log at info: {event}")

    spy.info = _info_forbidden  # type: ignore[method-assign]
    monkeypatch.setattr(hooks, "logger", spy)
    lease = SimpleNamespace(
        message_id="m-unwired",
        conversation_id="c-unwired",
    )
    state = TurnState.from_journal(_partial_journal())
    tool = await hooks.build_crash_delegate_tool(lease, state, sink=EventSink())
    assert tool is None
    kw = spy.get("recover.crash_delegate_unwired")
    assert kw["message_id"] == "m-unwired"
    assert kw["unfinished"] == 1
    assert "set_crash_delegate_factory" in kw["hint"]


def _patch_recover_lease_heartbeat(monkeypatch) -> list[dict]:
    """Stub recovering heartbeat so unit tests do not touch the lease repo."""
    hb_calls: list[dict] = []

    async def _fake_hb(message_id, *, owner_id=None, phase=None):
        hb_calls.append({"message_id": message_id, "phase": phase})
        return True

    async def _fake_hb_loop(message_id, *, owner_id, interval_seconds, stop, phase="running"):
        # Exit immediately when stop is set by recover's finally.
        await stop.wait()

    monkeypatch.setattr(
        "agentcore.runtime.leases.service.heartbeat_turn_lease",
        _fake_hb,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.service.lease_heartbeat_loop",
        _fake_hb_loop,
    )
    return hb_calls


async def test_recover_expired_lease_degrades_to_interrupted_when_unwired(monkeypatch):
    """Production crash-delegate factory is unwired → honest interrupted, not silent drop."""
    from agentcore.runtime.events import FinishReason
    from agentcore.runtime.recover import recover_expired_lease
    from agentcore.runtime.recover_hooks import set_crash_delegate_factory

    message_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    conversation_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    lease = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        meta={"trace_id": "tr-d", "recover_attempts": 1},
        trace_id=None,
    )
    state = TurnState.from_journal(_partial_journal())
    salvage_calls: list[dict] = []
    released: list[str] = []
    hb_calls = _patch_recover_lease_heartbeat(monkeypatch)

    async def _fake_orphan(**kwargs):
        return None

    async def _fake_salvage(**kwargs):
        salvage_calls.append(kwargs)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    set_crash_delegate_factory(None)
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.orphan_turn_before_recover",
        _fake_orphan,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.sweeper.salvage_interrupted_turn",
        _fake_salvage,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.service.release_turn_lease",
        _fake_release,
    )

    await recover_expired_lease(lease, state)
    assert len(salvage_calls) == 1
    assert salvage_calls[0]["message_id"] == message_id
    assert salvage_calls[0]["reason"] == "redrive_failed"
    assert released == [message_id]
    assert any(c.get("phase") == "recovering" for c in hb_calls)
    # finish_reason constant still the interrupted terminal (salvage path contract)
    assert FinishReason.INTERRUPTED.value == "interrupted"


async def test_recover_expired_lease_redrives_when_factory_wired(monkeypatch):
    """Factory returns a DelegateTool → recover_turn resume_plan runs (true redrive)."""
    from agentcore.runtime.recover import recover_expired_lease
    from agentcore.runtime.recover_hooks import set_crash_delegate_factory

    message_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    conversation_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    lease = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        meta={"trace_id": "tr-r", "recover_attempts": 1},
        trace_id=None,
    )
    state = TurnState.from_journal(_partial_journal())
    resume_calls: list[dict] = []
    salvage_calls: list[dict] = []
    released: list[str] = []
    _patch_recover_lease_heartbeat(monkeypatch)

    async def _fake_orphan(**kwargs):
        return None

    async def _resume_plan(plan, seed_completed, **kwargs):
        resume_calls.append(
            {
                "plan_ids": [n.run_id for n in plan.nodes],
                "seed": set(seed_completed),
                "execution_id": kwargs.get("execution_id"),
                "coordinate": kwargs.get("coordinate"),
                "coordination": kwargs.get("coordination"),
            }
        )
        return ToolResult(tool_call_id="t1", success=True, output="redriven")

    async def _factory(lease_arg, state_arg, *, sink):
        assert lease_arg.message_id == message_id
        assert state_arg.unfinished_run_ids == ["w2"]
        tool = MagicMock()
        tool.resume_plan = _resume_plan
        return tool

    async def _fake_salvage(**kwargs):
        salvage_calls.append(kwargs)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    set_crash_delegate_factory(_factory)
    try:
        monkeypatch.setattr(
            "agentcore.runtime.interaction_orphan.orphan_turn_before_recover",
            _fake_orphan,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.sweeper.salvage_interrupted_turn",
            _fake_salvage,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.service.release_turn_lease",
            _fake_release,
        )
        await recover_expired_lease(lease, state)
    finally:
        set_crash_delegate_factory(None)

    assert len(resume_calls) == 1
    assert resume_calls[0]["seed"] == {"w1"}
    assert resume_calls[0]["plan_ids"] == ["w1", "w2"]
    assert resume_calls[0]["execution_id"] == "exec-crash-1"
    assert resume_calls[0]["coordinate"] is True
    assert resume_calls[0]["coordination"] == "wall"
    assert salvage_calls == []
    assert released == [message_id]


async def test_recover_expired_lease_redrive_facts_land_on_original_turn(monkeypatch):
    """重驱期间产生的事实必须落进 ORIGINAL 回合的 journal（唯一事实源 · 重连即回放）。

    裸 ``EventSink()`` + 未绑 journal writer 时，sink 三条落库回退路径全落空：恢复期
    一条事实都不进 journal，协作图永远停在崩溃那一刻、产出只能另起一条消息。
    """
    from agentcore.runtime.events import run_completed, run_started
    from agentcore.runtime.facts import current_fact_log
    from agentcore.runtime.journal.writer import current_journal_writer
    from agentcore.runtime.recover import recover_expired_lease
    from agentcore.runtime.recover_hooks import set_crash_delegate_factory

    message_id = "facts000-0000-0000-0000-000000000001"
    conversation_id = "facts000-0000-0000-0000-000000000002"
    lease = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        meta={"trace_id": "tr-facts", "recover_attempts": 1},
        trace_id=None,
    )
    state = TurnState.from_journal(_partial_journal())
    appended: list[dict] = []
    released: list[str] = []
    observed: dict = {}
    _patch_recover_lease_heartbeat(monkeypatch)

    class _FakeStore:
        async def append_journal(
            self, *, turn_id, seq, conversation_id, trace_id, entry
        ):
            appended.append(
                {
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "trace_id": trace_id,
                    "kind": entry.get("kind"),
                }
            )
            return len(appended) - 1

        async def upsert_stream_segments(self, *, turn_id, segments):
            return None

    monkeypatch.setattr(
        "agentcore.conversation.store.get_conversation_store", lambda: _FakeStore()
    )

    async def _fake_orphan(**kwargs):
        return None

    async def _factory(lease_arg, state_arg, *, sink):
        observed["sink"] = sink

        async def _resume_plan(plan, seed_completed, **kwargs):
            writer = current_journal_writer.get()
            observed["writer_turn_id"] = getattr(writer, "turn_id", None)
            observed["writer_conversation_id"] = getattr(writer, "conversation_id", None)
            observed["fact_log_bound"] = current_fact_log.get() is not None
            sink.emit(run_started("w2", "agent-w2"))
            sink.emit(
                run_completed("w2", "agent-w2", output_summary="done", duration_ms=1)
            )
            return ToolResult(tool_call_id="t1", success=True, output="redriven")

        tool = MagicMock()
        tool.resume_plan = _resume_plan
        return tool

    async def _fake_salvage(**kwargs):
        raise AssertionError("successful redrive must not salvage")

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    set_crash_delegate_factory(_factory)
    try:
        monkeypatch.setattr(
            "agentcore.runtime.interaction_orphan.orphan_turn_before_recover",
            _fake_orphan,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.sweeper.salvage_interrupted_turn",
            _fake_salvage,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.service.release_turn_lease",
            _fake_release,
        )
        await recover_expired_lease(lease, state)
    finally:
        set_crash_delegate_factory(None)

    # The redrive sink carries the crashed turn's identity (not an anonymous one).
    assert observed["sink"].message_id == message_id
    assert observed["sink"].conversation_id == conversation_id
    assert observed["writer_turn_id"] == message_id
    assert observed["writer_conversation_id"] == conversation_id
    assert observed["fact_log_bound"] is True

    kinds = [row["kind"] for row in appended]
    assert "run_started" in kinds
    assert "run_completed" in kinds
    assert {row["turn_id"] for row in appended} == {message_id}
    assert {row["conversation_id"] for row in appended} == {conversation_id}
    assert {row["trace_id"] for row in appended} == {"tr-facts"}
    assert released == [message_id]
    # Turn-scoped bindings must not leak past the recovering task.
    assert current_journal_writer.get() is None
    assert current_fact_log.get() is None


def _patch_recovered_badge_store(monkeypatch) -> list[dict]:
    """Capture the 「曾中断恢复」 assistant upsert instead of touching the DB."""
    writes: list[dict] = []

    class _Repo:
        def __init__(self, _db):
            pass

        async def upsert_assistant(self, **kwargs):
            writes.append(kwargs)
            return SimpleNamespace(id=kwargs.get("message_id"))

    class _Db:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("agentcore.db.base.async_session_factory", lambda: _Db())
    monkeypatch.setattr("agentcore.db.repositories.MessageRepository", _Repo)
    return writes


async def test_recover_expired_lease_marks_recovered_and_binds_closing_to_turn(
    monkeypatch,
):
    """D5 归属原回合：重驱给原消息盖「曾中断恢复」，并把收口指向同一条消息。

    没有 ``recovered_turn_id``，harvest 会另起一条与原回合无关的助手消息；不在
    teardown 交还 ``turn_attached``，收口还得空等 5 秒 stale-attach 才敢开工。
    """
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        active_coordination,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.recover import recover_expired_lease
    from agentcore.runtime.recover_hooks import set_crash_delegate_factory

    message_id = "recov000-0000-0000-0000-000000000001"
    conversation_id = "recov000-0000-0000-0000-000000000002"
    lease = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        meta={"trace_id": "tr-recov", "recover_attempts": 1},
        trace_id=None,
    )
    state = TurnState.from_journal(_partial_journal())
    released: list[str] = []
    _patch_recover_lease_heartbeat(monkeypatch)
    badge_writes = _patch_recovered_badge_store(monkeypatch)
    clear_active_coordination()

    async def _fake_orphan(**kwargs):
        return None

    async def _arm(plan, seed_completed, **kwargs):
        session = CoordinationSession(
            execution_id=kwargs.get("execution_id") or "exec-crash-1",
            total_workers=2,
            conversation_id=conversation_id,
        )
        set_active_coordination(session)
        # Recover binds the turn synchronously after arm — never after the drive.
        assert session.recovered_turn_id == ""
        return ToolResult(tool_call_id="t1", success=True, output="armed")

    async def _factory(lease_arg, state_arg, *, sink):
        tool = MagicMock()
        tool.resume_plan = _arm
        return tool

    async def _fake_salvage(**kwargs):
        raise AssertionError("successful redrive must not salvage")

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    set_crash_delegate_factory(_factory)
    try:
        monkeypatch.setattr(
            "agentcore.runtime.interaction_orphan.orphan_turn_before_recover",
            _fake_orphan,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.sweeper.salvage_interrupted_turn",
            _fake_salvage,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.service.release_turn_lease",
            _fake_release,
        )
        await recover_expired_lease(lease, state)
        session = active_coordination("exec-crash-1")
    finally:
        set_crash_delegate_factory(None)
        clear_active_coordination()

    assert len(badge_writes) == 1
    badge = badge_writes[0]
    assert badge["message_id"] == message_id
    assert badge["conversation_id"] == conversation_id
    assert badge["metadata"] == {"recovered": True}
    assert badge["merge"] is True
    # Empty body: the merge must not clobber whatever the crashed turn streamed.
    assert badge["content"] == ""

    assert session is not None
    assert session.recovered_turn_id == message_id
    # Ownership handed back only after the lease is gone, so the closing turn can
    # re-acquire it instead of racing recover's release.
    assert session.turn_attached is False
    assert released == [message_id]


async def test_recover_expired_lease_salvages_when_rebuild_fails(monkeypatch):
    """Factory rebuild returns None → existing salvage + lease release (no extra fallback)."""
    from agentcore.runtime.recover import recover_expired_lease
    from agentcore.runtime.recover_hooks import set_crash_delegate_factory

    message_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    conversation_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    lease = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        meta={"trace_id": "tr-f", "recover_attempts": 1},
        trace_id=None,
    )
    state = TurnState.from_journal(_partial_journal())
    salvage_calls: list[dict] = []
    released: list[str] = []
    _patch_recover_lease_heartbeat(monkeypatch)

    async def _fake_orphan(**kwargs):
        return None

    async def _factory_fail(lease_arg, state_arg, *, sink):
        return None

    async def _fake_salvage(**kwargs):
        salvage_calls.append(kwargs)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    set_crash_delegate_factory(_factory_fail)
    try:
        monkeypatch.setattr(
            "agentcore.runtime.interaction_orphan.orphan_turn_before_recover",
            _fake_orphan,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.sweeper.salvage_interrupted_turn",
            _fake_salvage,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.service.release_turn_lease",
            _fake_release,
        )
        await recover_expired_lease(lease, state)
    finally:
        set_crash_delegate_factory(None)

    assert len(salvage_calls) == 1
    assert salvage_calls[0]["message_id"] == message_id
    assert salvage_calls[0]["reason"] == "redrive_failed"
    assert released == [message_id]


async def test_recover_expired_lease_timeout_salvages(monkeypatch):
    """Hung arm (recover_turn) past recover timeout → salvage interrupted + release."""
    import asyncio

    from agentcore.config import settings
    from agentcore.runtime.recover import recover_expired_lease
    from agentcore.runtime.recover_hooks import set_crash_delegate_factory

    monkeypatch.setattr(settings, "turn_lease_recover_timeout_seconds", 0.05)
    message_id = "timeout00-0000-0000-0000-000000000001"
    lease = SimpleNamespace(
        message_id=message_id,
        conversation_id="timeout00-0000-0000-0000-000000000002",
        user_id="u1",
        meta={"trace_id": "tr-t", "recover_attempts": 1},
        trace_id=None,
    )
    state = TurnState.from_journal(_partial_journal())
    salvage_calls: list[dict] = []
    released: list[str] = []
    _patch_recover_lease_heartbeat(monkeypatch)

    async def _fake_orphan(**kwargs):
        return None

    async def _hang_resume(plan, seed_completed, **kwargs):
        await asyncio.sleep(10)
        return ToolResult(tool_call_id="t1", success=True, output="late")

    async def _factory(lease_arg, state_arg, *, sink):
        tool = MagicMock()
        tool.resume_plan = _hang_resume
        return tool

    async def _fake_salvage(**kwargs):
        salvage_calls.append(kwargs)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    set_crash_delegate_factory(_factory)
    try:
        monkeypatch.setattr(
            "agentcore.runtime.interaction_orphan.orphan_turn_before_recover",
            _fake_orphan,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.sweeper.salvage_interrupted_turn",
            _fake_salvage,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.service.release_turn_lease",
            _fake_release,
        )
        await recover_expired_lease(lease, state)
    finally:
        set_crash_delegate_factory(None)

    assert len(salvage_calls) == 1
    assert salvage_calls[0]["reason"] == "redrive_failed"
    assert released == [message_id]


async def test_recover_expired_lease_timeout_cancels_drive(monkeypatch):
    """Arm timeout after drive started → cancel_coordination stops drive, then salvage."""
    import asyncio

    from agentcore.config import settings
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.coordination.session import (
        cancel_coordination_on_user_stop as real_cancel,
    )
    from agentcore.runtime.recover import recover_expired_lease
    from agentcore.runtime.recover_hooks import set_crash_delegate_factory

    monkeypatch.setattr(settings, "turn_lease_recover_timeout_seconds", 0.05)
    message_id = "timeout01-0000-0000-0000-000000000001"
    conversation_id = "timeout01-0000-0000-0000-000000000002"
    lease = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        meta={"trace_id": "tr-td", "recover_attempts": 1},
        trace_id=None,
    )
    state = TurnState.from_journal(_partial_journal())
    salvage_calls: list[dict] = []
    released: list[str] = []
    cancel_calls: list[dict] = []
    drive_task_ref: list = []
    _patch_recover_lease_heartbeat(monkeypatch)
    clear_active_coordination()

    async def _fake_orphan(**kwargs):
        return None

    async def _arm_then_hang(plan, seed_completed, **kwargs):
        session = CoordinationSession(
            execution_id="exec-crash-1",
            total_workers=2,
            conversation_id=conversation_id,
        )

        async def _hang() -> None:
            await asyncio.Event().wait()

        session.drive_task = asyncio.create_task(_hang())
        drive_task_ref.append(session.drive_task)
        set_active_coordination(session)
        await asyncio.sleep(10)
        return ToolResult(tool_call_id="t1", success=True, output="late")

    async def _factory(lease_arg, state_arg, *, sink):
        tool = MagicMock()
        tool.resume_plan = _arm_then_hang
        return tool

    async def _fake_salvage(**kwargs):
        salvage_calls.append(kwargs)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    def _spy_cancel(conversation_id=None, *, execution_id=None):
        cancel_calls.append(
            {"conversation_id": conversation_id, "execution_id": execution_id}
        )
        return real_cancel(conversation_id, execution_id=execution_id)

    set_crash_delegate_factory(_factory)
    try:
        monkeypatch.setattr(
            "agentcore.runtime.interaction_orphan.orphan_turn_before_recover",
            _fake_orphan,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.sweeper.salvage_interrupted_turn",
            _fake_salvage,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.service.release_turn_lease",
            _fake_release,
        )
        monkeypatch.setattr(
            "agentcore.runtime.coordination.session.cancel_coordination_on_user_stop",
            _spy_cancel,
        )
        await recover_expired_lease(lease, state)
    finally:
        set_crash_delegate_factory(None)
        clear_active_coordination()

    assert any(c.get("execution_id") == "exec-crash-1" for c in cancel_calls)
    assert len(salvage_calls) == 1
    assert salvage_calls[0]["reason"] == "redrive_failed"
    assert released == [message_id]
    assert drive_task_ref
    assert drive_task_ref[0].cancelled() or drive_task_ref[0].done()


async def test_recover_expired_lease_drive_outlives_timeout_succeeds(monkeypatch):
    """Drive longer than arm timeout still settles — no false timeout salvage."""
    import asyncio

    from agentcore.config import settings
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        clear_active_coordination,
        set_active_coordination,
    )
    from agentcore.runtime.recover import recover_expired_lease
    from agentcore.runtime.recover_hooks import set_crash_delegate_factory

    monkeypatch.setattr(settings, "turn_lease_recover_timeout_seconds", 0.05)
    message_id = "timeout02-0000-0000-0000-000000000001"
    conversation_id = "timeout02-0000-0000-0000-000000000002"
    lease = SimpleNamespace(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        meta={"trace_id": "tr-long", "recover_attempts": 1},
        trace_id=None,
    )
    state = TurnState.from_journal(_partial_journal())
    salvage_calls: list[dict] = []
    released: list[str] = []
    _patch_recover_lease_heartbeat(monkeypatch)
    clear_active_coordination()

    async def _fake_orphan(**kwargs):
        return None

    async def _fast_arm_slow_drive(plan, seed_completed, **kwargs):
        session = CoordinationSession(
            execution_id="exec-crash-1",
            total_workers=2,
            conversation_id=conversation_id,
        )
        # Drive intentionally longer than arm timeout (0.05s).
        session.drive_task = asyncio.create_task(asyncio.sleep(0.2))
        set_active_coordination(session)
        return ToolResult(tool_call_id="t1", success=True, output="armed")

    async def _factory(lease_arg, state_arg, *, sink):
        tool = MagicMock()
        tool.resume_plan = _fast_arm_slow_drive
        return tool

    async def _fake_salvage(**kwargs):
        salvage_calls.append(kwargs)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    set_crash_delegate_factory(_factory)
    try:
        monkeypatch.setattr(
            "agentcore.runtime.interaction_orphan.orphan_turn_before_recover",
            _fake_orphan,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.sweeper.salvage_interrupted_turn",
            _fake_salvage,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.service.release_turn_lease",
            _fake_release,
        )
        await recover_expired_lease(lease, state)
    finally:
        set_crash_delegate_factory(None)
        clear_active_coordination()

    assert salvage_calls == []
    assert released == [message_id]


async def test_recover_expired_lease_stalled_attempts_salvages(monkeypatch):
    """Too many claim→ready cycles without settle → salvage (no another ready)."""
    from agentcore.config import settings
    from agentcore.runtime.recover import recover_expired_lease
    from agentcore.runtime.recover_hooks import set_crash_delegate_factory

    monkeypatch.setattr(settings, "turn_lease_recover_max_attempts", 3)
    message_id = "stalled0-0000-0000-0000-000000000001"
    lease = SimpleNamespace(
        message_id=message_id,
        conversation_id="stalled0-0000-0000-0000-000000000002",
        user_id="u1",
        meta={"trace_id": "tr-s", "recover_attempts": 4},
        trace_id=None,
    )
    state = TurnState.from_journal(_partial_journal())
    salvage_calls: list[dict] = []
    released: list[str] = []
    factory_calls: list[str] = []
    _patch_recover_lease_heartbeat(monkeypatch)

    async def _factory(lease_arg, state_arg, *, sink):
        factory_calls.append("called")
        return MagicMock()

    async def _fake_salvage(**kwargs):
        salvage_calls.append(kwargs)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    set_crash_delegate_factory(_factory)
    try:
        monkeypatch.setattr(
            "agentcore.runtime.leases.sweeper.salvage_interrupted_turn",
            _fake_salvage,
        )
        monkeypatch.setattr(
            "agentcore.runtime.leases.service.release_turn_lease",
            _fake_release,
        )
        await recover_expired_lease(lease, state)
    finally:
        set_crash_delegate_factory(None)

    assert factory_calls == []
    assert len(salvage_calls) == 1
    assert released == [message_id]


async def test_production_crash_factory_returns_none_without_turn_started(monkeypatch):
    """Missing turn_started in journal → rebuild_failed warning + None (salvage upstream)."""
    from agentcore.runtime import crash_delegate as crash_mod
    from agentcore.runtime.crash_delegate import production_crash_delegate_factory
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(crash_mod, "logger", spy)
    lease = SimpleNamespace(
        message_id="m-no-started",
        conversation_id="c-no-started",
        user_id="u1",
    )
    state = TurnState.from_journal(_partial_journal())  # no turn_started
    tool = await production_crash_delegate_factory(lease, state, sink=EventSink())
    assert tool is None
    kw = spy.get("recover.crash_delegate_rebuild_failed")
    assert kw["message_id"] == "m-no-started"
    assert "turn_started" in kw["error"]


async def test_production_crash_factory_base_prompt_lists_system_skills(monkeypatch):
    """Crash rebuild ``<按需目录>`` comes from MergedConsultSource (includes system skills)."""
    from unittest.mock import AsyncMock

    from agentcore.runtime import crash_delegate as crash_mod
    from agentcore.runtime.crash_delegate import production_crash_delegate_factory
    from agentcore.runtime.facts import FactKind

    captured: dict = {}

    async def _fake_wire(**kwargs):
        captured["base_system_prompt"] = kwargs["base_system_prompt"]
        return SimpleNamespace(delegate_tool=object())

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _FakeConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(folder_id=None)

    class _FakeBoardRepo:
        def __init__(self, _session):
            pass

        async def get_by_conversation_id(self, *_a, **_k):
            return None

    backend = SimpleNamespace(location="server")
    monkeypatch.setattr(crash_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(crash_mod, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(crash_mod, "BoardRepository", _FakeBoardRepo)
    monkeypatch.setattr(
        crash_mod, "resolve_local_binding", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        crash_mod, "resolve_credentials", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        crash_mod, "resolve_profile_set", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        crash_mod, "resolve_memory_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        crash_mod, "resolve_conversation_history_access", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        crash_mod, "resolve_permission_axes", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(crash_mod, "turn_profiles_for_turn", lambda *_a, **_k: object())
    monkeypatch.setattr(crash_mod, "bind_credential_pricing_context", lambda *_a: None)
    monkeypatch.setattr(
        crash_mod.pipeline_pkg,
        "build_turn_router",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        crash_mod, "build_turn_backend", AsyncMock(return_value=backend)
    )
    monkeypatch.setattr(
        crash_mod, "session_callbacks", lambda *_a: (AsyncMock(), AsyncMock())
    )
    monkeypatch.setattr(
        crash_mod, "suspension_callbacks", lambda: (AsyncMock(), AsyncMock())
    )
    monkeypatch.setattr(
        crash_mod, "assemble_turn_rules", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        crash_mod, "resolve_exec_languages", AsyncMock(return_value=())
    )
    monkeypatch.setattr(
        crash_mod, "detect_workspace_git", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        crash_mod, "build_workspace_context", lambda *_a, **_k: ""
    )
    # Rules / memory IO must not block the skill-directory assertion.
    monkeypatch.setattr(
        "agentcore.memory.rules_injection.load_on_demand_user_rules",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "agentcore.memory.injection.load_memory_topics",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(crash_mod, "wire_crash_turn", _fake_wire)

    journal = [
        {
            "kind": FactKind.TURN_STARTED.value,
            "payload": {"user_message": "继续"},
            "ts": "t0",
            "seq": 0,
        },
        *_partial_journal(),
    ]
    lease = SimpleNamespace(
        message_id="m-crash-skills",
        conversation_id="c-crash-skills",
        user_id="u1",
    )
    tool = await production_crash_delegate_factory(
        lease, TurnState.from_journal(journal), sink=EventSink()
    )
    assert tool is not None
    prompt = captured["base_system_prompt"]
    assert "<按需目录>" in prompt
    # Worker catalog: 队员干活手册留下；派单/协调主管手册与 product_help 都不列。
    assert "- team_orchestration_advanced" not in prompt
    assert "- work_discipline" in prompt
    assert "- product_help" not in prompt


async def test_orphan_turn_lease_keeps_row_for_sweeper(monkeypatch):
    """CancelledError path must mark orphaned, not delete the lease row."""
    from agentcore.runtime.leases import service as lease_svc

    calls: list[tuple] = []

    class _FakeRepo:
        def __init__(self, _session):
            pass

        async def mark_orphaned(self, message_id, *, owner_id=None):
            calls.append(("orphan", message_id, owner_id))
            return True

        async def release(self, message_id, *, owner_id=None):
            calls.append(("release", message_id, owner_id))

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(lease_svc, "TurnLeaseRepository", _FakeRepo)
    monkeypatch.setattr(lease_svc, "async_session_factory", lambda: _FakeSession())

    await lease_svc.orphan_turn_lease("m-orphan")
    assert calls[0][0] == "orphan"
    assert calls[0][1] == "m-orphan"
    assert not any(c[0] == "release" for c in calls)
