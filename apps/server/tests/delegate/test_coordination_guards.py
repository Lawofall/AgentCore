"""Guards: idle-patrol activity check, isomorphic re-delegation, user_stop cascade."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from agentcore.runtime.coordination import wait as coord_wait
from agentcore.runtime.coordination.isomorphic import (
    is_isomorphic_redelegation,
    tasks_similar,
)
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    active_coordination,
    cancel_coordination_on_user_stop,
    clear_active_coordination,
    release_turn_coordination,
    set_active_coordination,
)
from agentcore.runtime.coordination.wait import await_coordination_injection
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPolicy, RunSpec
from agentcore.runtime.turn.interrupt import TurnInterruptReason, compose_interrupt_body
from agentcore.runtime.turn.runs import TurnRun, turn_runs


def _plan(*nodes: RunSpec) -> RunPlan:
    plan = RunPlan()
    for n in nodes:
        plan.add(n)
    return plan


# --- A: idle patrol activity -------------------------------------------------


def test_has_inflight_work_and_progress_summary():
    s = CoordinationSession(execution_id="e", total_workers=2)
    # Avoid arm_worker_timeout (needs a running loop); stamp registry directly.
    s._running_workers["w1"] = "研究员"
    s._running_workers["w2"] = "写手"
    s._worker_started_at["w1"] = s._worker_started_at["w2"] = __import__("time").monotonic()
    assert s.has_inflight_work() is False
    s.mark_worker_busy("w1", "llm")
    assert s.has_inflight_work() is True
    summary = s.worker_progress_summary()
    assert "研究员" in summary
    assert "LLM 调用中" in summary
    assert "写手" in summary
    s.clear_worker_busy("w1")
    assert s.has_inflight_work() is False
    # Minute-level verify must NOT block idle patrol / wall+0 CEO wake.
    s.mark_worker_busy("w1", "verify")
    assert s.has_inflight_work() is False
    assert s.has_verify_busy() is True
    assert "有界验证中" in s.worker_progress_summary()
    assert "cancel_worker" in s.worker_progress_summary()
    s.clear_worker_busy("w1")
    assert s.has_verify_busy() is False
    # Blocking escalate awaiting CEO: not short in-flight (same as verify).
    s.mark_worker_busy("w1", "arbitrate")
    assert s.has_inflight_work() is False
    assert s._busy_workers["w1"] == "arbitrate"
    assert "等待主管仲裁" in s.worker_progress_summary()
    s.clear_worker_busy("w1")


def test_worker_budget_facts_only_lists_stamped_numbers():
    """队员预算行只列引擎已盖章的数字，缺字段不编造。"""
    live = _plan(
        RunSpec(
            run_id="w1",
            role="研究员",
            task="调研",
            token_ceiling=4_000_000,
            max_rounds=56,
            policy=RunPolicy(timeout_s=1200),
        ),
        RunSpec(run_id="w2", role="写手", task="撰写"),
    )
    s = CoordinationSession(execution_id="e-budget", total_workers=2)
    s.live_plan = live
    s._running_workers["w1"] = "研究员"
    s._running_workers["w2"] = "写手"
    s._worker_started_at["w1"] = s._worker_started_at["w2"] = __import__("time").monotonic()
    assert s.worker_budget_facts("w1") == [
        "超时阈值 1200s",
        "token 顶 4000000",
    ]
    assert s.worker_budget_facts("w2") == []
    summary = s.worker_progress_summary()
    assert "token 顶 4000000" in summary
    assert "超时阈值 1200s" in summary
    assert "轮次上限" not in summary
    assert "已用" not in summary
    assert "已花" not in summary


def test_worker_budget_facts_use_live_spend_not_just_ceilings():
    """Executor-stamped used/tokens replace static caps; kind-only busy invents nothing."""
    live = _plan(
        RunSpec(
            run_id="w1",
            role="研究员",
            task="调研",
            token_ceiling=4_000_000,
            max_rounds=56,
            policy=RunPolicy(timeout_s=1200),
        )
    )
    s = CoordinationSession(execution_id="e-spend", total_workers=1)
    s.live_plan = live
    s._running_workers["w1"] = "研究员"
    s._worker_started_at["w1"] = __import__("time").monotonic()
    s.mark_worker_busy("w1", "llm")
    assert s.worker_budget_facts("w1") == [
        "超时阈值 1200s",
        "token 顶 4000000",
    ]
    s.mark_worker_busy(
        "w1",
        "llm",
        rounds_used=52,
        rounds_limit=56,
        tokens_spent=2_700_000,
    )
    assert s.worker_budget_facts("w1") == [
        "超时阈值 1200s",
        "已花 2700000/4000000",
    ]
    summary = s.worker_progress_summary()
    assert "已花 2700000/4000000" in summary
    assert "已用" not in summary
    assert "轮次上限" not in summary
    assert "token 顶 4000000" not in summary
    # 轮间 clear 必须保住数字，否则 idle 巡查刚好赶上空窗。
    s.clear_worker_busy("w1")
    assert s.worker_budget_facts("w1") == [
        "超时阈值 1200s",
        "已花 2700000/4000000",
    ]
    # Pass-local reset last-write-wins; tokens keep the max
    # so a new pass's 0 cannot wipe prior-pass spend.
    s.mark_worker_busy("w1", "llm", rounds_used=0, rounds_limit=4, tokens_spent=0)
    assert s.worker_budget_facts("w1") == [
        "超时阈值 1200s",
        "已花 2700000/4000000",
    ]
    s.disarm_worker_timeout("w1")
    assert s.worker_budget_facts("w1") == [
        "超时阈值 1200s",
        "token 顶 4000000",
    ]


async def test_idle_timeout_defers_when_workers_busy(monkeypatch):
    """Workers mid-LLM → idle window expires but no patrol nudge; real event still wakes."""
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_MAX_S", 1.0)
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-busy", total_workers=1)
    session._running_workers["w1"] = "研究员"
    session._worker_started_at["w1"] = __import__("time").monotonic()
    session.mark_worker_busy("w1", "llm")
    # Fake a live drive so wait does not short-circuit as team_done.
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        async def _post_later() -> None:
            # Stay busy across ≥1 idle window, then post while still busy so the
            # wait returns the real event (not a racey idle_timeout after clear).
            await asyncio.sleep(0.12)
            from agentcore.runtime.coordination.session import (
                CoordinationEvent,
                CoordinationEventKind,
            )

            session.post(
                CoordinationEvent(
                    kind=CoordinationEventKind.WORKER_COMPLETED,
                    payload={
                        "run_id": "w1",
                        "role": "研究员",
                        "status": "failed",
                    },
                )
            )

        helper = asyncio.create_task(_post_later())
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=3.0)
        await helper
        text = msgs[0].content or ""
        assert "等待团队事件超时" not in text
        assert "worker_completed" in text or "研究员" in text
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination()


async def test_idle_patrols_when_only_verify_busy(monkeypatch):
    """Long verify must not defer patrol — CEO can cancel_worker mid typecheck."""
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_MAX_S", 1.0)
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-verify-patrol",
        total_workers=1,
    )
    session._running_workers["w1"] = "渲染链路审查员"
    session._worker_started_at["w1"] = __import__("time").monotonic()
    session.mark_worker_busy("w1", "verify")
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=2.0)
        text = msgs[0].content or ""
        assert "等待团队事件超时" in text
        assert "有界验证中" in text
        assert "cancel_worker" in text
        assert session.idle_streak == 1
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination("e-verify-patrol")


async def test_idle_timeout_patrols_when_truly_stalled(monkeypatch):
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_MAX_S", 1.0)
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-stall", total_workers=1)
    session._running_workers["w1"] = "研究员"
    session._worker_started_at["w1"] = __import__("time").monotonic()
    # Registered but not busy → true stall → patrol with progress summary.
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=2.0)
        text = msgs[0].content or ""
        assert "等待团队事件超时" in text
        assert "研究员" in text
        assert session.idle_streak == 1
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination()


# --- B: isomorphic re-delegation --------------------------------------------


def test_tasks_similar_and_isomorphic_plan():
    assert tasks_similar("调研竞品格局", "调研竞品格局并整理要点")
    assert not tasks_similar("调研竞品", "撰写最终报告")
    live = _plan(
        RunSpec(run_id="a", role="法律研究员", task="梳理相关法条与司法解释"),
        RunSpec(run_id="b", role="实务案例分析师", task="归纳典型判例要点"),
        RunSpec(run_id="c", role="实务写作专家", task="撰写实务研究成稿"),
    )
    twin = _plan(
        RunSpec(run_id="a2", role="法律研究员", task="梳理相关法条与司法解释要点"),
        RunSpec(run_id="b2", role="实务案例分析师", task="归纳典型判例要点并对照"),
        RunSpec(run_id="c2", role="实务写作专家", task="撰写实务研究成稿"),
    )
    assert is_isomorphic_redelegation(twin, live, completed_run_ids=set()) is True
    different = _plan(
        RunSpec(run_id="d", role="审计员", task="独立审查成稿质量"),
    )
    assert is_isomorphic_redelegation(different, live, completed_run_ids=set()) is False


async def test_secondary_isomorphic_delegate_rejected():
    from agentcore.runtime.events import EventSink, EventType
    from tests.delegate.conftest import ctx, tool
    from tests.delegate.test_coordination_secondary_delegate import _SlowWorkers

    clear_active_coordination()
    sink = EventSink()
    t = tool(_SlowWorkers(["A", "B", "C", "D"], delay=0.5), sink=sink)
    first = await t.execute(
        {
            "tasks": [
                {"role": "研究员", "task": "做A调研"},
                {"role": "写手", "task": "做B撰写"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert first.success is True
    session = active_coordination("e")
    assert session is not None
    drive = session.drive_task
    assert drive is not None and not drive.done()
    assert len([e for e in sink._history if e.type is EventType.RUN_PLAN]) == 1

    second = await t.execute(
        {
            "tasks": [
                {"role": "研究员", "task": "做A调研补充"},
                {"role": "写手", "task": "做B撰写完善"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert second.success is False
    assert "同构" in (second.error or "")
    assert 'force=["' not in (second.error or "")
    assert "wait" not in (second.error or "")
    assert "update_synthesis" not in (second.error or "")
    assert second.contract_failure is True
    assert session.total_workers == 2
    # 同构拒在 emit 前：不得留下第二张 durable run_plan。
    assert len([e for e in sink._history if e.type is EventType.RUN_PLAN]) == 1

    # 同构连拒不得推进熔断。
    from agentcore.runtime.loop_controller import LoopController, ToolAttempt

    breaker = LoopController()
    for i in range(5):
        breaker.record(
            [
                ToolAttempt(
                    f"iso{i}",
                    "delegate",
                    success=False,
                    contract_failure=second.contract_failure,
                )
            ]
        )
        assert not breaker.tool_circuit_breaker()

    # 审查是净新角色：同构闸不拦，并入当前图。
    added = await t.execute(
        {
            "tasks": [
                {"role": "审查", "task": "做C审查"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert added.success is True
    assert session.total_workers >= 3

    drive.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drive
    clear_active_coordination("e")


# --- B1b: thrash rebrand cold-delegate ---------------------------------------


async def test_thrash_rebrand_cold_delegate_rejected_continue():
    """Cold similar task after thrash → reject; continue_from 续派不走换马甲闸。"""
    from agentcore.runtime.coordination.thrash import (
        ThrashRecord,
        clear_thrash_registry,
        note_thrashing_worker,
    )
    from agentcore.runtime.events import EventSink
    from tests.delegate.conftest import ctx, tool
    from tests.delegate.test_coordination_secondary_delegate import _SlowWorkers

    clear_thrash_registry()
    clear_active_coordination()
    sink = EventSink()
    t = tool(_SlowWorkers(["ok"], delay=0.05), sink=sink)
    t._conversation_id = "thrash-conv"

    note_thrashing_worker(
        "thrash-conv",
        ThrashRecord(
            run_id="prior-thrash",
            task="修复 TopBar 缺少 named export",
            artifacts=("src/TopBar.tsx",),
            role="工程师",
        ),
    )

    cold = await t.execute(
        {
            "tasks": [
                {
                    "role": "修码员",
                    "task": "修复 TopBar named export 缺失",
                    "deliverable": {
                        "form": "files",
                        "requires_files": True,
                        "artifacts": ["src/TopBar.tsx"],
                    },
                }
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert cold.success is False
    assert "触顶换马甲" in (cold.error or "")
    assert "continue_from_run_id=`prior-thrash`" in (cold.error or "")
    assert 'force=["' not in (cold.error or "")
    assert cold.contract_failure is True

    forced = await t.execute(
        {
            "tasks": [
                {
                    "role": "修码员",
                    "task": "修复 TopBar named export 缺失",
                    "deliverable": {
                        "form": "files",
                        "requires_files": True,
                        "artifacts": ["src/TopBar.tsx"],
                    },
                }
            ],
            "coordinate": False,
            "force": ["thrash"],
        },
        ctx(),
    )
    assert forced.success is False
    assert "触顶换马甲" in (forced.error or "")
    assert 'force=["' not in (forced.error or "")

    cont = await t.execute(
        {
            "tasks": [
                {
                    "role": "修码员",
                    "task": "修复 TopBar named export 缺失",
                    "continue_from_run_id": "prior-thrash",
                    "deliverable": {
                        "form": "files",
                        "requires_files": True,
                        "artifacts": ["src/TopBar.tsx"],
                    },
                }
            ],
            "coordinate": False,
        },
        ctx(),
    )
    # continue_from may still fail session lookup; admission must not thrash-reject.
    assert "触顶换马甲" not in (cont.error or "")
    clear_thrash_registry("thrash-conv")
    clear_active_coordination()


# --- B2: merge run_id collision receipts ------------------------------------


def _fake_merge_tool() -> MagicMock:
    tool = MagicMock()
    tool._sink = MagicMock()
    # MagicMock would otherwise make getattr(_folder_id) truthy and invent a
    # fake birth desk for ownership keys.
    tool._folder_id = None
    return tool


# --- B3: append overlap guard (role / deliverable) --------------------------


def test_append_overlap_reject_message_mentions_replaces_not_cancel_for_done():
    """拒派文案须明示 replaces_run_id，并点明已完成不能靠 cancel。"""
    from agentcore.runtime.coordination.append_guard import (
        AppendOverlap,
        append_overlap_reject_message,
    )

    empty = append_overlap_reject_message([], completed=0, total=2)
    assert "replaces_run_id" in empty
    assert "不能靠 cancel" in empty

    detailed = append_overlap_reject_message(
        [
            AppendOverlap(
                new_role="内容策略",
                live_role="内容文案",
                live_run_id="copy",
                new_run_id="dup",
                reason="role+deliverable",
            )
        ],
        completed=1,
        total=2,
    )
    assert "replaces_run_id" in detailed
    assert "replan" in detailed
    assert "不能靠 cancel" in detailed
    assert "内容策略" in detailed


def test_roles_and_file_targets_detect_geo_class_overlap():
    from agentcore.runtime.coordination.append_guard import (
        declare_plan_artifacts,
        find_append_overlaps,
        node_file_targets,
        roles_overlap,
    )
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace.write_claims import WriteCoordinator

    # Seat = normalized exact equality only (no fuzzy prefix / stem / edit distance).
    assert not roles_overlap("内容文案", "内容策略")
    assert not roles_overlap("痛点调研员", "定价调研员")
    assert roles_overlap("页面 QA", "页面 QA")
    assert roles_overlap("痛点调研员", " 痛点调研员 ")
    assert roles_overlap("V2专项测试员", "V2 专项测试员")
    assert roles_overlap("V2专项测试员", "v2专项测试员")
    assert not roles_overlap("前端工程师", "测试工程师")
    assert not roles_overlap("SEO 优化师", "内容文案")

    skeleton = RunSpec(
        run_id="skeleton",
        role="骨架工程师",
        task="写 site/index.html 与 site/styles.css",
        deliverable=Deliverable(
            form="files",
            artifacts=["site/index.html", "site/styles.css", "site/main.js"],
        ),
    )
    frontend = RunSpec(
        run_id="fe",
        role="前端工程师",
        task="基于文案实现整站，写入 site/index.html",
        deliverable=Deliverable(
            form="files",
            artifacts=["site/index.html"],
        ),
    )
    assert "site/index.html" in node_file_targets(skeleton)
    assert "site/index.html" in node_file_targets(frontend)

    live = _plan(
        RunSpec(
            run_id="copy",
            role="内容文案",
            task="撰写文案落盘 site/copy.md",
            deliverable=Deliverable(artifacts=["site/copy.md"]),
        ),
        skeleton,
        RunSpec(run_id="qa", role="页面 QA", task="质检并写 site/QA.md"),
    )
    overlapping = _plan(frontend)
    ownership = WriteCoordinator()
    declare_plan_artifacts(live, ownership)
    hits = find_append_overlaps(
        overlapping, live, completed_run_ids=set(), ownership=ownership
    )
    assert hits == []

    non_overlap = _plan(
        RunSpec(run_id="seo", role="SEO 优化师", task="整理站外外链策略备忘"),
    )
    assert find_append_overlaps(non_overlap, live, completed_run_ids=set()) == []

    # Same seat still collides; different seats with shared job suffix do not.
    same_seat = _plan(RunSpec(run_id="qa2", role="页面 QA", task="再质检"))
    assert find_append_overlaps(same_seat, live, completed_run_ids=set())
    assert find_append_overlaps(same_seat, live, completed_run_ids=set())[0].reason == "role"
    fe_vs_qa = _plan(RunSpec(run_id="fe_only", role="前端工程师", task="写组件（无站点文件）"))
    assert find_append_overlaps(fe_vs_qa, live, completed_run_ids=set()) == []


def test_vacated_seat_auto_replaces_pain_point_case():
    """痛点失败 + 定价未完成 + 再派痛点（无 replaces）→ 放行并接替痛点空位。"""
    from agentcore.runtime.coordination.append_guard import (
        apply_vacated_seat_replaces,
        declare_plan_artifacts,
        find_append_overlaps,
    )
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace.write_claims import WriteCoordinator

    live = _plan(
        RunSpec(
            run_id="pain",
            role="痛点调研员",
            task="调研痛点",
            deliverable=Deliverable(artifacts=["research/pain.md"]),
        ),
        RunSpec(
            run_id="price",
            role="定价调研员",
            task="调研定价",
            deliverable=Deliverable(artifacts=["research/pricing.md"]),
        ),
        RunSpec(
            run_id="channel",
            role="渠道调研员",
            task="调研渠道",
            deliverable=Deliverable(artifacts=["research/channel.md"]),
        ),
    )
    ownership = WriteCoordinator()
    declare_plan_artifacts(live, ownership)
    assert ownership.owner_of("research/pain.md") == "pain"

    # 痛点终态失败；定价/渠道仍在跑。
    completed = {"pain"}
    vacated = {"pain"}
    redispatch = _plan(
        RunSpec(
            run_id="pain2",
            role="痛点调研员",
            task="补调研痛点",
            deliverable=Deliverable(artifacts=["research/pain.md"]),
        )
    )
    applied = apply_vacated_seat_replaces(
        redispatch,
        live,
        completed_run_ids=completed,
        vacated_run_ids=vacated,
    )
    assert applied == [("pain2", "pain")]
    assert redispatch.nodes[0].replaces_run_id == "pain"
    assert (
        find_append_overlaps(
            redispatch, live, completed_run_ids=completed, ownership=ownership
        )
        == []
    )
    declare_plan_artifacts(redispatch, ownership)
    assert ownership.owner_of("research/pain.md") == "pain2"

    # 成功完成的同座位再派 → 同样自动 replaces（同岗位续做 / 预算触顶补派）。
    live_ok = _plan(
        RunSpec(
            run_id="pain_ok",
            role="痛点调研员",
            task="已成功",
            deliverable=Deliverable(artifacts=["research/pain.md"]),
        ),
        RunSpec(
            run_id="price2",
            role="定价调研员",
            task="仍在跑",
            deliverable=Deliverable(artifacts=["research/pricing.md"]),
        ),
    )
    own2 = WriteCoordinator()
    declare_plan_artifacts(live_ok, own2)
    again = _plan(
        RunSpec(
            run_id="pain3",
            role="痛点调研员",
            task="再派",
            deliverable=Deliverable(artifacts=["research/pain.md"]),
        )
    )
    assert apply_vacated_seat_replaces(
        again,
        live_ok,
        completed_run_ids={"pain_ok"},
        vacated_run_ids=set(),
    ) == [("pain3", "pain_ok")]
    assert again.nodes[0].replaces_run_id == "pain_ok"
    assert (
        find_append_overlaps(
            again, live_ok, completed_run_ids={"pain_ok"}, ownership=own2
        )
        == []
    )
    declare_plan_artifacts(again, own2)
    assert own2.owner_of("research/pain.md") == "pain3"


def test_same_seat_incomplete_still_rejects():
    """定价未完成时再派定价 → 仍拒（真撞座位）。"""
    from agentcore.runtime.coordination.append_guard import (
        apply_vacated_seat_replaces,
        find_append_overlaps,
    )

    live = _plan(
        RunSpec(run_id="price", role="定价调研员", task="调研定价"),
        RunSpec(run_id="channel", role="渠道调研员", task="调研渠道"),
    )
    dup = _plan(RunSpec(run_id="price2", role="定价调研员", task="再派定价"))
    assert (
        apply_vacated_seat_replaces(
            dup, live, completed_run_ids=set(), vacated_run_ids=set()
        )
        == []
    )
    hits = find_append_overlaps(dup, live, completed_run_ids=set())
    assert hits
    assert hits[0].reason == "role"
    assert hits[0].live_run_id == "price"


def test_same_batch_norm_role_whitespace_variant_rejects():
    """同批「V2专项测试员」与「V2 专项测试员」→ sibling_role 拒收。"""
    from agentcore.runtime.coordination.append_guard import (
        admit_added_nodes,
        append_overlap_reject_message,
        find_append_overlaps,
        find_sibling_role_crosses,
    )

    batch = _plan(
        RunSpec(run_id="qa1", role="V2专项测试员", task="测 A"),
        RunSpec(run_id="qa2", role="V2 专项测试员", task="测 B"),
    )
    sibling = find_sibling_role_crosses(batch)
    assert len(sibling) == 1
    assert sibling[0].reason == "sibling_role"
    assert {sibling[0].live_run_id, sibling[0].new_run_id} == {"qa1", "qa2"}

    hits = find_append_overlaps(batch, None, completed_run_ids=set())
    assert hits
    assert all(h.reason == "sibling_role" for h in hits)

    msg = append_overlap_reject_message(hits, completed=0, total=len(batch.nodes))
    assert "同批座位重叠已拒绝" in msg
    assert "V2" in msg

    reject = admit_added_nodes(batch, None, completed_run_ids=set())
    assert reject is not None
    assert "同批座位重叠已拒绝" in reject

    # Distinct seats after norm still admit.
    ok = _plan(
        RunSpec(run_id="a", role="V2专项测试员", task="测 A"),
        RunSpec(run_id="b", role="V2专项开发员", task="写 B"),
    )
    assert find_sibling_role_crosses(ok) == []
    assert find_append_overlaps(ok, None, completed_run_ids=set()) == []
    assert admit_added_nodes(ok, None, completed_run_ids=set()) is None


def test_same_batch_serial_same_role_with_depends_on_admits():
    """同批同座但 depends_on 串行交接 → 非 sibling_role（勿靠改角色名）。"""
    from agentcore.runtime.coordination.append_guard import (
        admit_added_nodes,
        find_sibling_role_crosses,
    )

    batch = _plan(
        RunSpec(run_id="r1", role="正方", task="首轮"),
        RunSpec(run_id="r2", role="正方", task="次轮", depends_on=["r1"]),
    )
    assert find_sibling_role_crosses(batch) == []
    assert admit_added_nodes(batch, None, completed_run_ids=set()) is None


def test_same_batch_scoped_fanout_same_role_admits():
    """同批同角色但交付物 scope 互斥（sibling 各写 distinct artifacts）→ 放行。"""
    from agentcore.runtime.coordination.append_guard import (
        admit_added_nodes,
        find_sibling_role_crosses,
    )
    from agentcore.runtime.runs.types import Deliverable

    batch = _plan(
        RunSpec(
            run_id="eval_0",
            role="评估员",
            task="评 A",
            deliverable=Deliverable(artifacts=["evals/option-a.md"]),
        ),
        RunSpec(
            run_id="eval_1",
            role="评估员",
            task="评 B",
            deliverable=Deliverable(artifacts=["evals/option-b.md"]),
        ),
    )
    assert find_sibling_role_crosses(batch) == []
    assert admit_added_nodes(batch, None, completed_run_ids=set()) is None


def test_same_batch_norm_role_conflicts_with_live_incomplete():
    """新批空格变体撞 live 未完成同座 → role 拒（new∪live）。"""
    from agentcore.runtime.coordination.append_guard import find_append_overlaps

    live = _plan(RunSpec(run_id="live_qa", role="V2专项测试员", task="在跑"))
    new = _plan(RunSpec(run_id="qa2", role="V2 专项测试员", task="再派"))
    hits = find_append_overlaps(new, live, completed_run_ids=set())
    assert hits
    assert hits[0].reason == "role"
    assert hits[0].live_run_id == "live_qa"


async def test_merge_auto_replaces_vacated_seat_and_rewrites_deps():
    """merge 入闸：空位自动 replaces + 下游 depends_on 改边。"""
    from agentcore.runtime.coordination.host import _merge_into_active_coordination
    from agentcore.runtime.runs.types import Deliverable

    clear_active_coordination()
    live = _plan(
        RunSpec(
            run_id="pain",
            role="痛点调研员",
            task="调研痛点",
            deliverable=Deliverable(artifacts=["research/pain.md"]),
        ),
        RunSpec(
            run_id="price",
            role="定价调研员",
            task="调研定价",
            deliverable=Deliverable(artifacts=["research/pricing.md"]),
        ),
        RunSpec(
            run_id="synth",
            role="汇总",
            task="汇总三路",
            depends_on=["pain", "price"],
        ),
    )
    session = CoordinationSession(execution_id="e-seat", total_workers=3)
    session.live_plan = live
    session.completed_run_ids.add("pain")
    session.vacated_run_ids.add("pain")
    session.failed_run_ids.add("pain")
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        result = _merge_into_active_coordination(
            _fake_merge_tool(),
            _plan(
                RunSpec(
                    run_id="pain2",
                    role="痛点调研员",
                    task="补派痛点",
                    deliverable=Deliverable(artifacts=["research/pain.md"]),
                )
            ),
            session,
            execution_id="e-seat",
            seed_completed=None,
            complexity_hint="",
            call_idx=2,
        )
        assert result.success is True
        pain2 = next(n for n in live.nodes if n.run_id == "pain2")
        assert pain2.replaces_run_id == "pain"
        synth = next(n for n in live.nodes if n.run_id == "synth")
        assert "pain2" in synth.depends_on
        assert "pain" not in synth.depends_on
        assert "price" in synth.depends_on
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination("e-seat")


async def test_merge_allows_same_path_different_seat_append():
    """DAG 未完成时追加同路径、不同座位 → 放行。"""
    from agentcore.runtime.coordination.append_guard import declare_plan_artifacts
    from agentcore.runtime.coordination.host import _merge_into_active_coordination
    from agentcore.runtime.runs.types import Deliverable

    clear_active_coordination()
    live = _plan(
        RunSpec(
            run_id="copy",
            role="内容文案",
            task="写 site/copy.md",
            deliverable=Deliverable(artifacts=["site/copy.md"]),
            depends_on=[],
        ),
        RunSpec(
            run_id="skeleton",
            role="骨架工程师",
            task="写 site/index.html",
            deliverable=Deliverable(artifacts=["site/index.html"]),
            depends_on=["copy"],
        ),
    )
    session = CoordinationSession(execution_id="e-overlap", total_workers=2)
    session.live_plan = live
    declare_plan_artifacts(live, session.ensure_file_ownership())
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        overlapping = _plan(
            RunSpec(
                run_id="dup_copy",
                role="内容策略",
                task="为官网撰写各版块文案并落盘 site/copy.md",
                deliverable=Deliverable(artifacts=["site/copy.md"]),
            )
        )
        result = _merge_into_active_coordination(
            _fake_merge_tool(),
            overlapping,
            session,
            execution_id="e-overlap",
            seed_completed=None,
            complexity_hint="",
            call_idx=2,
        )
        assert result.success is True
        assert session.total_workers == 3
        assert len(live.nodes) == 3
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination("e-overlap")


async def test_merge_allows_non_overlapping_append():
    """流水线外新增职责（无角色/文件重叠）仍放行。"""
    from agentcore.runtime.coordination.host import _merge_into_active_coordination
    from agentcore.runtime.runs.types import Deliverable

    clear_active_coordination()
    live = _plan(
        RunSpec(
            run_id="copy",
            role="内容文案",
            task="写 site/copy.md",
            deliverable=Deliverable(artifacts=["site/copy.md"]),
        ),
        RunSpec(
            run_id="skeleton",
            role="骨架工程师",
            task="写 site/index.html",
            deliverable=Deliverable(artifacts=["site/index.html"]),
            depends_on=["copy"],
        ),
    )
    session = CoordinationSession(execution_id="e-ok-append", total_workers=2)
    session.live_plan = live
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        extra = _plan(
            RunSpec(
                run_id="legal",
                role="合规顾问",
                task="审核品牌用语是否触碰广告法红线，产出备忘（不写站点文件）",
            )
        )
        result = _merge_into_active_coordination(
            _fake_merge_tool(),
            extra,
            session,
            execution_id="e-ok-append",
            seed_completed=None,
            complexity_hint="",
            call_idx=2,
        )
        assert result.success is True
        assert session.total_workers == 3
        assert any(n.run_id == "legal" for n in live.nodes)
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination("e-ok-append")


def test_healthy_idle_inject_has_progress_and_no_action_guidance():
    """健康流水线 idle 注入含进度视图，并明确导向无需追加动作。"""
    from agentcore.runtime.coordination.inject import idle_yield_messages
    from agentcore.runtime.coordination.pipeline_view import (
        format_idle_yield_brief,
        is_pipeline_healthy,
    )
    from agentcore.runtime.runs.types import Deliverable

    live = _plan(
        RunSpec(
            run_id="copy",
            role="内容文案",
            task="写 site/copy.md",
            deliverable=Deliverable(artifacts=["site/copy.md"]),
            token_ceiling=4_000_000,
            max_rounds=56,
            policy=RunPolicy(timeout_s=1200),
        ),
        RunSpec(
            run_id="skeleton",
            role="骨架工程师",
            task="写 site/index.html",
            deliverable=Deliverable(artifacts=["site/index.html"]),
            depends_on=["copy"],
        ),
        RunSpec(
            run_id="qa",
            role="页面 QA",
            task="质检",
            depends_on=["skeleton"],
        ),
    )
    session = CoordinationSession(execution_id="e-idle", total_workers=3)
    session.live_plan = live
    session._running_workers["copy"] = "内容文案"
    session._worker_started_at["copy"] = __import__("time").monotonic()
    session.mark_worker_busy("copy", "llm")

    assert is_pipeline_healthy(session) is True
    brief = format_idle_yield_brief(session)
    assert "流水线进度" in brief
    assert "Wave" in brief
    assert "在跑" in brief or "内容文案" in brief
    assert "依赖阻塞" in brief
    assert "无需追加" in brief
    assert "正常推进" in brief
    assert "【协调期】" in brief
    assert "可静默" in brief
    assert "谁还在跑" not in brief
    assert "三选一" not in brief
    assert "谁在后台推进" not in brief
    assert "谁在后台、完成后会再汇报" not in brief
    assert "保持静默即可" not in brief
    assert "cancel_worker" not in brief
    assert "token 顶 4000000" in brief
    assert "轮次上限" not in brief
    assert "超时阈值 1200s" in brief
    assert "疑似卡死" not in brief
    assert "上方进展行" not in brief
    assert "已运行墙钟" not in brief

    msgs = idle_yield_messages(session)
    assert len(msgs) == 1
    assert "流水线进度" in (msgs[0].content or "")
    assert "无需追加" in (msgs[0].content or "")
    assert "可静默" in (msgs[0].content or "")
    assert "保持静默即可" not in (msgs[0].content or "")
    assert "cancel_worker" not in (msgs[0].content or "")
    assert "token 顶 4000000" in (msgs[0].content or "")


def test_healthy_idle_brief_shows_live_spend_when_stamped():
    """CEO idle brief lists 已花 once the executor has stamped spend."""
    from agentcore.runtime.coordination.pipeline_view import format_idle_yield_brief
    from agentcore.runtime.runs.types import Deliverable

    live = _plan(
        RunSpec(
            run_id="copy",
            role="内容文案",
            task="写 site/copy.md",
            deliverable=Deliverable(artifacts=["site/copy.md"]),
            token_ceiling=4_000_000,
            max_rounds=56,
            policy=RunPolicy(timeout_s=1200),
        ),
        RunSpec(
            run_id="skeleton",
            role="骨架工程师",
            task="写 site/index.html",
            depends_on=["copy"],
        ),
    )
    session = CoordinationSession(execution_id="e-idle-spend", total_workers=2)
    session.live_plan = live
    session._running_workers["copy"] = "内容文案"
    session._worker_started_at["copy"] = __import__("time").monotonic()
    session.mark_worker_busy(
        "copy",
        "llm",
        rounds_used=52,
        rounds_limit=56,
        tokens_spent=2_700_000,
    )
    brief = format_idle_yield_brief(session)
    assert "已花 2700000/4000000" in brief
    assert "已用" not in brief
    assert "轮次上限" not in brief
    assert "token 顶 4000000" not in brief
    assert "疑似卡死" not in brief
    assert "上方进展行" not in brief


def test_idle_yield_brief_pending_approval_forbids_wait(monkeypatch):
    """有热路 pending 时 idle_yield 文案禁止 wait/再派，不含「正常推进」。"""
    from agentcore.runtime.coordination.pipeline_view import format_idle_yield_brief
    from agentcore.runtime.runs.types import Deliverable

    live = _plan(
        RunSpec(
            run_id="copy",
            role="内容文案",
            task="写 site/copy.md",
            deliverable=Deliverable(artifacts=["site/copy.md"]),
        ),
        RunSpec(
            run_id="skeleton",
            role="骨架工程师",
            task="写 site/index.html",
            deliverable=Deliverable(artifacts=["site/index.html"]),
            depends_on=["copy"],
        ),
    )
    session = CoordinationSession(
        execution_id="e-idle-pending", total_workers=2, conversation_id="c-idle"
    )
    session.live_plan = live
    session._running_workers["copy"] = "内容文案"
    session._worker_started_at["copy"] = __import__("time").monotonic()
    session.mark_worker_busy("copy", "llm")

    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.has_hot_user_pending",
        lambda _cid: True,
    )

    brief = format_idle_yield_brief(session)
    assert "等你允许" in brief
    assert "等待用户审批" in brief or "审批/授权" in brief
    assert "正常推进" not in brief
    assert "这是预期中的等待" not in brief
    assert "禁止" in brief or "勿" in brief
    assert "会继续" in brief or "报告阻塞" in brief
    assert "听团" in brief or "wait" in brief
    assert "【禁止】调用 wait" not in brief
    assert "保持静默，引导" not in brief
    assert "团队已取消" not in brief
    assert "调度已停" not in brief


async def test_idle_yield_held_while_inflight(monkeypatch):
    """队员仍有 in-flight LLM 时不叫醒 CEO；失败完成仍立刻叫醒。"""
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_MAX_S", 1.0)
    monkeypatch.setattr(coord_wait, "_WAIT_HEARTBEAT_S", 0.05)
    clear_active_coordination()
    live = _plan(
        RunSpec(run_id="a", role="内容文案", task="写 site/copy.md", depends_on=[]),
        RunSpec(
            run_id="b",
            role="骨架工程师",
            task="写骨架",
            depends_on=["a"],
        ),
    )
    session = CoordinationSession(execution_id="e-idle-yield", total_workers=2)
    session.live_plan = live
    session._running_workers["a"] = "内容文案"
    session._worker_started_at["a"] = __import__("time").monotonic()
    session.mark_worker_busy("a", "llm")
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        from agentcore.runtime.coordination.session import (
            CoordinationEvent,
            CoordinationEventKind,
        )

        async def _fail_later() -> None:
            await asyncio.sleep(0.25)
            session.post(
                CoordinationEvent(
                    kind=CoordinationEventKind.WORKER_COMPLETED,
                    payload={
                        "run_id": "a",
                        "role": "内容文案",
                        "status": "failed",
                        "summary": "boom",
                    },
                )
            )

        fail_task = asyncio.create_task(_fail_later())
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=2.0)
        await fail_task
        assert session.idle_streak == 0
        assert msgs
        assert "worker_completed" in (msgs[0].content or "")
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination("e-idle-yield")


async def test_merge_all_skipped_returns_structured_failure():
    """整批 run_id 撞车 → success=False，结构化回执列出跳过明细，不改 live 图。"""
    from agentcore.runtime.coordination.host import _merge_into_active_coordination

    clear_active_coordination()
    live = _plan(RunSpec(run_id="a", role="研究员", task="做A"))
    session = CoordinationSession(execution_id="e-skip-all", total_workers=1)
    session.live_plan = live
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        colliding = _plan(
            RunSpec(run_id="a", role="写手", task="撞车同 id"),
        )
        workers_before = session.total_workers
        budget_before = session.budget_remaining
        result = _merge_into_active_coordination(
            _fake_merge_tool(),
            colliding,
            session,
            execution_id="e-skip-all",
            seed_completed=None,
            complexity_hint="",
            call_idx=2,
        )
        assert result.success is False
        err = result.error or ""
        assert "全部跳过" in err
        assert "`a`" in err
        assert "duplicate run_id" in err
        assert "wait" not in err
        assert "update_synthesis" not in err
        assert result.contract_failure is True
        assert session.total_workers == workers_before
        assert session.budget_remaining == budget_before
        assert len(live.nodes) == 1
        assert live.nodes[0].role == "研究员"
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination("e-skip-all")


async def test_merge_partial_skip_lists_merged_and_skipped():
    """部分撞车 → success=True，回执同时列已并入与跳过原因；新节点入图。"""
    from agentcore.runtime.coordination.host import _merge_into_active_coordination

    clear_active_coordination()
    live = _plan(RunSpec(run_id="a", role="研究员", task="做A"))
    session = CoordinationSession(execution_id="e-skip-part", total_workers=1)
    session.live_plan = live
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        batch = _plan(
            RunSpec(run_id="a", role="撞车", task="复用 id"),
            RunSpec(run_id="b", role="写手", task="新任务"),
        )
        result = _merge_into_active_coordination(
            _fake_merge_tool(),
            batch,
            session,
            execution_id="e-skip-part",
            seed_completed=None,
            complexity_hint="",
            call_idx=2,
        )
        assert result.success is True
        out = result.output or ""
        assert "部分跳过" in out
        assert "写手" in out
        assert "`a`" in out
        assert "duplicate run_id" in out
        assert "wait" not in out
        assert "update_synthesis" not in out
        assert "人已派出" not in out
        assert session.total_workers == 2
        assert {n.run_id for n in live.nodes} == {"a", "b"}
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination("e-skip-part")


# --- C: user_stop cascade ---------------------------------------------------


def test_user_stop_body_keeps_stream_without_chrome_notes():
    """Interrupt closer writes captain text only; stop chrome is metadata + UI."""
    body = compose_interrupt_body("partial", reason=TurnInterruptReason.USER_STOP)
    assert body == "partial"
    assert "已停止" not in body
    assert "连接中断" not in body
    empty = compose_interrupt_body("", reason=TurnInterruptReason.USER_STOP)
    assert empty == ""


def test_redrive_failed_body_forces_user_visible_notice():
    """案 fake-dispatch-stall-claim C：redrive_failed 禁止静默清队无说明。"""
    from agentcore.runtime.turn.interrupt import REDRIVE_FAILED_USER_VISIBLE

    kickoff = "好，派 3 个 worker 开工高规格版："
    body = compose_interrupt_body(kickoff, reason=TurnInterruptReason.REDRIVE_FAILED)
    assert kickoff in body
    assert REDRIVE_FAILED_USER_VISIBLE in body

    empty = compose_interrupt_body("", reason=TurnInterruptReason.REDRIVE_FAILED)
    assert empty == REDRIVE_FAILED_USER_VISIBLE

    # Idempotent — no stacked notices on re-salvage.
    again = compose_interrupt_body(body, reason=TurnInterruptReason.REDRIVE_FAILED)
    assert again.count("【中断说明】") == 1


def test_redrive_failed_notice_answers_what_survived_not_internal_verbs():
    """崩溃后用户最怕的是「清掉的是我的东西吗」——文案必须正面回答，别丢内部动词。"""
    from agentcore.runtime.turn.interrupt import REDRIVE_FAILED_USER_VISIBLE

    note = REDRIVE_FAILED_USER_VISIBLE
    # 影响：这一轮不再继续（队员停下）。
    assert "不会再有新进展" in note
    # 用户的东西还在——这是崩溃现场他唯一想确认的事。
    assert "文件" in note and "都还在" in note
    # 他能做什么。
    assert "直接发下一条" in note
    # 内部动作 / 标识不进用户面。
    for internal in ("团队已清", "已清", "run_id", "execution", "redrive", "salvage"):
        assert internal not in note


def test_redrive_failed_notice_stays_idempotent_over_legacy_bodies():
    """升级前挂的是旧稿：再次收口不许叠第二条说明。"""
    legacy = "好，派 3 个 worker 开工：\n\n【中断说明】后台恢复失败，本轮未完工（团队已清）。可直接发送下一条继续。"
    again = compose_interrupt_body(legacy, reason=TurnInterruptReason.REDRIVE_FAILED)
    assert again == legacy
    assert again.count("【中断说明】") == 1


async def test_user_stop_cancels_drive_and_release_clears():
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-stop", total_workers=2, conversation_id="conv-stop"
    )
    session._running_workers["w1"] = "研究员"
    session._running_workers["w2"] = "写手"

    async def _hang() -> None:
        await asyncio.Event().wait()

    session.drive_task = asyncio.create_task(_hang())
    set_active_coordination(session)

    assert cancel_coordination_on_user_stop("conv-stop") is True
    assert session.user_stopped is True
    assert "w1" in session.cancel_ids
    assert "w2" in session.cancel_ids
    # Drive cancel signalled
    await asyncio.sleep(0)
    assert session.drive_task.cancelled() or session.drive_task.done()
    assert session.drive_cancel_reason == "user_stop"
    from agentcore.core.task_cancel import CANCEL_REASON_ATTR, cancel_reason_from_task

    assert getattr(session.drive_task, CANCEL_REASON_ATTR, None) == "user_stop"
    assert cancel_reason_from_task(session.drive_task) == "user_stop"

    release_turn_coordination("e-stop")
    assert active_coordination("e-stop") is None
    clear_active_coordination()


async def test_turn_runs_stop_cascades_coordination():
    clear_active_coordination()
    conversation_id = "conv-cascade"
    session = CoordinationSession(
        execution_id="e-cascade", total_workers=1, conversation_id=conversation_id
    )
    session._running_workers["w1"] = "研究员"

    async def _hang() -> None:
        await asyncio.Event().wait()

    session.drive_task = asyncio.create_task(_hang())
    set_active_coordination(session)

    async def _noop() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(_noop())
    turn_runs._runs[conversation_id] = TurnRun(
        run_id="r1",
        conversation_id=conversation_id,
        task=task,
        sink=MagicMock(),
    )
    try:
        assert turn_runs.stop(conversation_id) is True
        assert session.user_stopped is True
        assert "w1" in session.cancel_ids
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        turn_runs._runs.pop(conversation_id, None)
        clear_active_coordination()


# --- C3: file ownership at dispatch (完成后仍占 / sibling / replaces) ---


def test_sibling_artifact_cross_detected():
    from agentcore.runtime.coordination.append_guard import find_sibling_artifact_crosses
    from agentcore.runtime.runs.types import Deliverable

    plan = _plan(
        RunSpec(
            run_id="a",
            role="前端",
            task="写 App",
            deliverable=Deliverable(artifacts=["src/App.tsx"]),
        ),
        RunSpec(
            run_id="b",
            role="整合",
            task="也写 App",
            deliverable=Deliverable(artifacts=["src/App.tsx"]),
        ),
    )
    hits = find_sibling_artifact_crosses(plan)
    assert hits
    assert hits[0].reason == "sibling_artifact"
    assert hits[0].path == "src/App.tsx"


def test_cross_desk_same_rel_path_not_sibling_cross():
    """两桌同 App.tsx → 同批 sibling 不撞。"""
    from agentcore.runtime.coordination.append_guard import find_sibling_artifact_crosses
    from agentcore.runtime.runs.types import Deliverable

    plan = _plan(
        RunSpec(
            run_id="a",
            role="前端 A",
            task="写 App",
            deliverable=Deliverable(artifacts=["App.tsx"]),
            target_folder_id="desk-alpha",
        ),
        RunSpec(
            run_id="b",
            role="前端 B",
            task="也写 App",
            deliverable=Deliverable(artifacts=["App.tsx"]),
            target_folder_id="desk-beta",
        ),
    )
    assert find_sibling_artifact_crosses(plan) == []


def test_same_desk_same_rel_path_still_sibling_cross():
    """同桌同路径仍硬拒。"""
    from agentcore.runtime.coordination.append_guard import (
        append_overlap_reject_message,
        find_sibling_artifact_crosses,
    )
    from agentcore.runtime.runs.types import Deliverable

    plan = _plan(
        RunSpec(
            run_id="a",
            role="前端 A",
            task="写 App",
            deliverable=Deliverable(artifacts=["App.tsx"]),
            target_folder_id="desk-same",
        ),
        RunSpec(
            run_id="b",
            role="前端 B",
            task="也写 App",
            deliverable=Deliverable(artifacts=["App.tsx"]),
            target_folder_id="desk-same",
        ),
    )
    hits = find_sibling_artifact_crosses(plan)
    assert hits
    assert hits[0].path == "App.tsx"
    msg = append_overlap_reject_message(hits, completed=0, total=2)
    assert "同批交付物交叉已拒绝" in msg
    assert "队员追加已拒绝" not in msg
    assert "`App.tsx`" in msg


def test_ancestor_artifact_overlap_not_sibling_cross():
    from agentcore.runtime.coordination.append_guard import find_sibling_artifact_crosses
    from agentcore.runtime.runs.types import Deliverable

    plan = _plan(
        RunSpec(
            run_id="up",
            role="草稿",
            task="草稿",
            deliverable=Deliverable(artifacts=["report.md"]),
        ),
        RunSpec(
            run_id="down",
            role="整合",
            task="整合",
            depends_on=["up"],
            deliverable=Deliverable(artifacts=["report.md"]),
        ),
    )
    assert find_sibling_artifact_crosses(plan) == []


def test_completed_owner_allows_append_for_dispatch_handoff():
    """已完成锁主 + 新节点声明同 artifact → 入闸不拒（declare 会 handoff）。"""
    from agentcore.runtime.coordination.append_guard import (
        declare_plan_artifacts,
        find_append_overlaps,
    )
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace.write_claims import WriteCoordinator

    live = _plan(
        RunSpec(
            run_id="integration",
            role="整合",
            task="写 App.tsx",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        ),
    )
    ownership = WriteCoordinator()
    declare_plan_artifacts(live, ownership)
    new = _plan(
        RunSpec(
            run_id="fe2",
            role="前端 App",
            task="重写 App",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        )
    )
    hits = find_append_overlaps(
        new, live, completed_run_ids={"integration"}, ownership=ownership
    )
    assert hits == []
    declare_plan_artifacts(new, ownership, completed_run_ids={"integration"})
    assert ownership.owner_of("App.tsx") == "fe2"


def test_same_seat_completed_cold_redispatch_inherits_locks():
    """本样本形态：同岗位预算触顶未落盘后再派 → auto replaces，无需声明 artifact 也能写。"""
    from agentcore.runtime.coordination.append_guard import (
        apply_vacated_seat_replaces,
        declare_plan_artifacts,
        find_append_overlaps,
    )
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace.write_claims import WriteCoordinator

    live = _plan(
        RunSpec(
            run_id="fe1",
            role="前端改造",
            task="令牌化",
            deliverable=Deliverable(
                artifacts=["src/ui/ReasoningGraph.tsx", "src/game/GameScene.ts"]
            ),
        ),
    )
    ownership = WriteCoordinator()
    declare_plan_artifacts(live, ownership)
    assert ownership.owner_of("src/ui/ReasoningGraph.tsx") == "fe1"

    cold = _plan(
        RunSpec(
            run_id="fe2",
            role="前端改造",
            task="完成 ReasoningGraph.tsx 与 GameScene.ts 落盘",
        )
    )
    assert apply_vacated_seat_replaces(
        cold, live, completed_run_ids={"fe1"}, vacated_run_ids=set()
    ) == [("fe2", "fe1")]
    assert (
        find_append_overlaps(
            cold, live, completed_run_ids={"fe1"}, ownership=ownership
        )
        == []
    )
    declare_plan_artifacts(cold, ownership)
    assert ownership.owner_of("src/ui/ReasoningGraph.tsx") == "fe2"
    assert ownership.owner_of("src/game/GameScene.ts") == "fe2"
    assert ownership.claim("src/ui/ReasoningGraph.tsx", "fe2", frozenset()) is None


def test_running_owner_does_not_block_append_file_overlap():
    """锁主仍在跑时，声明同 artifact 的无关节点可以开工（座位不重叠）。"""
    from agentcore.runtime.coordination.append_guard import (
        declare_plan_artifacts,
        find_append_overlaps,
    )
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace.write_claims import WriteCoordinator

    live = _plan(
        RunSpec(
            run_id="integration",
            role="整合",
            task="写 App.tsx",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        ),
    )
    ownership = WriteCoordinator()
    declare_plan_artifacts(live, ownership)
    new = _plan(
        RunSpec(
            run_id="fe2",
            role="前端 App",
            task="重写 App",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        )
    )
    hits = find_append_overlaps(
        new, live, completed_run_ids=set(), ownership=ownership
    )
    assert hits == []


def test_replaces_skips_overlap_and_transfers():
    from agentcore.runtime.coordination.append_guard import (
        declare_plan_artifacts,
        find_append_overlaps,
    )
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace.write_claims import WriteCoordinator

    live = _plan(
        RunSpec(
            run_id="old",
            role="前端",
            task="写",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        ),
    )
    ownership = WriteCoordinator()
    declare_plan_artifacts(live, ownership)
    assert ownership.owner_of("App.tsx") == "old"

    replacement = _plan(
        RunSpec(
            run_id="new",
            role="前端补派",
            task="接手",
            replaces_run_id="old",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        )
    )
    assert (
        find_append_overlaps(
            replacement, live, completed_run_ids=set(), ownership=ownership
        )
        == []
    )
    declare_plan_artifacts(replacement, ownership)
    assert ownership.owner_of("App.tsx") == "new"


async def test_merge_admits_after_completed_owner_and_handoffs():
    """图全完成后声明同终稿 → 放行并 dispatch_handoff（跨波次修订）。"""
    from agentcore.runtime.coordination.host import _merge_into_active_coordination
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace.write_claims import WriteCoordinator

    clear_active_coordination()
    live = _plan(
        RunSpec(
            run_id="integration",
            role="整合",
            task="写 App.tsx",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        ),
    )
    session = CoordinationSession(execution_id="e-done-own", total_workers=1)
    session.live_plan = live
    session.completed_run_ids.add("integration")
    session.file_ownership = WriteCoordinator()
    from agentcore.runtime.coordination.append_guard import declare_plan_artifacts

    declare_plan_artifacts(live, session.file_ownership)
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        result = _merge_into_active_coordination(
            _fake_merge_tool(),
            _plan(
                RunSpec(
                    run_id="fe_dup",
                    role="前端 App",
                    task="再写 App.tsx",
                    deliverable=Deliverable(artifacts=["App.tsx"]),
                )
            ),
            session,
            execution_id="e-done-own",
            seed_completed=None,
            complexity_hint="",
            call_idx=2,
        )
        assert result.success is True
        assert session.ensure_file_ownership().owner_of("App.tsx") == "fe_dup"
        assert session.total_workers >= 2
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination("e-done-own")


def test_session_ownership_snapshot_roundtrip():
    from agentcore.workspace.write_claims import WriteCoordinator

    session = CoordinationSession(execution_id="e-snap", total_workers=1)
    ledger = session.ensure_file_ownership()
    assert isinstance(ledger, WriteCoordinator)
    ledger.declare("f.md", "w1", frozenset())
    snap = session.snapshot()
    assert snap.file_ownership.get("_v") == 3
    owners = snap.file_ownership.get("owners", {})
    assert any(k.endswith("f.md") or k.endswith("\0f.md") for k in owners)
    assert "w1" in owners.values()
    restored = CoordinationSession.from_snapshot(snap)
    assert restored.ensure_file_ownership().owner_of("f.md") == "w1"


# --- C3: reject before durable run_plan emit (零图副作用) ---


async def test_sibling_artifact_same_path_emits_run_plan(monkeypatch):
    """同批交付物交叉、不同座位 → 能开工（有 run_plan）。"""
    import agentcore.tools.builtin.delegate.tool as delegate_tool_mod
    from agentcore.runtime.events import EventSink, EventType
    from agentcore.runtime.facts import FactKind, TurnFactLog, current_fact_log
    from tests.conftest import LogSpy
    from tests.delegate.conftest import ctx, tool

    clear_active_coordination()
    log = TurnFactLog()
    token = current_fact_log.set(log)
    spy = LogSpy()
    monkeypatch.setattr(delegate_tool_mod, "logger", spy)
    sink = EventSink()
    t = tool(MagicMock(), sink=sink)
    try:
        result = await t.execute(
            {
                "tasks": [
                    {
                        "role": "前端",
                        "task": "写 App",
                        "deliverable": {"artifacts": ["src/App.tsx"]},
                    },
                    {
                        "role": "整合",
                        "task": "也写 App",
                        "deliverable": {"artifacts": ["src/App.tsx"]},
                    },
                ],
                "coordinate": False,
            },
            ctx(),
        )
        assert result.success is True
        run_plans = [e for e in sink._history if e.type is EventType.RUN_PLAN]
        assert len(run_plans) == 1
        assert any(e.get("kind") == FactKind.PLAN_SNAPSHOT.value for e in log.entries())
    finally:
        current_fact_log.reset(token)


async def test_sibling_same_path_then_second_batch_adds_swimlane():
    """同路径不同座位第一批能开工；再派不同文件仍可追加。"""
    from agentcore.runtime.events import EventSink, EventType
    from tests.delegate.conftest import Provider, ctx, tool

    clear_active_coordination()
    sink = EventSink()
    t = tool(Provider(["AOUT", "BOUT"]), sink=sink)
    first = await t.execute(
        {
            "tasks": [
                {
                    "role": "前端",
                    "task": "写 App",
                    "deliverable": {"artifacts": ["src/App.tsx"]},
                },
                {
                    "role": "整合",
                    "task": "也写 App",
                    "deliverable": {"artifacts": ["src/App.tsx"]},
                },
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert first.success is True
    run_plans = [e for e in sink._history if e.type is EventType.RUN_PLAN]
    assert len(run_plans) == 1
    roles = {a.get("role") for a in (run_plans[0].payload.get("agents") or [])}
    assert "前端" in roles and "整合" in roles


async def test_append_same_path_different_seat_emits_second_run_plan():
    """活跃协调上文件重叠、不同座位追加 → 第二张 run_plan。"""
    from agentcore.runtime.events import EventSink, EventType
    from tests.delegate.conftest import ctx, tool
    from tests.delegate.test_coordination_secondary_delegate import _SlowWorkers

    clear_active_coordination()
    sink = EventSink()
    t = tool(_SlowWorkers(["A", "B", "C"], delay=0.5), sink=sink)
    first = await t.execute(
        {
            "tasks": [
                {
                    "role": "骨架",
                    "task": "写 site/index.html",
                    "deliverable": {
                        "artifacts": ["site/index.html"],
                        "form": "files",
                    },
                },
                {"role": "文案", "task": "写文案"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert first.success is True
    session = active_coordination("e")
    assert session is not None
    drive = session.drive_task
    assert drive is not None and not drive.done()
    plans_after_first = [e for e in sink._history if e.type is EventType.RUN_PLAN]
    assert len(plans_after_first) == 1

    second = await t.execute(
        {
            "tasks": [
                {
                    "role": "前端整站",
                    "task": "重写整站",
                    "deliverable": {
                        "artifacts": ["site/index.html"],
                        "form": "files",
                    },
                }
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert second.success is True
    plans_after = [e for e in sink._history if e.type is EventType.RUN_PLAN]
    assert len(plans_after) == 2

    drive.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drive
    clear_active_coordination("e")


@pytest.mark.asyncio
async def test_try_start_same_path_different_seat_creates_session():
    """try_start：同路径不同座位 → 建 session。"""
    from agentcore.runtime.coordination.host import try_start_coordination
    from agentcore.runtime.runs.types import Deliverable

    clear_active_coordination()
    plan = _plan(
        RunSpec(
            run_id="a",
            role="前端",
            task="写",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        ),
        RunSpec(
            run_id="b",
            role="整合",
            task="也写",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        ),
    )
    tool = _fake_merge_tool()
    tool._depth = 0
    tool._checkpoint_enabled = False
    started = try_start_coordination(
        tool,
        plan,
        execution_id="e-sib-pre",
        seed_completed=None,
        complexity_hint="standard",
        call_idx=1,
        coordinate=True,
    )
    assert started is not None
    assert started.success is True
    session = active_coordination("e-sib-pre")
    assert session is not None
    drive = session.drive_task
    if drive is not None and not drive.done():
        drive.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drive
    clear_active_coordination("e-sib-pre")


def test_nested_declare_transfers_paths_from_parent_not_all():
    """Nested child artifacts ⊆ parent-owned → path-level handoff; other parent paths stay."""
    from agentcore.runtime.coordination.append_guard import (
        declare_nested_drive_artifacts,
        declare_plan_artifacts,
    )
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace import write_claims as wc
    from agentcore.workspace.write_claims import WriteCoordinator

    parent_plan = _plan(
        RunSpec(
            run_id="backend-fix",
            role="后端补齐",
            task="占位",
            deliverable=Deliverable(
                artifacts=[
                    "src/storage/db.ts",
                    "src/storage/index.ts",
                    "src/tools/base-tool.ts",
                ]
            ),
        )
    )
    ownership = WriteCoordinator()
    declare_plan_artifacts(parent_plan, ownership)
    assert ownership.owner_of("src/storage/db.ts") == "backend-fix"
    assert ownership.owner_of("src/tools/base-tool.ts") == "backend-fix"

    child_plan = _plan(
        RunSpec(
            run_id="storage",
            role="存储层",
            task="写 storage",
            parent_run_id="backend-fix",
            depth=1,
            deliverable=Deliverable(
                artifacts=["src/storage/db.ts", "src/storage/index.ts"]
            ),
        ),
        RunSpec(
            run_id="tools",
            role="工具系统",
            task="写 tools",
            parent_run_id="backend-fix",
            depth=1,
            deliverable=Deliverable(artifacts=["src/tools/base-tool.ts"]),
        ),
    )
    tool = MagicMock()
    tool._depth = 1

    original = wc.resolve_write_coordinator
    wc.resolve_write_coordinator = lambda **_kwargs: ownership  # type: ignore[assignment]
    try:
        conflicts = declare_nested_drive_artifacts(
            tool, child_plan, execution_id="e-nested"
        )
    finally:
        wc.resolve_write_coordinator = original

    assert conflicts == []
    assert ownership.owner_of("src/storage/db.ts") == "storage"
    assert ownership.owner_of("src/storage/index.ts") == "storage"
    assert ownership.owner_of("src/tools/base-tool.ts") == "tools"


def test_nested_declare_skipped_at_depth_zero():
    from agentcore.runtime.coordination.append_guard import declare_nested_drive_artifacts
    from agentcore.runtime.runs.types import Deliverable
    from agentcore.workspace import write_claims as wc
    from agentcore.workspace.write_claims import WriteCoordinator

    ownership = WriteCoordinator()
    ownership.declare("a.md", "lead", frozenset())
    plan = _plan(
        RunSpec(
            run_id="child",
            role="子",
            task="写",
            parent_run_id="lead",
            depth=0,
            deliverable=Deliverable(artifacts=["a.md"]),
        )
    )
    tool = MagicMock()
    tool._depth = 0

    original = wc.resolve_write_coordinator
    wc.resolve_write_coordinator = lambda **_kwargs: ownership  # type: ignore[assignment]
    try:
        conflicts = declare_nested_drive_artifacts(tool, plan, execution_id="e0")
    finally:
        wc.resolve_write_coordinator = original

    assert conflicts == []
    # Depth 0 skipped — ownership unchanged.
    assert ownership.owner_of("a.md") == "lead"


# --- replan.adds shares append seat/artifact admit + lock transfer ------------


class _FakeReplanTools:
    def list_all(self):
        return []


def _fake_replan_tool(*, execution_id: str, plan, completed: dict | None = None):
    """Minimal DelegateTool stand-in for apply_replan under active coordination."""
    from agentcore.runtime.delegate.supervised import SupervisedRun
    from agentcore.runtime.runs import BoundaryReason
    from agentcore.runtime.runs.types import RunPhase, RunState

    tool = MagicMock()
    tool._tools = _FakeReplanTools()
    tool._captain_run_id = "cap"
    tool._depth = 0
    tool._topology_lock = False
    tool._folder_id = "test_birth"
    tool._supervised = SupervisedRun(
        plan=plan,
        completed=completed
        or {
            rid: RunState(phase=RunPhase.COMPLETED)
            for rid in ()  # filled by caller via session; seed empty here
        },
        execution_id=execution_id,
        reason=BoundaryReason.SCOPE,
        boundary_run_ids=[],
    )
    return tool


@pytest.mark.asyncio
async def test_replan_adds_same_seat_takeover_transfers_write_locks():
    """活跃协调下 replan.adds 同座位接手 → auto replaces + 写锁归新主。"""
    from agentcore.runtime.coordination.append_guard import declare_plan_artifacts
    from agentcore.runtime.delegate.supervised import apply_replan
    from agentcore.runtime.runs.types import Deliverable, RunPhase, RunState

    clear_active_coordination()
    live = _plan(
        RunSpec(
            run_id="fe1",
            role="前端改造",
            task="令牌化",
            deliverable=Deliverable(
                artifacts=["src/ui/ReasoningGraph.tsx", "src/game/GameScene.ts"]
            ),
        ),
        RunSpec(
            run_id="qa",
            role="页面 QA",
            task="仍在跑",
            deliverable=Deliverable(artifacts=["qa/notes.md"]),
        ),
    )
    session = CoordinationSession(execution_id="e-replan-xfer", total_workers=2)
    session.live_plan = live
    session.completed_run_ids.add("fe1")
    declare_plan_artifacts(live, session.ensure_file_ownership())
    assert session.ensure_file_ownership().owner_of("src/ui/ReasoningGraph.tsx") == "fe1"
    set_active_coordination(session)
    try:
        completed = {"fe1": RunState(phase=RunPhase.COMPLETED)}
        tool = _fake_replan_tool(
            execution_id="e-replan-xfer", plan=live, completed=completed
        )
        errors = await apply_replan(
            tool,
            live,
            completed,
            binds=[],
            steers=[],
            adds=[
                {
                    "role": "前端改造",
                    "task": "完成 ReasoningGraph.tsx 与 GameScene.ts 落盘",
                    "deliverable": {
                        "artifacts": [
                            "src/ui/ReasoningGraph.tsx",
                            "src/game/GameScene.ts",
                        ]
                    },
                }
            ],
        )
        assert errors == []
        new = next(n for n in live.nodes if n.run_id != "fe1" and n.run_id != "qa")
        assert new.replaces_run_id == "fe1"
        ownership = session.ensure_file_ownership()
        assert ownership.owner_of("src/ui/ReasoningGraph.tsx") == new.run_id
        assert ownership.owner_of("src/game/GameScene.ts") == new.run_id
        assert session.total_workers == 3
    finally:
        clear_active_coordination("e-replan-xfer")


@pytest.mark.asyncio
async def test_replan_adds_rejects_incomplete_seat_with_append_family_message():
    """活跃协调下 replan.adds 撞未完成同座位 → 拒，文案与 append 同族。"""
    from agentcore.runtime.delegate.supervised import apply_replan
    from agentcore.runtime.runs.types import Deliverable

    clear_active_coordination()
    live = _plan(
        RunSpec(
            run_id="price",
            role="定价调研员",
            task="调研定价",
            deliverable=Deliverable(artifacts=["research/pricing.md"]),
        ),
        RunSpec(
            run_id="channel",
            role="渠道调研员",
            task="调研渠道",
            deliverable=Deliverable(artifacts=["research/channel.md"]),
        ),
    )
    session = CoordinationSession(execution_id="e-replan-rej", total_workers=2)
    session.live_plan = live
    set_active_coordination(session)
    try:
        tool = _fake_replan_tool(execution_id="e-replan-rej", plan=live, completed={})
        before_ids = {n.run_id for n in live.nodes}
        errors = await apply_replan(
            tool,
            live,
            {},
            binds=[],
            steers=[],
            adds=[
                {
                    "role": "定价调研员",
                    "task": "再派定价",
                    "deliverable": {"artifacts": ["research/pricing.md"]},
                }
            ],
        )
        assert errors
        assert any("【队员追加已拒绝" in e for e in errors)
        assert any("座位" in e or "重叠" in e for e in errors)
        assert {n.run_id for n in live.nodes} == before_ids
    finally:
        clear_active_coordination("e-replan-rej")


def test_never_started_seats_excluded_from_isomorphic_denominator():
    """从未 run_started 的空座位不得进 isomorphic「还在跑」分母。"""
    live = _plan(
        RunSpec(run_id="a", role="前端", task="写 App"),
        RunSpec(run_id="b", role="整合", task="也写 App"),
    )
    twin = _plan(
        RunSpec(run_id="a2", role="前端", task="写 App 页面"),
        RunSpec(run_id="b2", role="整合", task="也写 App 汇总"),
    )
    assert is_isomorphic_redelegation(twin, live, completed_run_ids=set()) is True
    assert (
        is_isomorphic_redelegation(
            twin, live, completed_run_ids=set(), started_run_ids=set()
        )
        is False
    )
    assert (
        is_isomorphic_redelegation(
            twin, live, completed_run_ids=set(), started_run_ids={"a", "b"}
        )
        is True
    )


def test_same_batch_plan_excludes_host_terminals_from_sibling():
    """host∪new 含已完成同路径节点 → 不是同批 sibling；只扫新批次。"""
    from agentcore.runtime.coordination.append_guard import (
        find_sibling_artifact_crosses,
        same_batch_plan,
    )
    from agentcore.runtime.runs.types import Deliverable

    host = RunSpec(
        run_id="h1",
        role="前端",
        task="写完了",
        deliverable=Deliverable(artifacts=["src/App.tsx"]),
    )
    newbie = RunSpec(
        run_id="n1",
        role="前端",
        task="续写",
        deliverable=Deliverable(artifacts=["src/App.tsx"]),
    )
    merged = _plan(host, newbie)
    assert find_sibling_artifact_crosses(merged)
    batch = same_batch_plan(merged, exclude_run_ids={"h1"})
    assert [n.run_id for n in batch.nodes] == ["n1"]
    assert find_sibling_artifact_crosses(batch) == []


@pytest.mark.asyncio
async def test_try_start_late_sibling_wipes_empty_seats_and_unlocks_isomorphic(
    monkeypatch,
):
    """admit 漏过 → try_start 才拒：不得留下 1/2 空座位；随后同构再派不得连拒。"""
    from agentcore.runtime.events import EventSink, EventType
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.journal.fold import plan_from_journal
    from tests.delegate.conftest import Provider, ctx, tool

    clear_active_coordination()
    log = TurnFactLog()
    token = current_fact_log.set(log)
    monkeypatch.setattr(
        "agentcore.runtime.coordination.host.admit_before_run_plan_emit",
        lambda *_a, **_k: None,
    )
    sink = EventSink()
    t = tool(Provider(["AOUT", "BOUT"]), sink=sink)
    try:
        rejected = await t.execute(
            {
                "tasks": [
                    {
                        "role": "前端",
                        "task": "写 App",
                        "deliverable": {"artifacts": ["src/App.tsx"]},
                    },
                    {
                        "role": "前端",
                        "task": "也写 App",
                        "deliverable": {"artifacts": ["src/App.tsx"]},
                    },
                ],
                "coordinate": True,
            },
            ctx(),
        )
        assert rejected.success is False
        assert rejected.contract_failure is True
        assert "座位" in (rejected.error or "") or "重叠" in (rejected.error or "")
        assert active_coordination("e") is None
        folded = plan_from_journal(log.entries())
        leftover_ids = {n.run_id for n in folded.nodes} if folded is not None else set()
        assert leftover_ids == set()
        run_plans = [e for e in sink._history if e.type is EventType.RUN_PLAN]
        assert run_plans == []

        ok = await t.execute(
            {
                "tasks": [
                    {
                        "role": "前端",
                        "task": "写 App",
                        "deliverable": {"artifacts": ["src/App.tsx"]},
                    },
                    {
                        "role": "整合",
                        "task": "写汇总",
                        "deliverable": {"artifacts": ["src/summary.md"]},
                    },
                ],
                "coordinate": True,
            },
            ctx(),
        )
        assert ok.success is True
        assert "同构" not in (ok.error or "")
        session = active_coordination("e")
        assert session is not None
        drive = session.drive_task
        if drive is not None and not drive.done():
            drive.cancel()
            with pytest.raises(asyncio.CancelledError):
                await drive
    finally:
        current_fact_log.reset(token)
        clear_active_coordination("e")


@pytest.mark.asyncio
async def test_try_start_retracts_already_written_empty_seats():
    """万一已写下 run_plan / snapshot，try_start 拒后必须擦掉从未开工座位。"""
    from agentcore.runtime.coordination.host import try_start_coordination
    from agentcore.runtime.delegate.steer import record_plan_snapshot
    from agentcore.runtime.events import EventSink, EventType, run_plan
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.journal.fold import plan_from_journal
    from agentcore.runtime.runs.types import Deliverable

    clear_active_coordination()
    log = TurnFactLog()
    token = current_fact_log.set(log)
    sink = EventSink()
    plan = _plan(
        RunSpec(
            run_id="a",
            role="前端",
            task="写",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        ),
        RunSpec(
            run_id="b",
            role="前端",
            task="也写",
            deliverable=Deliverable(artifacts=["App.tsx"]),
        ),
    )
    record_plan_snapshot(plan)
    sink.emit(
        run_plan(
            execution_id="e-late-wipe",
            plan_type="multi_agent",
            task_summary="2",
            agents=[{"id": "a", "role": "前端"}, {"id": "b", "role": "前端"}],
            runs=[{"id": "a"}, {"id": "b"}],
        )
    )
    tool = _fake_merge_tool()
    tool._sink = sink
    tool._depth = 0
    tool._checkpoint_enabled = False
    try:
        started = try_start_coordination(
            tool,
            plan,
            execution_id="e-late-wipe",
            seed_completed=None,
            complexity_hint="standard",
            call_idx=1,
            coordinate=True,
        )
        assert started is not None
        assert started.success is False
        assert active_coordination("e-late-wipe") is None
        folded = plan_from_journal(log.entries())
        assert folded is None or [n.run_id for n in folded.nodes] == []
        skipped = [e for e in sink._history if e.type is EventType.RUN_SKIPPED]
        assert {e.payload.get("run_id") for e in skipped} == {"a", "b"}
        leftover = folded if folded is not None else _plan()
        twin = _plan(
            RunSpec(run_id="a2", role="前端", task="写"),
            RunSpec(run_id="b2", role="整合", task="也写"),
        )
        assert (
            is_isomorphic_redelegation(
                twin, leftover, completed_run_ids=set(), started_run_ids=set()
            )
            is False
        )
    finally:
        current_fact_log.reset(token)
        clear_active_coordination("e-late-wipe")


@pytest.mark.asyncio
async def test_try_start_sibling_ignores_completed_host_same_path():
    """已完成同座+同路径续派：try_start 不得把 host∪new 当同批 sibling。"""
    from agentcore.runtime.coordination.host import try_start_coordination
    from agentcore.runtime.runs.types import Deliverable, RunPhase, RunState

    clear_active_coordination()
    plan = _plan(
        RunSpec(
            run_id="h1",
            role="前端",
            task="已完成",
            deliverable=Deliverable(artifacts=["src/App.tsx"]),
        ),
        RunSpec(
            run_id="n1",
            role="前端",
            task="续写",
            deliverable=Deliverable(artifacts=["src/App.tsx"]),
        ),
    )
    seed = {"h1": RunState(phase=RunPhase.COMPLETED)}
    tool = _fake_merge_tool()
    tool._depth = 0
    tool._checkpoint_enabled = False
    tool._conversation_id = "c-host-union"
    started = try_start_coordination(
        tool,
        plan,
        execution_id="e-host-union",
        seed_completed=seed,
        complexity_hint="standard",
        call_idx=1,
        coordinate=True,
    )
    try:
        assert started is not None
        assert started.success is True
        session = active_coordination("e-host-union")
        assert session is not None
        drive = session.drive_task
        if drive is not None and not drive.done():
            drive.cancel()
            with pytest.raises(asyncio.CancelledError):
                await drive
    finally:
        clear_active_coordination("e-host-union")


@pytest.mark.asyncio
async def test_cancel_worker_vacates_unstarted_when_session_inactive():
    """无 active session 时仍能划掉从未开工座位，划完能再派。"""
    from agentcore.runtime.coordination.tools import CancelWorkerTool
    from tests.delegate.conftest import Provider, ctx
    from tests.delegate.conftest import tool as make_tool

    clear_active_coordination()
    live = _plan(
        RunSpec(run_id="empty1", role="前端", task="写 App"),
        RunSpec(run_id="empty2", role="整合", task="写汇总"),
    )
    session = CoordinationSession(execution_id="e", total_workers=2)
    session.live_plan = live
    session.active = False
    set_active_coordination(session)
    try:
        cancel = CancelWorkerTool()
        result = await cancel.execute({"run_id": "empty1", "reason": "空座位"}, ctx())
        assert result.success is True
        assert "已从队列撤出" in (result.output or "")
        assert "empty1" in session.vacated_run_ids
        assert "协调模式" not in (result.error or "")

        again = await cancel.execute({"run_id": "empty2"}, ctx())
        assert again.success is True
        assert "empty2" in session.vacated_run_ids

        t = make_tool(Provider(["AOUT", "BOUT"]))
        ok = await t.execute(
            {
                "tasks": [
                    {"role": "前端", "task": "写 App"},
                    {"role": "整合", "task": "写汇总"},
                ],
                "coordinate": True,
            },
            ctx(),
        )
        assert ok.success is True
        assert "同构" not in (ok.error or "")
        live_session = active_coordination("e")
        drive = getattr(live_session, "drive_task", None) if live_session else None
        if drive is not None and not drive.done():
            drive.cancel()
            with pytest.raises(asyncio.CancelledError):
                await drive
    finally:
        clear_active_coordination("e")


@pytest.mark.asyncio
async def test_cancel_worker_vacates_journal_unstarted_without_session():
    """没有协调 session 时，journal 上从未开工的座位仍可划掉。"""
    from agentcore.runtime.coordination.tools import CancelWorkerTool
    from agentcore.runtime.delegate.steer import record_plan_snapshot
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.journal.fold import plan_from_journal
    from tests.delegate.conftest import ctx

    clear_active_coordination()
    log = TurnFactLog()
    token = current_fact_log.set(log)
    plan = _plan(
        RunSpec(run_id="ghost_a", role="前端", task="写"),
        RunSpec(run_id="ghost_b", role="整合", task="也写"),
    )
    record_plan_snapshot(plan)
    try:
        cancel = CancelWorkerTool()
        result = await cancel.execute({"run_id": "ghost_a"}, ctx())
        assert result.success is True
        assert "已从队列撤出" in (result.output or "")
        assert "协调模式" not in (result.error or "")
        folded = plan_from_journal(log.entries())
        assert folded is not None
        assert [n.run_id for n in folded.nodes] == ["ghost_b"]
        second = await cancel.execute({"run_id": "ghost_b"}, ctx())
        assert second.success is True
        folded2 = plan_from_journal(log.entries())
        assert folded2 is None or [n.run_id for n in folded2.nodes] == []
    finally:
        current_fact_log.reset(token)
        clear_active_coordination()
