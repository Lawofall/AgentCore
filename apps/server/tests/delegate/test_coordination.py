"""CEO 协调模式 Phase 2：非阻塞 delegate + 事件队列 + budget。"""

from __future__ import annotations

import asyncio

from agentcore.runtime.coordination.journal import (
    CoordinationSnapshotFact,
    coordination_from_journal,
)
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    CoordinationSnapshot,
    active_coordination,
    clear_active_coordination,
    coordination_budget_for_batch,
    release_turn_coordination,
    set_active_coordination,
    should_enter_coordination,
)
from agentcore.runtime.coordination.tools import CancelWorkerTool, UpdateSynthesisTool
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.interaction import InteractionRegistry
from tests.delegate.conftest import Provider, ctx, tool


def test_should_enter_coordination_gate():
    # Default-on: coordinate=True (or omitted at tool layer) + ≥1 + root.
    assert should_enter_coordination(coordinate=True, worker_count=2, depth=0)
    assert should_enter_coordination(coordinate=True, worker_count=1, depth=0)
    # Explicit opt-out.
    assert not should_enter_coordination(coordinate=False, worker_count=2, depth=0)
    assert not should_enter_coordination(coordinate=False, worker_count=1, depth=0)
    assert not should_enter_coordination(coordinate=True, worker_count=0, depth=0)
    assert not should_enter_coordination(coordinate=True, worker_count=2, depth=1)
    # B1: checkpoint_after batch + gate open → classic blocking (durable plan_review).
    assert not should_enter_coordination(
        coordinate=True,
        worker_count=2,
        depth=0,
        has_checkpoint=True,
        checkpoint_enabled=True,
    )
    # Gate off (evals): checkpoint nodes do not block coordination.
    assert should_enter_coordination(
        coordinate=True,
        worker_count=2,
        depth=0,
        has_checkpoint=True,
        checkpoint_enabled=False,
    )
    # Gate open but no checkpoint nodes → still enter.
    assert should_enter_coordination(
        coordinate=True,
        worker_count=2,
        depth=0,
        has_checkpoint=False,
        checkpoint_enabled=True,
    )


def test_coordination_snapshot_roundtrip():
    snap = CoordinationSnapshot(
        execution_id="e1",
        draft="草稿",
        completed_run_ids=["a"],
        progress_budget_remaining=2,
        decision_budget_remaining=1,
        total_workers=2,
        pending_events=[{"kind": "worker_completed", "payload": {"run_id": "a"}}],
    )
    raw = snap.to_dict()
    # Wave3a：新快照不再双写合计键；budget_remaining 属性仅为两池合计便利读。
    assert "budget_remaining" not in raw
    assert raw["progress_budget_remaining"] == 2
    assert raw["decision_budget_remaining"] == 1
    restored = CoordinationSnapshot.from_dict(raw)
    assert restored is not None
    assert restored.draft == "草稿"
    # 两池独立值 roundtrip 保真；budget_remaining 属性仍为合计。
    assert restored.progress_budget_remaining == 2
    assert restored.decision_budget_remaining == 1
    assert restored.budget_remaining == 3
    session = CoordinationSession.from_snapshot(restored)
    assert session.draft == "草稿"
    assert session.progress_budget_remaining == 2
    assert session.decision_budget_remaining == 1
    assert "a" in session.completed_run_ids
    pending = session.drain_nowait()
    assert len(pending) == 1
    assert pending[0].kind is CoordinationEventKind.WORKER_COMPLETED


def test_coordination_snapshot_expands_live_plan_and_interjections():
    """批次 4：live_plan / 插话队列 / 注入标记 roundtrip；凭据不入快照。"""
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    plan = RunPlan(
        nodes=[
            RunSpec(run_id="r1", role="研究员", task="调研", agent_name="研究员"),
            RunSpec(run_id="r2", role="撰写", task="写稿", agent_name="撰写"),
        ]
    )
    session = CoordinationSession(
        execution_id="e-snap",
        total_workers=2,
        draft="中间稿",
        all_completed_injected=False,
        harvest_scheduled=False,
        terminal_posted=True,
        settled_via=None,
        turn_attached=False,
    )
    session.live_plan = plan
    session.stash_interjection(
        "inj-1",
        {
            "content": "插一句",
            "user_id": "u1",
            "conversation_id": "c1",
            "attachments": [],
            "requires_tools": False,
            "x_client_platform": "desktop",
            "llm_credentials": {"api_key": "SECRET"},
            "llm_supports_tools": True,
        },
    )
    snap = session.snapshot()
    raw = snap.to_dict()
    assert raw["live_plan"] is not None
    assert len(raw["live_plan"]["nodes"]) == 2
    assert len(raw["pending_interjections"]) == 1
    assert "llm_credentials" not in raw["pending_interjections"][0]
    assert raw["terminal_posted"] is True
    assert raw["turn_attached"] is False

    restored = CoordinationSession.from_snapshot(
        CoordinationSnapshot.from_dict(raw)  # type: ignore[arg-type]
    )
    assert restored.live_plan is not None
    assert [n.run_id for n in restored.live_plan.nodes] == ["r1", "r2"]
    assert "inj-1" in restored.pending_interjections
    assert "llm_credentials" not in restored.pending_interjections["inj-1"]
    assert restored.pending_interjections["inj-1"]["content"] == "插一句"
    assert restored.terminal_posted is True
    assert restored.turn_attached is False


def test_terminal_settlement_errors_when_unsettled():
    """终态已投递但未附着注入 / harvest → clear 时 error 级对账告警。"""
    from structlog.testing import capture_logs

    clear_active_coordination()
    session = CoordinationSession(execution_id="e-unset", total_workers=2)
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 2, "total": 2},
        )
    )
    assert session.terminal_posted is True
    set_active_coordination(session)
    with capture_logs() as logs:
        clear_active_coordination("e-unset")
    assert any(e.get("event") == "coordination.terminal_unsettled" for e in logs)


def test_terminal_settlement_ok_when_attached_inject():
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-ok", total_workers=1)
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 1, "total": 1},
        )
    )
    session.mark_settled("attached_inject")
    set_active_coordination(session)
    clear_active_coordination("e-ok")  # no error


def test_single_pool_snapshot_rejected():
    """开发期：仅含旧合计键 ``budget_remaining`` 的快照不可恢复。"""
    assert (
        CoordinationSnapshot.from_dict(
            {"execution_id": "leg", "budget_remaining": 9, "total_workers": 3}
        )
        is None
    )


def test_flat_file_ownership_ignored():
    """开发期：flat path→owner 不 coerce；仅 v2 nested 入账。"""
    snap = CoordinationSnapshot.from_dict(
        {
            "execution_id": "own",
            "progress_budget_remaining": 4,
            "decision_budget_remaining": 2,
            "file_ownership": {"docs/a.md": "w1"},
        }
    )
    assert snap is not None
    assert snap.file_ownership == {}


def test_coordination_from_journal():
    """journal 帧须带两池键才能 fold；仅合计键 → 拒绝。"""
    legacy = CoordinationSnapshotFact(
        snapshot={
            "execution_id": "ex",
            "draft": "d",
            "completed_run_ids": [],
            "budget_remaining": 5,
            "total_workers": 2,
            "active": True,
        }
    ).to_fact()
    assert coordination_from_journal([legacy.entry()]) is None

    fact = CoordinationSnapshotFact(
        snapshot={
            "execution_id": "ex",
            "draft": "d",
            "completed_run_ids": [],
            "progress_budget_remaining": 3,
            "decision_budget_remaining": 2,
            "total_workers": 2,
            "active": True,
        }
    ).to_fact()
    snap = coordination_from_journal([fact.entry()])
    assert snap is not None
    assert snap.draft == "d"
    assert snap.progress_budget_remaining == 3
    assert snap.decision_budget_remaining == 2
    assert snap.budget_remaining == 5


def test_necessary_decision_points():
    session = CoordinationSession(execution_id="e", total_workers=3)
    success = [
        CoordinationEvent(
            kind=CoordinationEventKind.WORKER_COMPLETED,
            payload={"run_id": "w1", "status": "completed"},
        )
    ]
    assert not session.is_necessary_decision(success)
    session.note_decision_points(success)
    skipped = [
        CoordinationEvent(
            kind=CoordinationEventKind.WORKER_COMPLETED,
            payload={"run_id": "w2", "status": "skipped"},
        )
    ]
    assert not session.is_necessary_decision(skipped)
    failed = [
        CoordinationEvent(
            kind=CoordinationEventKind.WORKER_COMPLETED,
            payload={"run_id": "w3", "status": "failed"},
        )
    ]
    assert session.is_necessary_decision(failed)
    assert session.is_necessary_decision(
        [CoordinationEvent(kind=CoordinationEventKind.ESCALATION, payload={})]
    )
    assert session.is_necessary_decision(
        [CoordinationEvent(kind=CoordinationEventKind.ALL_COMPLETED, payload={})]
    )


async def test_solo_worker_enters_coordination():
    """金线：单 worker + coordinate=true（默认）进入协调，立即返回『团队已启动』。"""
    clear_active_coordination()
    t = tool(Provider(["SOLO_OUT"]))
    result = await t.execute(
        {
            "tasks": [{"role": "工程师", "task": "做一件事"}],
            "coordinate": True,
        },
        ctx(),
    )
    assert result.success is True
    assert result.is_terminal is False
    assert "团队已启动" in result.output
    assert "SOLO_OUT" not in result.output
    session = active_coordination("e")
    assert session is not None
    assert session.drive_task is not None
    await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination("e")


async def test_solo_worker_explicit_coordinate_false_stays_blocking():
    """金线：单 worker + coordinate=false → 经典阻塞，返回完整产物。"""
    clear_active_coordination()
    t = tool(Provider(["SOLO_OUT"]))
    result = await t.execute(
        {
            "tasks": [{"role": "工程师", "task": "做一件事"}],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert result.is_terminal is False
    assert "SOLO_OUT" in result.output
    assert "团队已启动" not in result.output
    assert active_coordination() is None


async def test_multi_worker_omitted_coordinate_defaults_to_coordination():
    """金线：多 worker 省略 coordinate → 默认协调（立即返回『团队已启动』）。"""
    clear_active_coordination()
    t = tool(Provider(["AOUT", "BOUT"]))
    result = await t.execute(
        {"tasks": [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}]},
        ctx(),
    )
    assert result.success is True
    assert "团队已启动" in result.output
    assert "AOUT" not in result.output
    session = active_coordination("e")
    assert session is not None
    assert session.drive_task is not None
    await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination("e")


async def test_multi_worker_explicit_coordinate_false_stays_blocking():
    """金线：多 worker + coordinate=false → 经典阻塞语义。"""
    t = tool(Provider(["AOUT", "BOUT"]))
    result = await t.execute(
        {
            "tasks": [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert "AOUT" in result.output
    assert "BOUT" in result.output
    assert "团队已启动" not in result.output
    assert active_coordination() is None


async def test_coordinate_returns_immediately_and_posts_events():
    """coordinate=true + ≥2 worker → 立即返回；后台完成后投递 all_completed。"""
    clear_active_coordination()
    t = tool(Provider(["AOUT", "BOUT"]))
    result = await t.execute(
        {
            "tasks": [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}],
            "coordinate": True,
        },
        ctx(),
    )
    assert result.success is True
    assert "团队已启动" in result.output
    assert "AOUT" not in result.output  # not yet folded into tool result
    session = active_coordination("e")
    assert session is not None
    assert session.total_workers == 2
    assert session.drive_task is not None

    # Wait for background drive to finish and post all_completed.
    await asyncio.wait_for(session.drive_task, timeout=10)
    events = session.drain_nowait()
    kinds = [e.kind for e in events]
    assert CoordinationEventKind.WORKER_COMPLETED in kinds
    assert CoordinationEventKind.ALL_COMPLETED in kinds
    assert len(session.completed_run_ids) == 2
    clear_active_coordination("e")


async def test_coord_drive_session_saver_does_not_shadow_coordination_session():
    """Regression: ``for session in registered`` must not rebind the coordination session.

    With ``_session_store`` + ``_session_saver``, the post-wave saver loop iterates
    RunSessions. A prior bug rebound the outer ``session`` parameter so
    ``session.post(ALL_COMPLETED)`` raised ``AttributeError: 'RunSession' object
    has no attribute 'post'``.
    """
    from agentcore.runtime.sessions import SessionStore
    from agentcore.tools.builtin.delegate import DelegateTool
    from agentcore.tools.registry import ToolRegistry

    clear_active_coordination()
    store = SessionStore()
    saved: list = []

    async def _saver(run_session) -> None:
        saved.append(run_session)

    t = DelegateTool(
        llm=Provider(["AOUT", "BOUT"]),
        sink=EventSink(),
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx(),
        session_store=store,
        session_saver=_saver,
        folder_id="test_birth",
        approval_gate=None,
    )
    result = await t.execute(
        {
            "tasks": [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}],
            "coordinate": True,
        },
        ctx(),
    )
    assert result.success is True
    session = active_coordination("e")
    assert session is not None
    assert session.drive_task is not None
    await asyncio.wait_for(session.drive_task, timeout=10)
    events = session.drain_nowait()
    kinds = [e.kind for e in events]
    assert CoordinationEventKind.ALL_COMPLETED in kinds, (
        f"coordination post must succeed after session_saver; kinds={kinds}"
    )
    assert saved, "trigger path requires register_sessions → session_saver"
    clear_active_coordination("e")


async def test_update_synthesis_emits_preview():
    clear_active_coordination()
    sink = EventSink()
    session = CoordinationSession(execution_id="e", total_workers=2)
    from agentcore.runtime.coordination.session import set_active_coordination

    set_active_coordination(session)
    syn = UpdateSynthesisTool(sink=sink)
    result = await syn.execute({"draft": "进展中的合成草稿"}, ctx())
    assert result.success is True
    assert session.draft == "进展中的合成草稿"
    sink.close()
    previews = [
        e async for e in sink if e.type == EventType.TEAM_SYNTHESIS_PREVIEW
    ]
    assert len(previews) == 1
    assert previews[0].payload["text"] == "进展中的合成草稿"
    assert previews[0].payload["in_progress"] is True
    clear_active_coordination()


async def test_wait_tool_is_clean_noop_during_coordination():
    """协调期无需处置：wait 成功返回，不产生 error 工具调用。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e", total_workers=2)
    from structlog.testing import capture_logs

    from agentcore.runtime.coordination.session import set_active_coordination
    from agentcore.runtime.coordination.tools import WaitTool

    set_active_coordination(session)
    tool = WaitTool()
    with capture_logs() as logs:
        result = await tool.execute({"reason": "纯进展，无需处置"}, ctx())
    assert result.success is True
    assert result.error is None
    assert "等待" in (result.output or "")
    waits = [e for e in logs if e.get("event") == "coordination.wait"]
    assert len(waits) == 1
    assert waits[0]["execution_id"] == "e"
    clear_active_coordination()


async def test_wait_tool_finds_adopted_live_when_context_is_mint():
    """跨回合 adopt：context.execution_id 是本回合 mint，wait 仍能找到旧 live 图。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import (
        current_execution_id,
        set_active_coordination,
    )
    from agentcore.runtime.coordination.tools import WaitTool

    session = CoordinationSession(execution_id="e-live", total_workers=2)
    set_active_coordination(session)
    token = current_execution_id.set("e-live")
    try:
        mint_ctx = ctx()
        mint_ctx.execution_id = "e-mint"
        result = await WaitTool().execute({}, mint_ctx)
        assert result.success is True
        assert result.error is None
        assert "等待" in (result.output or "")
    finally:
        current_execution_id.reset(token)
        clear_active_coordination()


async def test_wait_tool_rejects_when_hot_user_pending(monkeypatch):
    """有 pending 热审批时禁止 CEO 空 wait。"""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e", total_workers=2, conversation_id="c-wait"
    )
    from agentcore.runtime.coordination.session import set_active_coordination
    from agentcore.runtime.coordination.tools import WaitTool
    from agentcore.runtime.interaction import InteractionKind, InteractionRegistry

    reg = InteractionRegistry()
    reg.create("a1", "c-wait", kind=InteractionKind.APPROVAL, payload={"tool_name": "x"})
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.default_interaction_registry",
        lambda: reg,
    )
    set_active_coordination(session)
    try:
        result = await WaitTool().execute({}, ctx())
        assert result.success is False
        assert "审批" in (result.error or "")
        assert "wait" in (result.error or "").lower() or "禁止" in (result.error or "")
    finally:
        clear_active_coordination()


async def test_wait_tool_errors_outside_coordination():
    clear_active_coordination()
    from agentcore.runtime.coordination.tools import WaitTool

    result = await WaitTool().execute({}, ctx())
    assert result.success is False
    assert "协调模式" in (result.error or "")


async def test_cancel_worker_requests_cancel():
    clear_active_coordination()
    session = CoordinationSession(execution_id="e", total_workers=2)
    from agentcore.runtime.coordination.session import set_active_coordination

    set_active_coordination(session)
    # Register the in-flight worker (as bridge does on dispatch) so cancel resolves.
    session.arm_worker_timeout("w1", role="研究员", timeout_s=60)
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "w1", "reason": "重复"}, ctx())
    assert result.success is True
    assert "w1" in session.cancel_run_ids()
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_solo_cancel_worker_requests_cancel():
    """验收：单 worker 协调 session 上 cancel_worker 可达且生效。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e", total_workers=1)
    from agentcore.runtime.coordination.session import set_active_coordination

    set_active_coordination(session)
    session.arm_worker_timeout("w1", role="工程师", timeout_s=60)
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "w1", "reason": "用户要求停止"}, ctx())
    assert result.success is True
    assert "w1" in session.cancel_run_ids()
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_cancel_worker_resolves_suffix_short_name():
    """短名（run_id 尾缀）唯一匹配 → 解析为引擎全名后进 cancel 集合。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination

    session = CoordinationSession(execution_id="e", total_workers=2)
    set_active_coordination(session)
    session.arm_worker_timeout("del_abc_data_researcher", role="数据研究员", timeout_s=60)
    session.arm_worker_timeout("del_xyz_writer", role="写手", timeout_s=60)
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "data_researcher"}, ctx())
    assert result.success is True
    assert "del_abc_data_researcher" in session.cancel_run_ids()
    assert "del_xyz_writer" not in session.cancel_run_ids()
    assert "del_abc_data_researcher" in (result.output or "")
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_cancel_worker_resolves_role_name():
    """role 名唯一匹配（run_id 不含 role 尾缀时的回退通道）。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination

    session = CoordinationSession(execution_id="e", total_workers=2)
    set_active_coordination(session)
    # run_id 不以 role 结尾 → 尾缀通道落空，靠 role 精确匹配解析。
    session.arm_worker_timeout("del_run_one", role="researcher", timeout_s=60)
    session.arm_worker_timeout("del_run_two", role="writer", timeout_s=60)
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "researcher"}, ctx())
    assert result.success is True
    assert "del_run_one" in session.cancel_run_ids()
    assert "由「researcher」解析" in (result.output or "")
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_cancel_worker_ambiguous_errors_without_cancel():
    """多义（同 role / 同尾缀多命中）→ 明确报错、不写 cancel 集合、列出候选。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination

    session = CoordinationSession(execution_id="e", total_workers=2)
    set_active_coordination(session)
    session.arm_worker_timeout("del_abc_data_researcher", role="数据研究员", timeout_s=60)
    session.arm_worker_timeout("del_xyz_data_researcher", role="数据研究员", timeout_s=60)
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "data_researcher"}, ctx())
    assert result.success is False
    assert len(session.cancel_run_ids()) == 0
    assert "del_abc_data_researcher" in (result.error or "")
    assert "del_xyz_data_researcher" in (result.error or "")
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_cancel_worker_miss_lists_running_workers():
    """不命中 → 报错并列出当前可取消的在跑 worker（run_id + role），不虚假成功。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination

    session = CoordinationSession(execution_id="e", total_workers=2)
    set_active_coordination(session)
    session.arm_worker_timeout("del_abc_data_researcher", role="数据研究员", timeout_s=60)
    session.arm_worker_timeout("del_xyz_writer", role="写手", timeout_s=60)
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "不存在的名字"}, ctx())
    assert result.success is False
    assert len(session.cancel_run_ids()) == 0
    assert "del_abc_data_researcher" in (result.error or "")
    assert "数据研究员" in (result.error or "")
    assert "del_xyz_writer" in (result.error or "")
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_cancel_worker_already_ended_is_idempotent_success():
    """完成 / disarm 后：在跑表解析不到，但确认为本会话已结束 → 幂等成功、不进红错。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    session = CoordinationSession(execution_id="e", total_workers=2)
    set_active_coordination(session)
    session.live_plan = RunPlan(
        nodes=[
            RunSpec(run_id="del_abc_data_researcher", role="数据研究员", task="研"),
            RunSpec(run_id="del_xyz_writer", role="写手", task="写"),
        ]
    )
    session.arm_worker_timeout("del_abc_data_researcher", role="数据研究员", timeout_s=60)
    session.arm_worker_timeout("del_xyz_writer", role="写手", timeout_s=60)
    session.mark_worker_completed("del_abc_data_researcher")
    # 已完成：不再出现在在跑解析；ended 解析仍命中。
    assert session.resolve_cancel_target("data_researcher").run_id is None
    assert session.resolve_cancel_target("del_abc_data_researcher").run_id is None
    assert (
        session.resolve_ended_worker("del_abc_data_researcher").run_id
        == "del_abc_data_researcher"
    )
    assert session.resolve_ended_worker("data_researcher").run_id == "del_abc_data_researcher"
    # 仍在跑的 worker 照常可解析并取消。
    assert session.resolve_cancel_target("writer").run_id == "del_xyz_writer"
    cancel = CancelWorkerTool()
    ended = await cancel.execute({"run_id": "data_researcher"}, ctx())
    assert ended.success is True
    assert "已结束" in (ended.output or "")
    assert "del_abc_data_researcher" not in session.cancel_run_ids()
    # 全名同样幂等成功。
    ended_full = await cancel.execute({"run_id": "del_abc_data_researcher"}, ctx())
    assert ended_full.success is True
    assert "无需取消" in (ended_full.output or "")
    # 乱 id 仍失败。
    miss = await cancel.execute({"run_id": "不存在的名字"}, ctx())
    assert miss.success is False
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_cancel_worker_failed_is_idempotent_success():
    """FAILED 终态：不在跑表，但已结束 → 幂等成功、不进 cancel 集合。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    session = CoordinationSession(execution_id="e", total_workers=2)
    set_active_coordination(session)
    session.live_plan = RunPlan(
        nodes=[
            RunSpec(run_id="del_abc_n5", role="冒烟审计员", task="审"),
            RunSpec(run_id="del_abc_n4", role="集成工程师", task="集"),
        ]
    )
    session.arm_worker_timeout("del_abc_n5", role="冒烟审计员", timeout_s=60)
    session.arm_worker_timeout("del_abc_n4", role="集成工程师", timeout_s=60)
    session.mark_worker_completed("del_abc_n5")
    session.failed_run_ids.add("del_abc_n5")
    session.vacated_run_ids.add("del_abc_n5")
    assert session.resolve_cancel_target("del_abc_n5").run_id is None
    assert session.resolve_ended_worker("del_abc_n5").run_id == "del_abc_n5"
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "del_abc_n5"}, ctx())
    assert result.success is True
    assert "已结束" in (result.output or "")
    assert "无需取消" in (result.output or "")
    assert "del_abc_n5" not in session.cancel_run_ids()
    assert "del_abc_n4" not in session.cancel_run_ids()
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_cancel_worker_skipped_is_idempotent_success():
    """SKIPPED 终态：仅 vacated 亦可幂等成功（防御未进 completed 的路径）。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination

    session = CoordinationSession(execution_id="e", total_workers=2)
    set_active_coordination(session)
    session.arm_worker_timeout("del_skip_n1", role="写手", timeout_s=60)
    session.disarm_worker_timeout("del_skip_n1")
    # Mimic a path that only stamped vacated (skipped seat), not completed.
    session.vacated_run_ids.add("del_skip_n1")
    assert session.resolve_cancel_target("del_skip_n1").run_id is None
    assert session.resolve_ended_worker("del_skip_n1").run_id == "del_skip_n1"
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "del_skip_n1"}, ctx())
    assert result.success is True
    assert "无需取消" in (result.output or "")
    assert len(session.cancel_run_ids()) == 0
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_cancel_worker_pending_withdraws_from_queue():
    """排队未开跑：命中 live_plan → 正式撤出（skipped/vacated），成功文案，不自动改在跑同角色。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    session = CoordinationSession(execution_id="e", total_workers=2)
    set_active_coordination(session)
    session.live_plan = RunPlan(
        nodes=[
            RunSpec(run_id="del_wave_n5", role="冒烟审计员", task="审"),
            RunSpec(run_id="del_wave_n4b", role="冒烟审计员", task="审2"),
        ]
    )
    session.arm_worker_timeout("del_wave_n4b", role="冒烟审计员", timeout_s=60)
    assert session.resolve_pending_worker("del_wave_n5").run_id == "del_wave_n5"
    assert session.resolve_cancel_target("del_wave_n5").run_id is None
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "del_wave_n5", "reason": "缩 scope"}, ctx())
    assert result.success is True
    assert "已从队列撤出" in (result.output or "")
    assert "del_wave_n5" in session.completed_run_ids
    assert "del_wave_n5" in session.vacated_run_ids
    assert "del_wave_n5" in session.cancel_run_ids()
    # Same-role runner must not be auto-cancelled.
    assert "del_wave_n4b" not in session.cancel_run_ids()
    assert "del_wave_n4b" in dict(session.running_workers())
    # Idempotent after withdraw (ended path).
    again = await cancel.execute({"run_id": "del_wave_n5"}, ctx())
    assert again.success is True
    assert "无需取消" in (again.output or "")
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_cancel_worker_unknown_rejects_without_auto_retarget():
    """未知 id：拒绝；列出可取消在跑者，不自动 request_cancel；不误撤 pending。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    session = CoordinationSession(execution_id="e", total_workers=2)
    set_active_coordination(session)
    session.live_plan = RunPlan(
        nodes=[
            RunSpec(run_id="del_wave_n5", role="冒烟审计员", task="审"),
            RunSpec(run_id="del_wave_n4b", role="冒烟审计员", task="审2"),
        ]
    )
    session.arm_worker_timeout("del_wave_n4b", role="冒烟审计员", timeout_s=60)
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "del_wave_n_unknown"}, ctx())
    assert result.success is False
    assert len(session.cancel_run_ids()) == 0
    assert "del_wave_n5" not in session.completed_run_ids
    assert "del_wave_n5" not in session.vacated_run_ids
    err = result.error or ""
    assert "del_wave_n4b" in err
    assert "当前可取消" in err
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


async def test_cancel_worker_cancelled_terminal_is_idempotent_success():
    """CANCELLED 终态同幂等成功（与完成/失败/跳过对齐）。"""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination

    session = CoordinationSession(execution_id="e", total_workers=1)
    set_active_coordination(session)
    session.arm_worker_timeout("del_cancelled_w", role="写手", timeout_s=60)
    session.mark_worker_completed("del_cancelled_w")
    session.vacated_run_ids.add("del_cancelled_w")
    cancel = CancelWorkerTool()
    result = await cancel.execute({"run_id": "del_cancelled_w"}, ctx())
    assert result.success is True
    assert "无需取消" in (result.output or "")
    assert len(session.cancel_run_ids()) == 0
    session.close()
    await asyncio.sleep(0)
    clear_active_coordination()


def test_inject_timeout_shows_full_run_id():
    """TIMEOUT 注入文案须带全名 run_id，供 CEO 直接抄进 cancel_worker。"""
    from agentcore.runtime.coordination.inject import format_coordination_events

    session = CoordinationSession(execution_id="e", total_workers=2)
    with_elapsed = CoordinationEvent(
        kind=CoordinationEventKind.TIMEOUT,
        payload={
            "run_id": "del_abc_data_researcher",
            "role": "数据研究员",
            "elapsed_s": 7.0,
            "threshold_s": 120,
            "status": "running",
            "reason": "运行过久",
        },
    )
    text = format_coordination_events(session, [with_elapsed])
    assert "run_id=del_abc_data_researcher" in text
    no_elapsed = CoordinationEvent(
        kind=CoordinationEventKind.TIMEOUT,
        payload={"run_id": "del_abc_data_researcher", "role": "数据研究员"},
    )
    text2 = format_coordination_events(session, [no_elapsed])
    assert "run_id=del_abc_data_researcher" in text2


async def test_coord_tools_reject_outside_session():
    clear_active_coordination()
    syn = UpdateSynthesisTool(sink=EventSink())
    bad = await syn.execute({"draft": "x"}, ctx())
    assert bad.success is False
    cancel = CancelWorkerTool()
    bad2 = await cancel.execute({"run_id": "w1"}, ctx())
    assert bad2.success is False


async def test_update_synthesis_soft_tip_when_session_closed():
    """Team finished (session still registered, active=False) → soft success tip."""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e", total_workers=2)
    from agentcore.runtime.coordination.session import set_active_coordination

    set_active_coordination(session)
    session.close()
    assert session.active is False
    syn = UpdateSynthesisTool(sink=EventSink())
    result = await syn.execute({"draft": "终稿草稿"}, ctx())
    assert result.success is True
    assert "全部完成" in (result.output or "")
    assert "content_delta" in (result.output or "")
    clear_active_coordination()


async def test_terminal_not_skipped_when_both_pools_exhausted():
    """必要决策永不因预算被跳过：两池均为 0 时，终局 all_completed 仍唤醒并收口。"""
    from agentcore.runtime.coordination.wait import await_coordination_injection

    clear_active_coordination()
    session = CoordinationSession(
        execution_id="exec-b",
        total_workers=2,
        progress_budget_remaining=0,
        decision_budget_remaining=0,
    )
    session._saw_first_completion = True
    from agentcore.runtime.coordination.session import set_active_coordination

    set_active_coordination(session)
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 2, "total": 2},
        )
    )
    msgs = await await_coordination_injection([])
    assert len(msgs) == 1
    assert "all_completed" in (msgs[0].content or "")
    assert session.active is False  # 终局收口，即便两池都空
    clear_active_coordination()


async def test_wait_emits_coordination_wait_sse_enter_and_exit(monkeypatch):
    """Legitimate long wait must push coordination_wait SSE (enter + exit) for UX."""
    import agentcore.runtime.coordination.wait as coord_wait
    from agentcore.runtime.coordination.session import (
        current_execution_id,
        set_active_coordination,
    )
    from agentcore.runtime.coordination.wait import await_coordination_injection

    class _Sink:
        def __init__(self) -> None:
            self.events: list = []

        def emit(self, event) -> None:  # noqa: ANN001
            self.events.append(event)

    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 30.0)
    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-wait-ux", total_workers=8)
    session.completed_run_ids = {"w1", "w2", "w3", "w4", "w5"}
    sink = _Sink()
    session.event_sink = sink
    set_active_coordination(session)
    token = current_execution_id.set("exec-wait-ux")

    async def _post_later() -> None:
        await asyncio.sleep(0.05)
        session.post(
            CoordinationEvent(
                kind=CoordinationEventKind.WORKER_COMPLETED,
                payload={
                    "run_id": "w6",
                    "role": "F",
                    "status": "failed",
                    "summary": "ok",
                },
            )
        )

    try:
        post_task = asyncio.create_task(_post_later())
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=2.0)
        await post_task
    finally:
        current_execution_id.reset(token)
        clear_active_coordination()

    assert len(msgs) == 1
    wait_events = [e for e in sink.events if e.type is EventType.COORDINATION_WAIT]
    assert len(wait_events) >= 2
    assert wait_events[0].payload["waiting"] is True
    assert wait_events[0].payload["completed"] == 5
    assert wait_events[0].payload["total"] == 8
    assert wait_events[-1].payload["waiting"] is False


async def test_wait_drain_nowait_skips_coordination_wait_sse():
    """Immediate drain (no blocking wait) must stay silent — no UX flicker."""
    from agentcore.runtime.coordination.session import set_active_coordination
    from agentcore.runtime.coordination.wait import await_coordination_injection

    class _Sink:
        def __init__(self) -> None:
            self.events: list = []

        def emit(self, event) -> None:  # noqa: ANN001
            self.events.append(event)

    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-drain-ux", total_workers=2)
    sink = _Sink()
    session.event_sink = sink
    set_active_coordination(session)
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.WORKER_COMPLETED,
            payload={"run_id": "w1", "role": "A", "status": "completed", "summary": "ok"},
        )
    )
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 1, "total": 2},
        )
    )
    msgs = await await_coordination_injection([])
    clear_active_coordination()
    assert len(msgs) == 1
    wait_events = [e for e in sink.events if e.type is EventType.COORDINATION_WAIT]
    assert wait_events == []


async def test_worker_timeout_posts_event_without_cancel():
    """Phase 3: timer notifies CEO; worker is NOT auto-cancelled."""
    clear_active_coordination()
    from agentcore.runtime.coordination.session import set_active_coordination

    session = CoordinationSession(execution_id="exec-to", total_workers=2)
    set_active_coordination(session)
    session.arm_worker_timeout("w-slow", role="慢工", timeout_s=0.05)
    events = await session.wait_events(timeout=2.0)
    assert any(e.kind is CoordinationEventKind.TIMEOUT for e in events)
    timeout_ev = next(e for e in events if e.kind is CoordinationEventKind.TIMEOUT)
    assert timeout_ev.payload["run_id"] == "w-slow"
    assert timeout_ev.payload["role"] == "慢工"
    assert timeout_ev.payload["status"] == "running"
    assert timeout_ev.payload["elapsed_s"] >= 0.05
    assert "w-slow" not in session.cancel_run_ids()
    assert session.is_necessary_decision(events)
    session.disarm_worker_timeout("w-slow")
    clear_active_coordination()


async def test_worker_timeout_disarmed_on_completion():
    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-to2", total_workers=2)
    session.arm_worker_timeout("w1", role="A", timeout_s=5.0)
    session.mark_worker_completed("w1")
    await asyncio.sleep(0.05)
    assert session.drain_nowait() == []
    clear_active_coordination()


async def test_escalate_routes_to_coordination_queue():
    """Phase 3: worker escalate posts into CEO queue when coordinating."""
    clear_active_coordination()
    from agentcore.runtime.coordination.bridge import post_escalation_to_coordination
    from agentcore.runtime.coordination.session import set_active_coordination

    session = CoordinationSession(execution_id="exec-esc", total_workers=2)
    set_active_coordination(session)
    assert post_escalation_to_coordination(
        run_id="r1",
        role="研究员",
        kind="scope",
        question="真实需求变了",
        assumption="按原 brief 继续",
    )
    events = session.drain_nowait()
    assert len(events) == 1
    assert events[0].kind is CoordinationEventKind.ESCALATION
    assert events[0].payload["kind"] == "scope"
    assert events[0].payload["question"] == "真实需求变了"
    # Dedupe: same signal twice → one event.
    assert not post_escalation_to_coordination(
        run_id="r1",
        role="研究员",
        kind="scope",
        question="真实需求变了",
    )
    assert session.drain_nowait() == []
    clear_active_coordination()


async def test_escalate_ignored_outside_coordination():
    clear_active_coordination()
    from agentcore.runtime.coordination.bridge import post_escalation_to_coordination

    assert not post_escalation_to_coordination(
        run_id="r1", kind="normal", question="无人协调"
    )


async def test_note_conflict_posts_escalation():
    clear_active_coordination()
    from agentcore.runtime.coordination.bridge import post_note_to_coordination
    from agentcore.runtime.coordination.session import set_active_coordination

    session = CoordinationSession(execution_id="exec-note", total_workers=2)
    set_active_coordination(session)
    post_note_to_coordination(
        run_id="r2",
        role="写手",
        kind="decision",
        text="POST /auth 用 password",
        conflict="⚠️ 与 研究员 的决定可能冲突",
    )
    events = session.drain_nowait()
    kinds = [e.kind for e in events]
    assert CoordinationEventKind.NOTE_POSTED in kinds
    assert CoordinationEventKind.ESCALATION in kinds
    esc = next(e for e in events if e.kind is CoordinationEventKind.ESCALATION)
    assert esc.payload["kind"] == "note_conflict"
    assert esc.payload["source"] == "note_wall"
    clear_active_coordination()


async def test_coordination_scope_boundary_proceeds():
    """SCOPE under coordination → PROCEED (no YIELD) + escalation event."""
    clear_active_coordination()
    from agentcore.runtime.coordination.bridge import coordination_boundary_hook
    from agentcore.runtime.coordination.session import set_active_coordination
    from agentcore.runtime.runs import BoundaryOutcome, BoundaryReason
    from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState

    session = CoordinationSession(execution_id="exec-scope", total_workers=2)
    set_active_coordination(session)
    hook = coordination_boundary_hook(session, base_hook=None)
    node = RunSpec(run_id="a", role="研究员", task="调研", agent_name="研究员")
    state = RunState(
        phase=RunPhase.COMPLETED,
        content="ok",
        escalations=[{"kind": "scope", "question": "范围偏了", "consumed": False}],
    )
    outcome = await hook(BoundaryReason.SCOPE, [node], {"a": state})
    assert outcome is BoundaryOutcome.PROCEED
    events = session.drain_nowait()
    assert any(e.kind is CoordinationEventKind.ESCALATION for e in events)
    clear_active_coordination()


async def test_coordinate_react_loop_e2e(monkeypatch):
    """ReAct 全环：CEO delegate → 协调注入 → 终稿。

    例行成功完成不单独叫醒；终局 all_completed（或空转 yield 捎带摘要）后写终稿。
    Drives the real ``react_loop`` (role=captain) with a scripted CEO provider and
    a separate worker LLM on DelegateTool — covers non-blocking arming,
    coordination event injection between rounds, and final content after all_completed.
    """
    import json
    from pathlib import Path

    import agentcore.runtime.coordination.wait as coord_wait
    from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
    from agentcore.runtime.coordination.session import current_execution_id
    from agentcore.runtime.engine import react_loop
    from agentcore.tools.builtin.delegate import DelegateTool
    from agentcore.tools.protocol import ToolContext
    from agentcore.tools.registry import ToolRegistry
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace
    from tests.llm_helpers import make_profile_params

    # Keep idle-wait short if the race ever misses mid-wave events.
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 2.0)
    clear_active_coordination()
    sink = EventSink()
    draft_text = "进展中的合成草稿：两边方向一致，优先方案 A。"

    class _SlowSecondWorker:
        """First worker instant; second delayed past coalesce so CEO sees mid-wave."""

        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request):  # noqa: ANN001
            idx = self.calls
            self.calls += 1
            if idx >= 1:
                await asyncio.sleep(0.25)
            text = "AOUT" if idx == 0 else "BOUT"
            from tests.delegate.conftest import _upstream_body

            yield LLMChunk(delta_content=_upstream_body(text))

    class _CoordCeoProvider:
        def __init__(self) -> None:
            self.delegate_calls = 0
            self.synth_calls = 0
            self.final_calls = 0

        async def stream(self, request):  # noqa: ANN001
            tool_msgs = [m for m in request.messages if m.role == "tool"]
            last_tool = (tool_msgs[-1].content or "") if tool_msgs else ""
            coord_injected = any(
                m.role == "user" and m.content and "团队协调事件" in m.content
                for m in request.messages
            )
            all_done = any(
                m.role == "user"
                and m.content
                and "all_completed" in m.content
                for m in request.messages
            )
            if not tool_msgs:
                self.delegate_calls += 1
                # Sequential deps + slow r2 → first completion wakes CEO alone.
                # Omit coordinate — D2 默认协调；显式 true 等价。
                args = json.dumps(
                    {
                        "tasks": [
                            {"id": "r1", "role": "研究员", "task": "做A"},
                            {
                                "id": "r2",
                                "role": "写手",
                                "task": "做B",
                                "depends_on": ["r1"],
                            },
                        ],
                    }
                )
                yield LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="ceo-dc1",
                            function_name="delegate",
                            arguments_delta=args,
                        )
                    ]
                )
            elif "已更新合成草稿" in last_tool or all_done:
                self.final_calls += 1
                yield LLMChunk(delta_content="最终合成：A 与 B 已对齐，按方案 A 定稿。")
            elif coord_injected and self.synth_calls == 0 and not all_done:
                self.synth_calls += 1
                args = json.dumps({"draft": draft_text})
                yield LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="ceo-syn1",
                            function_name="update_synthesis",
                            arguments_delta=args,
                        )
                    ]
                )
            else:
                self.final_calls += 1
                yield LLMChunk(delta_content="最终合成：A 与 B 已对齐，按方案 A 定稿。")

    ceo_llm = _CoordCeoProvider()
    worker_llm = _SlowSecondWorker()
    base_ctx = ToolContext.create(
        execution_id="e-coord-e2e",
        run_id="cap",
        agent_id="cap",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )
    delegate = DelegateTool(
        llm=worker_llm,
        sink=sink,
        system_prompt="SYS",
        user_message="原始请求：并行做 A 和 B",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=base_ctx,
        folder_id="test_birth",
        approval_gate=None,
    )
    reg = ToolRegistry()
    reg.register(delegate)
    reg.register(UpdateSynthesisTool(sink=sink))
    reg.register(CancelWorkerTool())

    messages: list[LLMMessage] = [
        LLMMessage(role="user", content="请协调团队并行完成 A 和 B"),
    ]
    exec_token = current_execution_id.set("e-coord-e2e")
    try:
        content, _reasoning, _usage, rounds = await react_loop(
            messages=messages,
            llm=ceo_llm,
            tools=reg,
            sink=sink,
            tool_context=base_ctx,
            profile=make_profile_params(max_rounds=12),
            turn_model="m",
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        clear_active_coordination("e-coord-e2e")
        current_execution_id.reset(exec_token)

    assert ceo_llm.delegate_calls == 1
    # 中途 synth 只在空转 yield 捎带已完成摘要时才发生；终稿必须有。
    assert "最终合成" in content
    assert rounds >= 2
    assert any("团队协调事件" in (m.content or "") for m in messages if m.role == "user")

    sink.close()
    assert worker_llm.calls >= 2


async def test_captain_silent_listen_rounds_do_not_trip_empty_ladder(monkeypatch):
    """协调监听豁免：captain 对纯进展事件连续静默（无正文无工具）不进 B2 空响应梯子。

    连续两轮静默 = 默认 ``engine_empty_response_threshold``；无豁免时第二轮即
    DEGRADED 提前收口，all_completed 后的终稿不会出现。
    """
    from pathlib import Path

    import agentcore.runtime.coordination.wait as coord_wait
    from agentcore.llm.provider.protocol import LLMChunk, LLMMessage
    from agentcore.runtime.coordination.session import (
        current_execution_id,
        set_active_coordination,
    )
    from agentcore.runtime.engine import ReactLoopOut, react_loop
    from agentcore.runtime.events import FinishReason
    from agentcore.tools.protocol import ToolContext
    from agentcore.tools.registry import ToolRegistry
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace
    from tests.llm_helpers import make_profile_params

    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 2.0)
    # This test drives the ladder by posting the *next* event from inside each CEO
    # round, so per-event wakes are load-bearing — disable progress batching here
    # (batching itself is covered by dedicated unit tests).
    monkeypatch.setattr(coord_wait, "_MERGE_WINDOW_S", 0.0)
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-silent", total_workers=2)
    set_active_coordination(session)
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ESCALATION,
            payload={"run_id": "r1", "role": "研究员", "question": "范围？"},
        )
    )

    class _SilentListenCeo:
        """波内两轮静默监听（不产出任何 chunk）；all_completed 注入后才写终稿。"""

        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request):  # noqa: ANN001
            self.calls += 1
            all_done = any(
                m.role == "user" and m.content and "all_completed" in m.content
                for m in request.messages
            )
            if all_done:
                yield LLMChunk(delta_content="最终合成：两路产出一致，按方案 A 定稿。")
                return
            # 静默轮顺手把下一批事件排进队列，供下一轮注入。
            if self.calls == 1:
                session.post(
                    CoordinationEvent(
                        kind=CoordinationEventKind.ESCALATION,
                        payload={"run_id": "r2", "role": "写手", "question": "语气？"},
                    )
                )
            elif self.calls == 2:
                session.post(
                    CoordinationEvent(
                        kind=CoordinationEventKind.ALL_COMPLETED,
                        payload={"completed": 2, "total": 2},
                    )
                )

    ceo_llm = _SilentListenCeo()
    finish: list[FinishReason] = []
    exec_token = current_execution_id.set("e-silent")
    try:
        content, _reasoning, _usage, rounds = await react_loop(
            messages=[LLMMessage(role="user", content="并行做 A 和 B")],
            llm=ceo_llm,
            tools=ToolRegistry(),
            sink=EventSink(),
            tool_context=ToolContext.create(
                execution_id="e-silent",
                run_id="cap",
                agent_id="cap",
                backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
                user_id="u",
            ),
            profile=make_profile_params(max_rounds=8),
            turn_model="m",
            run_id="cap",
            role="captain",
            approval_gate=None,
            out=ReactLoopOut(finish_override=finish),
        )
    finally:
        clear_active_coordination("e-silent")
        current_execution_id.reset(exec_token)

    assert ceo_llm.calls == 3
    assert "最终合成" in content
    assert rounds == 3
    assert finish == []  # 无 DEGRADED——静默监听轮未进空响应梯子


async def test_concurrent_sessions_isolated_by_execution_id():
    """棘轮：两个不同 execution_id 的并发 CoordinationSession 互不串扰。"""
    from agentcore.runtime.coordination.session import (
        current_execution_id,
        set_active_coordination,
    )

    clear_active_coordination()
    a = CoordinationSession(execution_id="exec-iso-a", total_workers=2)
    b = CoordinationSession(execution_id="exec-iso-b", total_workers=3)
    set_active_coordination(a)
    set_active_coordination(b)

    assert active_coordination("exec-iso-a") is a
    assert active_coordination("exec-iso-b") is b
    assert active_coordination("exec-iso-a") is not active_coordination("exec-iso-b")

    a.update_draft("草稿 A")
    b.update_draft("草稿 B")
    a.post(
        CoordinationEvent(
            kind=CoordinationEventKind.WORKER_COMPLETED,
            payload={"run_id": "wa"},
        )
    )
    b.post(
        CoordinationEvent(
            kind=CoordinationEventKind.NOTE_POSTED,
            payload={"run_id": "wb", "text": "note-b"},
        )
    )

    assert a.draft == "草稿 A"
    assert b.draft == "草稿 B"
    assert active_coordination("exec-iso-a").draft == "草稿 A"
    assert active_coordination("exec-iso-b").draft == "草稿 B"

    ev_a = a.drain_nowait()
    ev_b = b.drain_nowait()
    assert len(ev_a) == 1 and ev_a[0].kind is CoordinationEventKind.WORKER_COMPLETED
    assert len(ev_b) == 1 and ev_b[0].kind is CoordinationEventKind.NOTE_POSTED
    assert a.drain_nowait() == []
    assert b.drain_nowait() == []

    # ContextVar resolves to the last set_active in this context (b).
    assert current_execution_id.get() == "exec-iso-b"
    assert active_coordination() is b

    clear_active_coordination("exec-iso-a")
    assert active_coordination("exec-iso-a") is None
    assert active_coordination("exec-iso-b") is b
    assert b.draft == "草稿 B"

    clear_active_coordination("exec-iso-b")
    assert active_coordination("exec-iso-b") is None
    clear_active_coordination()


async def test_checkpoint_batch_skips_coordination_for_durable_plan_review():
    """B1：含 checkpoint_after 且闸开 → 不进协调，回落经典 durable plan_review。"""
    from agentcore.core.types import ToolEffect
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.suspension import TurnSuspension, captain_transcript
    from tests.delegate.conftest import CKPT_DAG, tool_durable

    clear_active_coordination()
    registry = InteractionRegistry()
    sink = EventSink()
    saved: list[TurnSuspension] = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    t = tool_durable(Provider(["S1OUT", "S2OUT"]), sink, registry, _save, _drop)
    transcript = [
        LLMMessage(role="user", content="原始请求"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_del",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
        ),
    ]
    token = captain_transcript.set(transcript)
    try:
        # Default coordinate=True would enter coordination without B1; gate must skip.
        result = await t.execute({"tasks": CKPT_DAG}, ctx())
    finally:
        captain_transcript.reset(token)

    assert result.effect is ToolEffect.SUSPEND
    assert "团队已启动" not in (result.output or "")
    assert active_coordination("e") is None
    assert len(saved) == 1
    assert any(e.type is EventType.PLAN_REVIEW_REQUIRED for e in sink._history)


async def test_coordination_mid_checkpoint_still_boundary_yields():
    """防御：已在协调态时（如 replan 中途加把关）checkpoint 仍 BOUNDARY_YIELD。"""
    from agentcore.runtime.coordination.host import try_start_coordination
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        set_active_coordination,
    )
    from agentcore.runtime.runs import build_run_plan
    from agentcore.runtime.suspension import TurnSuspension
    from tests.delegate.conftest import CKPT_DAG, tool_durable

    clear_active_coordination()
    registry = InteractionRegistry()
    sink = EventSink()
    saved: list[TurnSuspension] = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    t = tool_durable(Provider(["S1OUT", "S2OUT"]), sink, registry, _save, _drop)
    plan, errors = build_run_plan(CKPT_DAG, id_prefix="del_mid_", parent_run_id="CEO", depth=1)
    assert not errors
    # Already-active session bypasses the entry gate (resume / mid-batch defense).
    session = CoordinationSession(execution_id="e", total_workers=len(plan.nodes))
    set_active_coordination(session)
    started = try_start_coordination(
        t,
        plan,
        execution_id="e",
        seed_completed=None,
        seed_notes=None,
        complexity_hint="standard",
        call_idx=1,
        coordinate=True,
        session=session,
    )
    assert started is not None
    assert "团队已启动" in started.output
    assert session.drive_task is not None
    await asyncio.wait_for(session.drive_task, timeout=10)

    assert saved == [], "协调态 checkpoint 不得 persist TurnSuspension"
    events = session.drain_nowait()
    kinds = [e.kind for e in events]
    assert CoordinationEventKind.BOUNDARY_YIELD in kinds
    byield = next(e for e in events if e.kind is CoordinationEventKind.BOUNDARY_YIELD)
    assert byield.payload.get("reason") == "checkpoint"
    assert not any(e.type is EventType.PLAN_REVIEW_REQUIRED for e in sink._history)
    clear_active_coordination("e")


async def test_classic_checkpoint_still_durable_when_not_coordinating():
    """经典阻塞 path（coordinate=false）仍 durable plan_review 挂起即收口。"""
    from agentcore.core.types import ToolEffect
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.suspension import TurnSuspension, captain_transcript
    from tests.delegate.conftest import CKPT_DAG, tool_durable

    clear_active_coordination()
    registry = InteractionRegistry()
    sink = EventSink()
    saved: list[TurnSuspension] = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    t = tool_durable(Provider(["S1OUT", "S2OUT"]), sink, registry, _save, _drop)
    transcript = [
        LLMMessage(role="user", content="原始请求"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_del",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
        ),
    ]
    token = captain_transcript.set(transcript)
    try:
        result = await t.execute({"tasks": CKPT_DAG, "coordinate": False}, ctx())
    finally:
        captain_transcript.reset(token)

    assert result.effect is ToolEffect.SUSPEND
    assert len(saved) == 1
    assert any(e.type is EventType.PLAN_REVIEW_REQUIRED for e in sink._history)


def test_coordination_budget_scales_with_batch_size():
    assert coordination_budget_for_batch(2) == 8  # small → floor 8
    assert coordination_budget_for_batch(4) == 8  # 4+4=8
    assert coordination_budget_for_batch(5) == 9  # nodes+4
    assert coordination_budget_for_batch(12) == 16  # 12+4=16
    assert coordination_budget_for_batch(20) == 16  # cap


def test_first_turn_all_completed_inject_asks_final_synthesis():
    """首回合 ALL_COMPLETED：当场写终稿；禁止与「勿做最终合成」同块并存。"""
    from agentcore.runtime.coordination.inject import format_coordination_events

    session = CoordinationSession(execution_id="e", total_workers=2)
    product = "【队员成品】调研报告正文……"
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 2, "total": 2, "output": product},
            )
        ],
    )
    assert "勿做最终合成" not in text
    assert "本回合勿做最终合成" not in text
    assert "本回合可见面只留人已派出" not in text
    assert "可见收口由系统收口回合完成" not in text
    assert "全部完成后做最终合成" not in text
    assert "报告本波结果" in text
    assert "本波结果按终稿纪律向用户交代" in text
    assert "团队成品" in text
    assert product in text


def test_note_attached_inject_visible_close_is_structural():
    """只认非空 content_delta + attached_inject；空串 / harvest 收口不得打标。"""
    session = CoordinationSession(execution_id="e-note", total_workers=1)
    session.note_attached_inject_visible_close("终稿")
    assert session.attached_inject_visible_close is False

    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    session.note_attached_inject_visible_close("   ")
    assert session.attached_inject_visible_close is False
    session.note_attached_inject_visible_close("交付正文")
    assert session.attached_inject_visible_close is True
    session.clear_attached_inject_visible_close()
    assert session.attached_inject_visible_close is False
    session.note_attached_inject_visible_close("重写终稿")
    assert session.attached_inject_visible_close is True

    harvest = CoordinationSession(execution_id="e-h", total_workers=1)
    harvest.all_completed_injected = True
    harvest.harvest_closing = True
    harvest.mark_settled("harvest")
    harvest.note_attached_inject_visible_close("收口正文")
    assert harvest.attached_inject_visible_close is False


def test_all_completed_inject_carries_output_without_unconditional_audit():
    from agentcore.runtime.coordination.inject import format_coordination_events

    session = CoordinationSession(execution_id="e", total_workers=3)
    session.harvest_closing = True
    product = "【队员成品】调研报告正文……"
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 3, "total": 3, "output": product},
            )
        ],
    )
    assert product in text
    assert "团队成品" in text
    assert "先派审计再收尾" not in text


def test_all_completed_inject_names_accepted_files():
    from agentcore.conversation.execution_harvest import format_harvest_user_text
    from agentcore.runtime.coordination.inject import format_coordination_events

    paths = ["工作稿/报告.md", "工作稿/附录.md"]
    facts = {"nodes": [], "files": paths, "outstanding_tool_failures": []}
    product = "【队员成品】调研报告正文……"
    session = CoordinationSession(execution_id="e", total_workers=2)
    session.harvest_closing = True
    session.harvest_user_facts = facts
    payload = {
        "completed": 2,
        "total": 2,
        "output": product,
        "user_facts": facts,
    }
    text = format_coordination_events(
        session,
        [CoordinationEvent(kind=CoordinationEventKind.ALL_COMPLETED, payload=payload)],
    )
    assert "已接受落盘" in text
    assert "`工作稿/报告.md`" in text
    assert "`工作稿/附录.md`" in text
    assert "禁止整段粘贴本清单当产物卡" in text

    session._harvest_stash.append(
        CoordinationEvent(kind=CoordinationEventKind.ALL_COMPLETED, payload=payload)
    )
    user_text = format_harvest_user_text(session)
    assert "已接受落盘" not in user_text
    assert "禁止整段粘贴本清单当产物卡" not in user_text


def test_all_completed_inject_skips_audit_nudge_for_brief_and_writing():
    from agentcore.runtime.coordination.inject import format_coordination_events
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable, RunSpec

    brief = CoordinationSession(execution_id="e-brief", total_workers=2)
    brief.harvest_closing = True
    brief.live_plan = RunPlan(
        nodes=[
            RunSpec(
                run_id="b0",
                task="摸底",
                role="方向专员",
                deliverable=Deliverable(
                    form="files",
                    artifacts=["AgentCore/文档/research/甲方向笔记.md"],
                ),
            ),
            RunSpec(
                run_id="b1",
                task="摸底",
                role="方向专员",
                deliverable=Deliverable(
                    form="files",
                    artifacts=["AgentCore/文档/research/乙方向笔记.md"],
                ),
            ),
        ]
    )
    brief_text = format_coordination_events(
        brief,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 2, "total": 2, "playbook": "parallel_brief"},
            )
        ],
    )
    assert "先派审计再收尾" not in brief_text

    writing = CoordinationSession(execution_id="e-write", total_workers=1)
    writing.harvest_closing = True
    writing.live_plan = RunPlan(
        nodes=[
            RunSpec(
                run_id="w0",
                task="成文",
                role="撰稿人",
                deliverable=Deliverable(
                    form="files",
                    artifacts=["AgentCore/文档/research/报告.md"],
                ),
            )
        ]
    )
    writing_text = format_coordination_events(
        writing,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 1, "total": 1},
            )
        ],
    )
    assert "先派审计再收尾" not in writing_text


def test_all_completed_inject_keeps_audit_nudge_for_audit_wave():
    from agentcore.runtime.coordination.inject import format_coordination_events
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable, RunSpec

    session = CoordinationSession(execution_id="e-audit", total_workers=1)
    session.harvest_closing = True
    session.live_plan = RunPlan(
        nodes=[
            RunSpec(
                run_id="a0",
                task="审计",
                role="代码审计员",
                deliverable=Deliverable(
                    form="files",
                    artifacts=["AgentCore/文档/reviews/code-audit.md"],
                    code_audit_gate=True,
                ),
            )
        ]
    )
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 1, "total": 1, "playbook": "code_audit"},
            )
        ],
    )
    assert "独立审计" in text
    assert "先派审计再收尾" in text

    by_playbook = CoordinationSession(execution_id="e-rr", total_workers=1)
    by_playbook.harvest_closing = True
    rr_text = format_coordination_events(
        by_playbook,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 1, "total": 1, "playbook": "research_report"},
            )
        ],
    )
    assert "先派审计再收尾" in rr_text


def test_inject_carries_final_synthesis_discipline():
    # 终稿纪律（B4·协调出口）：footer 与 all_completed 都提醒——交付物在前、过程简述
    # 从简、协调事件 / 名册 / escalation 原文 / 合成草稿不整段进终稿、未交付产物显式列出。
    from agentcore.runtime.coordination.inject import format_coordination_events

    session = CoordinationSession(execution_id="e", total_workers=2)
    session.harvest_closing = True
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 2, "total": 2},
            )
        ],
    )
    assert "【终稿纪律】" in text  # footer（每次注入都带）
    assert "交付物在前" in text
    assert "过程简述从简" in text
    assert "至多一段" not in text
    assert "禁止整段粘进终稿" in text
    assert "未交付的承诺产物" in text
    assert "终稿纪律】写" in text  # all_completed 分支的强化提醒
    assert "报告本波结果" in text
    assert "活没干完就接着干" in text
    assert "然后退出协调" not in text


def test_all_completed_inject_without_output_skips_audit_unless_review_wave():
    from agentcore.runtime.coordination.inject import format_coordination_events
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    session = CoordinationSession(execution_id="e", total_workers=2)
    session.harvest_closing = True
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 2, "total": 2},
            )
        ],
    )
    assert "团队已全部结束" in text
    assert "先派审计再收尾" not in text
    assert "团队成品" not in text

    review = CoordinationSession(execution_id="e-rev", total_workers=2)
    review.harvest_closing = True
    review.live_plan = RunPlan(
        nodes=[
            RunSpec(run_id="w", task="写", role="撰稿人"),
            RunSpec(run_id="r", task="审", role="学术审校员"),
        ]
    )
    review_text = format_coordination_events(
        review,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 2, "total": 2},
            )
        ],
    )
    assert "先派审计再收尾" in review_text


def test_all_completed_criteria_unmet_inject_steers_reuse_not_respawn():
    from agentcore.runtime.coordination.inject import format_coordination_events

    session = CoordinationSession(execution_id="e", total_workers=2)
    session.harvest_closing = True
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={
                    "completed": 2,
                    "total": 2,
                    "failed": 0,
                    "criteria_met": False,
                },
            )
        ],
    )
    assert "批次验收未满足" in text
    assert "调度已结束" in text
    assert "勿再启同服" in text
    assert "复用" in text or "只补浏览器" in text
    assert "团队已全部结束" not in text
    assert "活没干完就接着干" not in text


def test_harvest_inject_close_line_differs_by_outcome():
    from agentcore.runtime.coordination.inject import format_coordination_events

    ok = CoordinationSession(execution_id="e-ok", total_workers=1)
    ok.harvest_closing = True
    ok_text = format_coordination_events(
        ok,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 1, "total": 1},
            )
        ],
    )
    assert "活没干完就接着干" in ok_text
    assert "然后退出协调" not in ok_text

    fail = CoordinationSession(execution_id="e-fail", total_workers=2)
    fail.harvest_closing = True
    fail.failed_run_ids = {"b"}
    fail_text = format_coordination_events(
        fail,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 2, "total": 2, "failed": 1},
            )
        ],
    )
    assert "不要把失败当成功继续铺开" in fail_text
    assert "活没干完就接着干" not in fail_text

    cancelled = CoordinationSession(execution_id="e-c", total_workers=1)
    cancelled.harvest_closing = True
    cancelled_text = format_coordination_events(
        cancelled,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.DRIVE_CANCELLED,
                payload={"completed": 0, "total": 1},
            )
        ],
    )
    assert "不要接着派活" in cancelled_text
    assert "活没干完就接着干" not in cancelled_text

    paused = CoordinationSession(execution_id="e-soft", total_workers=1)
    paused.harvest_closing = True
    paused.soft_stop = True
    paused_text = format_coordination_events(
        paused,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 1, "total": 1},
            )
        ],
    )
    assert "不要自行接着干" in paused_text
    assert "请示用户而暂停" in paused_text
    assert "报告本波结果" not in paused_text
    assert "活没干完就接着干" not in paused_text


def test_checkpoint_boundary_yield_instructs_ask_user():
    from agentcore.runtime.coordination.inject import format_coordination_events

    session = CoordinationSession(execution_id="e", total_workers=2)
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.BOUNDARY_YIELD,
                payload={"reason": "checkpoint", "brief": "提纲已出"},
            )
        ],
    )
    assert "boundary_yield（checkpoint）" in text
    assert "ask_user" in text
    assert "不得自行替用户决定" in text
    assert "提纲已出" in text


async def test_snapshot_drain_wakes_waiter_with_all_completed():
    """drive finally snapshot must not leave CEO wait_events hanging until timeout.

    Race: waiter blocked on ``queue.get``; host ``record_coordination_snapshot`` drains
    ALL_COMPLETED into ``_pending``. Drain must wake the waiter so the event is
    delivered promptly (not after ``_COORD_WAIT_TIMEOUT_S``).
    """
    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-snap-wake", total_workers=4)
    wait_task = asyncio.create_task(session.wait_events(timeout=120.0))
    # Let the waiter park on queue.get before we post + snapshot-drain.
    await asyncio.sleep(0.05)
    assert not wait_task.done()

    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 4, "total": 4},
        )
    )
    # Same path as host.py finally → record_coordination_snapshot → session.snapshot().
    snap = session.snapshot()
    assert any(e.get("kind") == "all_completed" for e in snap.pending_events)

    events = await asyncio.wait_for(wait_task, timeout=1.0)
    assert any(e.kind is CoordinationEventKind.ALL_COMPLETED for e in events)
    clear_active_coordination()


def test_host_backfill_guard_still_posts_when_queue_empty():
    """Invariant guard unit: if somehow nothing is in-flight, host still backfills once."""
    from agentcore.runtime.coordination.host import (
        _ensure_terminal_all_completed,
        _has_all_completed_in_flight,
    )

    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-backfill", total_workers=2)
    session.completed_run_ids = {"w1", "w2"}
    assert not _has_all_completed_in_flight(session)
    assert _ensure_terminal_all_completed(session, output="缺口说明") is True
    assert _has_all_completed_in_flight(session)
    # Idempotent — second call must not double-post.
    assert _ensure_terminal_all_completed(session, output="again") is False
    events = session.drain_nowait()
    kinds = [e.kind for e in events]
    assert kinds.count(CoordinationEventKind.ALL_COMPLETED) == 1
    assert "缺口说明" in (events[0].payload.get("output") or "")
    clear_active_coordination()


async def test_wait_shortcircuit_guard_when_terminal_missing(monkeypatch):
    """Invariant guard: empty queue after team done must not idle-wait 120s."""
    import agentcore.runtime.coordination.wait as coord_wait
    from agentcore.runtime.coordination.session import (
        current_execution_id,
        set_active_coordination,
    )
    from agentcore.runtime.coordination.wait import await_coordination_injection

    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 30.0)
    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-short", total_workers=2)
    session.completed_run_ids = {"w1", "w2"}
    session.drive_task = asyncio.get_running_loop().create_future()
    session.drive_task.set_result(None)
    set_active_coordination(session)
    token = current_execution_id.set("exec-short")
    try:
        t0 = asyncio.get_running_loop().time()
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=2.0)
        elapsed = asyncio.get_running_loop().time() - t0
    finally:
        current_execution_id.reset(token)
        clear_active_coordination()

    assert elapsed < 2.0
    assert len(msgs) == 1
    assert "all_completed" in (msgs[0].content or "")
    assert "团队已全部结束" in (msgs[0].content or "")
    assert "报告本波结果" in (msgs[0].content or "")
    assert "勿做最终合成" not in (msgs[0].content or "")
    assert session.active is False
    assert session.all_completed_injected is True


def test_synthetic_all_completed_stamps_cancel_and_fail_flags():
    """Bag-full shortcircuit must carry cancel / fail flags; success stays flag-less."""
    from agentcore.runtime.coordination.wait import _synthetic_all_completed

    ok = CoordinationSession(execution_id="exec-short-ok", total_workers=2)
    ok.completed_run_ids = {"w1", "w2"}
    ok_ev = _synthetic_all_completed(ok)
    assert ok_ev.kind is CoordinationEventKind.ALL_COMPLETED
    assert ok_ev.payload.get("reason") == "team_done_shortcircuit"
    assert "cancelled" not in ok_ev.payload
    assert "failed" not in ok_ev.payload
    assert "criteria_met" not in ok_ev.payload

    cancelled = CoordinationSession(execution_id="exec-short-cancel", total_workers=1)
    cancelled.completed_run_ids = {"never-ran"}
    cancelled.cancel_ids = {"never-ran"}
    cancel_ev = _synthetic_all_completed(cancelled)
    assert cancel_ev.kind is CoordinationEventKind.ALL_COMPLETED
    assert cancel_ev.payload.get("cancelled") is True
    assert cancel_ev.payload.get("completed") == 1
    assert cancel_ev.payload.get("total") == 1
    assert "criteria_met" not in cancel_ev.payload

    failed = CoordinationSession(execution_id="exec-short-fail", total_workers=2)
    failed.completed_run_ids = {"ok", "boom"}
    failed.failed_run_ids = {"boom"}
    fail_ev = _synthetic_all_completed(failed)
    assert fail_ev.kind is CoordinationEventKind.ALL_COMPLETED
    assert fail_ev.payload.get("failed") == 1
    assert fail_ev.payload.get("criteria_met") is False
    assert "cancelled" not in fail_ev.payload


async def test_wait_shortcircuit_cancelled_bag_does_not_claim_all_done(monkeypatch):
    """Field shape: 1/1 bag filled by unsettled cancel must not say「团队已全部结束」."""
    import agentcore.runtime.coordination.wait as coord_wait
    from agentcore.runtime.coordination.session import (
        current_execution_id,
        set_active_coordination,
    )
    from agentcore.runtime.coordination.wait import await_coordination_injection

    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 30.0)
    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-short-cancel-wait", total_workers=1)
    session.completed_run_ids = {"never-ran"}
    session.cancel_ids = {"never-ran"}
    session.harvest_closing = True
    session.drive_task = asyncio.get_running_loop().create_future()
    session.drive_task.set_result(None)
    set_active_coordination(session)
    token = current_execution_id.set("exec-short-cancel-wait")
    try:
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=2.0)
    finally:
        current_execution_id.reset(token)
        clear_active_coordination()

    text = msgs[0].content or ""
    assert "all_completed" in text
    assert "调度中断" in text
    assert "团队已全部结束" not in text
    assert session.active is False
    assert session.all_completed_injected is True


async def test_wait_shortcircuit_failed_bag_does_not_claim_all_done(monkeypatch):
    """Failed ids in a full bag must not inject a flag-less「团队已全部结束」."""
    import agentcore.runtime.coordination.wait as coord_wait
    from agentcore.runtime.coordination.session import (
        current_execution_id,
        set_active_coordination,
    )
    from agentcore.runtime.coordination.wait import await_coordination_injection

    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 30.0)
    clear_active_coordination()
    session = CoordinationSession(execution_id="exec-short-fail-wait", total_workers=2)
    session.completed_run_ids = {"ok", "boom"}
    session.failed_run_ids = {"boom"}
    session.harvest_closing = True
    session.drive_task = asyncio.get_running_loop().create_future()
    session.drive_task.set_result(None)
    set_active_coordination(session)
    token = current_execution_id.set("exec-short-fail-wait")
    try:
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=2.0)
    finally:
        current_execution_id.reset(token)
        clear_active_coordination()

    text = msgs[0].content or ""
    assert "all_completed" in text
    assert "团队已全部结束" not in text
    assert "失败" in text
    assert session.active is False


async def test_retired_criteria_kind_still_posts_all_completed_without_host_backfill(
    monkeypatch,
):
    """S3: legacy completion_criteria ignored; ALL_COMPLETED still posts; host ensure no-op."""
    import agentcore.runtime.coordination.host as coord_host
    from agentcore.tools.builtin.delegate import DelegateTool
    from agentcore.tools.registry import ToolRegistry
    from tests.delegate.conftest import local_ctx

    backfill_results: list[bool] = []
    original = coord_host._ensure_terminal_all_completed

    def _spy(session, *, output: str = "") -> bool:
        posted = original(session, output=output)
        backfill_results.append(posted)
        return posted

    monkeypatch.setattr(coord_host, "_ensure_terminal_all_completed", _spy)
    clear_active_coordination()
    t = DelegateTool(
        llm=Provider(["AOUT", "BOUT"]),
        sink=EventSink(),
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=local_ctx(),
        folder_id="test_birth",
        approval_gate=None,
    )
    result = await t.execute(
        {
            "tasks": [
                {
                    "role": "工程师",
                    "task": "修并验绿",
                    "deliverable": {"form": "files"},
                    "tools": ["file_write", "code_execute", "test_run"],
                },
                {
                    "role": "测试",
                    "task": "跑通验证",
                    "deliverable": {"form": "files"},
                    "tools": ["file_write", "code_execute", "test_run"],
                },
            ],
            "coordinate": True,
            "completion_criteria": {
                "type": "code_verified",
                "verify_command": "pytest -q",
            },
        },
        local_ctx(),
    )
    assert result.success is True
    assert "团队已启动" in result.output
    session = active_coordination("e")
    assert session is not None
    assert session.drive_task is not None
    await asyncio.wait_for(session.drive_task, timeout=10)
    events = session.drain_nowait()
    kinds = [e.kind for e in events]
    assert CoordinationEventKind.ALL_COMPLETED in kinds
    all_done = next(e for e in events if e.kind is CoordinationEventKind.ALL_COMPLETED)
    assert "完成条件未满足" not in (all_done.payload.get("output") or "")
    assert backfill_results == [False]
    clear_active_coordination("e")


async def test_retired_criteria_kind_wait_drains_without_shortcircuit(monkeypatch):
    """S3: ignored kind still leaves ALL_COMPLETED in queue — wait drains, no shortcircuit."""
    import agentcore.runtime.coordination.wait as coord_wait
    from agentcore.runtime.coordination.session import (
        current_execution_id,
        set_active_coordination,
    )
    from agentcore.runtime.coordination.wait import await_coordination_injection
    from agentcore.tools.builtin.delegate import DelegateTool
    from agentcore.tools.registry import ToolRegistry
    from tests.delegate.conftest import local_ctx

    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 30.0)
    clear_active_coordination()
    t = DelegateTool(
        llm=Provider(["AOUT", "BOUT"]),
        sink=EventSink(),
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=local_ctx(),
        folder_id="test_birth",
        approval_gate=None,
    )
    await t.execute(
        {
            "tasks": [
                {
                    "role": "工程师",
                    "task": "修并验绿",
                    "deliverable": {"form": "files"},
                    "tools": ["file_write", "code_execute", "test_run"],
                },
                {
                    "role": "测试",
                    "task": "跑通验证",
                    "deliverable": {"form": "files"},
                    "tools": ["file_write", "code_execute", "test_run"],
                },
            ],
            "coordinate": True,
            "completion_criteria": {
                "type": "code_verified",
                "verify_command": "pytest -q",
            },
        },
        local_ctx(),
    )
    session = active_coordination("e")
    assert session is not None
    await asyncio.wait_for(session.drive_task, timeout=10)
    # Precondition: terminal event already queued by drive (not via wait shortcircuit).
    pending = session.drain_nowait()
    assert any(e.kind is CoordinationEventKind.ALL_COMPLETED for e in pending)
    # Re-queue so await_coordination_injection can drain normally.
    for ev in pending:
        session.post(ev)
    set_active_coordination(session)
    token = current_execution_id.set(session.execution_id)
    try:
        # Non-empty drain must never consult the shortcircuit arm.
        monkeypatch.setattr(
            coord_wait,
            "_drive_exhausted",
            lambda s: (_ for _ in ()).throw(
                AssertionError("shortcircuit arm must not run when ALL_COMPLETED queued")
            ),
        )
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=2.0)
    finally:
        current_execution_id.reset(token)
        clear_active_coordination("e")

    assert len(msgs) == 1
    assert "all_completed" in (msgs[0].content or "")
    assert "完成条件未满足" not in (msgs[0].content or "")
    assert session.active is False


async def _await_ceo_wake(monkeypatch, session, *, timeout_s: float = 2.0):
    """Bind session as active and measure await_coordination_injection latency."""
    import agentcore.runtime.coordination.wait as coord_wait
    from agentcore.runtime.coordination.session import (
        current_execution_id,
        set_active_coordination,
    )
    from agentcore.runtime.coordination.wait import await_coordination_injection

    # If the invariant leaks, fail fast instead of hanging near production 120s.
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 30.0)
    set_active_coordination(session)
    token = current_execution_id.set(session.execution_id)
    try:
        t0 = asyncio.get_running_loop().time()
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=timeout_s)
        elapsed = asyncio.get_running_loop().time() - t0
    finally:
        current_execution_id.reset(token)
    return msgs, elapsed


async def test_partial_failure_wakes_ceo_wait_promptly(monkeypatch):
    """Invariant: partial_failure_stashed posts ALL_COMPLETED → CEO wait wakes in seconds."""
    from agentcore.runtime.runs.types import RunPhase, RunState

    async def _exec(spec, completed):  # noqa: ANN001
        if spec.role == "写手":
            return RunState(phase=RunPhase.FAILED, error="boom", content="")
        return RunState(phase=RunPhase.COMPLETED, content=f"{spec.role}_OK")

    monkeypatch.setattr("agentcore.runtime.runs.build_agent_executor", lambda **kw: _exec)
    clear_active_coordination()
    t = tool(Provider([]))
    result = await t.execute(
        {
            "tasks": [
                {"role": "研究员", "task": "做A"},
                {"role": "写手", "task": "做B"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert result.success is True
    assert "团队已启动" in result.output
    session = active_coordination("e")
    assert session is not None
    await asyncio.wait_for(session.drive_task, timeout=10)
    assert t._supervised is not None

    msgs, elapsed = await _await_ceo_wake(monkeypatch, session)
    clear_active_coordination("e")

    assert elapsed < 2.0, f"CEO wait must not idle-timeout; elapsed={elapsed:.3f}s"
    assert len(msgs) == 1
    assert "all_completed" in (msgs[0].content or "")
    assert session.active is False
    assert session.all_completed_injected is True


async def test_all_success_wakes_ceo_wait_promptly(monkeypatch):
    """Invariant: full success posts ALL_COMPLETED → CEO wait wakes in seconds."""
    clear_active_coordination()
    t = tool(Provider(["AOUT", "BOUT"]))
    result = await t.execute(
        {
            "tasks": [
                {"role": "研究员", "task": "做A"},
                {"role": "写手", "task": "做B"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert result.success is True
    session = active_coordination("e")
    assert session is not None
    await asyncio.wait_for(session.drive_task, timeout=10)

    msgs, elapsed = await _await_ceo_wake(monkeypatch, session)
    clear_active_coordination("e")

    assert elapsed < 2.0, f"CEO wait must not idle-timeout; elapsed={elapsed:.3f}s"
    assert len(msgs) == 1
    assert "all_completed" in (msgs[0].content or "")
    assert session.active is False
    assert session.all_completed_injected is True


async def test_merge_rearm_wakes_ceo_wait_promptly(monkeypatch):
    """Invariant: after drive exit + merge rearm, new terminal still wakes CEO promptly.

    Mirrors the production sequence: first batch reaches terminal (ALL_COMPLETED in
    queue), secondary delegate rearm drops it (``dropped_all_completed``), then the
    appended worker finishes and must re-post a wait-consumable terminal.
    """
    clear_active_coordination()
    t = tool(Provider(["AOUT", "BOUT", "COUT"]))
    first = await t.execute(
        {
            "tasks": [
                {"role": "研究员", "task": "做A"},
                {"role": "写手", "task": "做B"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert first.success is True
    session = active_coordination("e")
    assert session is not None
    first_drive = session.drive_task
    assert first_drive is not None
    await asyncio.wait_for(first_drive, timeout=10)
    # Precondition: first batch left a terminal event (what rearm must drop).
    pending = session.drain_nowait()
    assert any(e.kind is CoordinationEventKind.ALL_COMPLETED for e in pending)
    for ev in pending:
        session.post(ev)

    second = await t.execute(
        {"tasks": [{"role": "补充", "task": "做C"}], "coordinate": True},
        ctx(),
    )
    assert second.success is True
    assert "队员已追加" in second.output
    assert session.total_workers == 3
    assert session.drive_task is not None
    assert session.drive_task is not first_drive
    await asyncio.wait_for(session.drive_task, timeout=10)

    msgs, elapsed = await _await_ceo_wake(monkeypatch, session)
    clear_active_coordination("e")

    assert elapsed < 2.0, f"CEO wait must not idle-timeout; elapsed={elapsed:.3f}s"
    assert len(msgs) == 1
    assert "all_completed" in (msgs[0].content or "")
    assert session.active is False
    assert session.all_completed_injected is True


def test_release_prefers_harvest_after_attached_inject(monkeypatch):
    """注入已 settle 仍须 harvest：等待气泡不是用户可见收口。"""
    from structlog.testing import capture_logs

    session = CoordinationSession(
        execution_id="e-harv-inject",
        total_workers=2,
        conversation_id="conv-harv-inject",
    )
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 2, "total": 2},
        )
    )
    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    set_active_coordination(session)

    called: dict[str, object] = {}

    def _fake_finish(s: CoordinationSession) -> None:
        called["session"] = s
        s.harvest_scheduled = True
        s.mark_settled("harvest")

    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.finish_detached_coordination",
        _fake_finish,
    )

    with capture_logs() as logs:
        release_turn_coordination("e-harv-inject")
    assert called.get("session") is session
    assert session.turn_attached is False
    assert session.harvest_scheduled is True
    assert session.settled_via == "harvest"
    assert any(e.get("event") == "coordination.release_prefers_harvest" for e in logs)


def test_release_keeps_session_when_harvest_already_scheduled():
    """harvest 已在飞：release 只交还附着，禁止裸 clear 掉收口。"""
    session = CoordinationSession(
        execution_id="e-harv-inflight",
        total_workers=1,
        conversation_id="conv-harv-inflight",
    )
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 1, "total": 1},
        )
    )
    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    session.harvest_scheduled = True
    set_active_coordination(session)

    release_turn_coordination("e-harv-inflight")
    assert active_coordination("e-harv-inflight") is session
    assert session.turn_attached is False
    assert session.harvest_scheduled is True


def test_release_prefers_harvest_when_terminal_unsettled(monkeypatch):
    """drive 已结束 + terminal_posted 未 settle → release 走 harvest，不裸 clear。"""
    from structlog.testing import capture_logs

    session = CoordinationSession(
        execution_id="e-harv",
        total_workers=2,
        conversation_id="conv-harv",
    )
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 2, "total": 2},
        )
    )
    assert session.terminal_posted is True
    set_active_coordination(session)

    called: dict[str, object] = {}

    def _fake_finish(s: CoordinationSession) -> None:
        called["session"] = s
        s.harvest_scheduled = True
        s.mark_settled("harvest")

    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.finish_detached_coordination",
        _fake_finish,
    )

    with capture_logs() as logs:
        release_turn_coordination("e-harv")
    assert called.get("session") is session
    assert session.turn_attached is False
    assert session.harvest_scheduled is True
    assert session.settled_via == "harvest"
    assert any(e.get("event") == "coordination.release_prefers_harvest" for e in logs)


def test_check_terminal_settlement_logs_unsettled():
    """终态未 inject/harvest 时 clear 路径打 coordination.terminal_unsettled。"""
    from structlog.testing import capture_logs

    session = CoordinationSession(execution_id="e-unsettle", total_workers=1)
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 1, "total": 1},
        )
    )
    set_active_coordination(session)
    with capture_logs() as logs:
        clear_active_coordination("e-unsettle")
    assert any(e.get("event") == "coordination.terminal_unsettled" for e in logs)


def test_terminal_unsettled_post_detach_missing_durable_run_terminal():
    """detach 后 harvest 已 stamp settled_via，但宿主 journal 缺 worker 终态 → 仍告警。"""
    from structlog.testing import capture_logs

    session = CoordinationSession(
        execution_id="e-post-detach-journal",
        total_workers=2,
        conversation_id="c-post-detach-journal",
    )
    session.turn_attached = False
    session.completed_run_ids.update({"w1", "w2"})
    session.mark_settled("harvest")
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 2, "total": 2},
        )
    )
    stale = [
        {"kind": "run_started", "payload": {"run_id": "w1"}},
        {"kind": "run_failed", "payload": {"run_id": "w1"}},
        {"kind": "run_started", "payload": {"run_id": "w2"}},
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
    ]
    with capture_logs() as logs:
        session.check_terminal_settlement(journal_entries=stale)
    unsettle = [e for e in logs if e.get("event") == "coordination.terminal_unsettled"]
    assert unsettle
    assert unsettle[0].get("missing_run_ids") == ["w2"]


def test_terminal_settlement_ok_when_post_detach_journal_has_all_terminals():
    """post-detach journal 终态齐全时，即使已 harvest 也不再打 terminal_unsettled。"""
    from structlog.testing import capture_logs

    session = CoordinationSession(
        execution_id="e-post-detach-ok",
        total_workers=2,
        conversation_id="c-post-detach-ok",
    )
    session.turn_attached = False
    session.completed_run_ids.update({"w1", "w2"})
    session.mark_settled("harvest")
    complete = [
        {"kind": "run_failed", "payload": {"run_id": "w1"}},
        {"kind": "run_failed", "payload": {"run_id": "w2"}},
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
    ]
    with capture_logs() as logs:
        session.check_terminal_settlement(journal_entries=complete)
    assert not any(e.get("event") == "coordination.terminal_unsettled" for e in logs)

