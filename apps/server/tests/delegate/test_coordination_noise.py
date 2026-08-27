"""CEO 协调层唤醒降噪（失败才叫醒 / 空转退避 / 里程碑 synthesis / 缺依赖搭车）。

对应五项降噪措施中的协调层部分（前端团队进展卡片见桌面端 vitest）：
1. 例行成功完成不叫醒；失败立刻叫醒并收口级联 skip；终局 / 升级不拖延。
2. 空转唤醒降频：idle 巡查按 ``2**idle_streak`` 退避；忙等（有 in-flight）不叫醒 CEO；
   无人 in-flight 的卡死仍发 patrol nudge；真实事件重置。
3. synthesis 里程碑化：工具描述 / 注入文案强调里程碑，例行完成不写。
5. suspect_missing_dep 搭车既有注入通道呈现给 CEO（不新增独立唤醒）。
"""

from __future__ import annotations

import asyncio

import pytest

import agentcore.runtime.coordination.wait as coord_wait
from agentcore.runtime.coordination.inject import format_coordination_events
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    clear_active_coordination,
    current_execution_id,
    set_active_coordination,
)
from agentcore.runtime.coordination.tools import UpdateSynthesisTool
from agentcore.runtime.coordination.wait import await_coordination_injection
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from tests.conftest import LogSpy


def _wc(run_id: str, role: str = "R", *, status: str = "completed") -> CoordinationEvent:
    return CoordinationEvent(
        kind=CoordinationEventKind.WORKER_COMPLETED,
        payload={"run_id": run_id, "role": role, "status": status, "summary": "ok"},
    )


def _all_done(*, completed: int, total: int) -> CoordinationEvent:
    return CoordinationEvent(
        kind=CoordinationEventKind.ALL_COMPLETED,
        payload={"completed": completed, "total": total},
    )


def _esc(run_id: str = "w9", role: str = "研究员") -> CoordinationEvent:
    return CoordinationEvent(
        kind=CoordinationEventKind.ESCALATION,
        payload={"run_id": run_id, "role": role, "question": "范围变了？"},
    )


async def _inject(execution_id: str, *, timeout: float = 2.0):
    """Bind the session active and run one ``await_coordination_injection`` cycle."""
    loop = asyncio.get_running_loop()
    token = current_execution_id.set(execution_id)
    try:
        t0 = loop.time()
        msgs = await asyncio.wait_for(await_coordination_injection([]), timeout=timeout)
        elapsed = loop.time() - t0
    finally:
        current_execution_id.reset(token)
    return msgs, elapsed


# --- 例行完成不叫醒；失败 / 终局立刻叫醒 ---------------------------------------


async def test_success_completion_does_not_wake():
    """成功完成不叫醒 CEO：注入在短超时内完不成。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-success-hold", total_workers=3)
    set_active_coordination(session)
    session.post(_wc("w1"))
    try:
        with pytest.raises(TimeoutError):
            await _inject("e-success-hold", timeout=0.35)
        assert session._deferred_progress
    finally:
        clear_active_coordination()


async def test_three_successes_still_do_not_wake():
    """三个成功完成也不再靠计数叫醒。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-count", total_workers=6)
    set_active_coordination(session)
    session.post(_wc("w1"))
    session.post(_wc("w2"))
    session.post(_wc("w3"))
    try:
        with pytest.raises(TimeoutError):
            await _inject("e-count", timeout=0.35)
        assert len(session._deferred_progress) >= 1
    finally:
        clear_active_coordination()


async def test_failed_completion_wakes_immediately():
    """失败立刻叫醒，不攒批。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-fail", total_workers=3)
    set_active_coordination(session)
    session.post(_wc("w1", status="failed"))
    try:
        msgs, elapsed = await _inject("e-fail", timeout=1.0)
    finally:
        clear_active_coordination()
    assert len(msgs) == 1
    assert "worker_completed" in (msgs[0].content or "")
    assert "failed" in (msgs[0].content or "")
    assert elapsed < 0.5


async def test_success_rides_failure_wake():
    """先成功暂存，失败到达时一次注入带上成功记录。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-ride", total_workers=4)
    set_active_coordination(session)
    session.post(_wc("w1"))
    session.post(_wc("w2", status="failed"))
    try:
        msgs, elapsed = await _inject("e-ride", timeout=1.0)
    finally:
        clear_active_coordination()
    assert len(msgs) == 1
    text = msgs[0].content or ""
    assert text.count("- worker_completed（") == 2
    assert "failed" in text
    assert elapsed < 0.5


async def test_skip_cancel_do_not_wake_alone():
    """单独的 skip / cancel 不叫醒（等失败或终局）。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-skip", total_workers=3)
    set_active_coordination(session)
    session.post(_wc("w1", status="skipped"))
    session.post(_wc("w2", status="cancelled"))
    try:
        with pytest.raises(TimeoutError):
            await _inject("e-skip", timeout=0.35)
    finally:
        clear_active_coordination()


async def test_failure_coalesces_cascade_skips(monkeypatch):
    """失败后短暂收口级联 skip，合成一次注入。"""
    monkeypatch.setattr(coord_wait, "_CASCADE_COALESCE_S", 0.2)
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-cascade", total_workers=4)
    set_active_coordination(session)
    session.post(_wc("w1", status="failed"))

    async def _post_skips() -> None:
        await asyncio.sleep(0.02)
        session.post(_wc("w2", status="skipped"))
        session.post(_wc("w3", status="skipped"))

    try:
        task = asyncio.create_task(_post_skips())
        msgs, elapsed = await _inject("e-cascade", timeout=2.0)
        await task
    finally:
        clear_active_coordination()
    text = msgs[0].content or ""
    assert "failed" in text
    assert text.count("- worker_completed（") == 3
    assert elapsed < 1.0


async def test_necessary_event_wakes_with_stashed_progress():
    """升级到达时带上已暂存的成功完成，立即唤醒。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-esc", total_workers=4)
    set_active_coordination(session)
    session.post(_wc("w1"))

    async def _post_escalation() -> None:
        await asyncio.sleep(0.03)
        session.post(_esc("w2"))

    try:
        task = asyncio.create_task(_post_escalation())
        msgs, elapsed = await _inject("e-esc", timeout=2.0)
        await task
    finally:
        clear_active_coordination()
    assert len(msgs) == 1
    text = msgs[0].content or ""
    assert "escalation" in text
    assert "worker_completed" in text
    assert elapsed < 1.0


async def test_terminal_all_completed_carries_stashed_progress():
    """终局立即唤醒并收口，暂存的成功完成一并注入。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-term", total_workers=2)
    set_active_coordination(session)
    session.post(_wc("w1"))

    async def _post_all_completed() -> None:
        await asyncio.sleep(0.03)
        session.post(_all_done(completed=2, total=2))

    try:
        task = asyncio.create_task(_post_all_completed())
        msgs, elapsed = await _inject("e-term", timeout=2.0)
        await task
    finally:
        clear_active_coordination()
    assert len(msgs) == 1
    text = msgs[0].content or ""
    assert "all_completed" in text
    assert "worker_completed" in text
    assert elapsed < 1.0
    assert session.active is False  # 收口


# --- 空转唤醒降频（Task 2）------------------------------------------------------


def test_idle_wait_timeout_backoff(monkeypatch):
    """idle 等待随 idle_streak 指数退避，封顶 ``_COORD_WAIT_TIMEOUT_MAX_S``。"""
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 10.0)
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_MAX_S", 100.0)
    s = CoordinationSession(execution_id="e", total_workers=2)
    s.idle_streak = 0
    assert coord_wait._idle_wait_timeout(s) == 10.0
    s.idle_streak = 1
    assert coord_wait._idle_wait_timeout(s) == 20.0
    s.idle_streak = 2
    assert coord_wait._idle_wait_timeout(s) == 40.0
    s.idle_streak = 3
    assert coord_wait._idle_wait_timeout(s) == 80.0
    s.idle_streak = 4
    assert coord_wait._idle_wait_timeout(s) == 100.0  # cap
    s.idle_streak = 12
    assert coord_wait._idle_wait_timeout(s) == 100.0
    s.idle_streak = -3
    assert coord_wait._idle_wait_timeout(s) == 10.0  # max(0, streak)


def test_idle_wait_timeout_production_curve():
    """生产常数：base 120s × 2**streak，封顶 600s。"""
    s = CoordinationSession(execution_id="e-prod", total_workers=2)
    assert coord_wait._COORD_WAIT_TIMEOUT_S == 120.0
    assert coord_wait._COORD_WAIT_TIMEOUT_MAX_S == 600.0
    s.idle_streak = 0
    assert coord_wait._idle_wait_timeout(s) == 120.0
    s.idle_streak = 1
    assert coord_wait._idle_wait_timeout(s) == 240.0
    s.idle_streak = 2
    assert coord_wait._idle_wait_timeout(s) == 480.0
    s.idle_streak = 3
    assert coord_wait._idle_wait_timeout(s) == 600.0
    s.idle_streak = 8
    assert coord_wait._idle_wait_timeout(s) == 600.0


async def test_idle_held_inflight_second_wait_uses_widened_timeout(monkeypatch):
    """忙等 hold 递增 idle_streak，下一次空转等待吃加宽 timeout；不叫醒 CEO。"""
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 10.0)
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_MAX_S", 100.0)
    monkeypatch.setattr(coord_wait, "_WAIT_HEARTBEAT_S", 0.01)
    seen_timeouts: list[float] = []

    async def _instant_wait(_session: CoordinationSession, *, timeout: float):
        seen_timeouts.append(timeout)
        if len(seen_timeouts) >= 5:
            return [_wc("w1", status="failed")]
        return []

    monkeypatch.setattr(coord_wait, "_wait_events_with_ux", _instant_wait)
    spy = LogSpy()
    monkeypatch.setattr(coord_wait, "logger", spy)
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-yield-widen", total_workers=2)
    session._running_workers["w1"] = "研究员"
    session._worker_started_at["w1"] = __import__("time").monotonic()
    session.mark_worker_busy("w1", "llm")
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        msgs, _ = await _inject("e-yield-widen", timeout=1.0)
        assert "worker_completed" in (msgs[0].content or "")
        assert session.idle_streak == 0
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination()

    # hold 路径：先按 streak 等满 idle 窗，再短等 heartbeat，bump 后下一轮加宽。
    assert seen_timeouts[:4] == [10.0, 0.01, 20.0, 0.01]
    holds = [kw for name, kw in spy.events if name == "coordination.idle_yield_held_inflight"]
    assert [kw["idle_streak"] for kw in holds] == [1, 2]
    yields = [kw for name, kw in spy.events if name == "coordination.idle_yield_to_captain"]
    assert yields == []


async def test_idle_held_inflight_bumps_backoff_and_real_event_resets(monkeypatch):
    """忙等 hold 递增 idle_streak 且不注入 CEO；真实失败事件叫醒并重置退避。"""
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_MAX_S", 1.0)
    monkeypatch.setattr(coord_wait, "_WAIT_HEARTBEAT_S", 0.05)
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-yield-backoff", total_workers=2)
    session._running_workers["w1"] = "研究员"
    session._worker_started_at["w1"] = __import__("time").monotonic()
    session.mark_worker_busy("w1", "llm")
    session.drive_task = asyncio.create_task(asyncio.sleep(30))
    set_active_coordination(session)
    try:
        async def _fail_later() -> None:
            await asyncio.sleep(0.25)
            session.post(_wc("w1", status="failed"))

        fail_task = asyncio.create_task(_fail_later())
        msgs3, _ = await _inject("e-yield-backoff", timeout=3.0)
        await fail_task
        assert session.idle_streak == 0
        assert "worker_completed" in (msgs3[0].content or "")
    finally:
        session.drive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session.drive_task
        clear_active_coordination()


async def test_idle_timeout_bumps_backoff_and_real_event_resets(monkeypatch):
    """空转超时递增 idle_streak（降频）且发出巡查 nudge；真实事件重置退避。"""
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_MAX_S", 1.0)
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-idle", total_workers=2)
    set_active_coordination(session)
    try:
        msgs1, _ = await _inject("e-idle", timeout=2.0)
        assert session.idle_streak == 1
        # 保留卡死巡查语义：仍发周期性 patrol nudge（可 cancel_worker）。
        assert "等待团队事件超时" in (msgs1[0].content or "")
        msgs2, _ = await _inject("e-idle", timeout=2.0)
        assert session.idle_streak == 2
        # 真实事件到达 → 退避清零。
        session.post(_wc("w1", status="failed"))
        msgs3, _ = await _inject("e-idle", timeout=2.0)
        assert session.idle_streak == 0
        assert "worker_completed" in (msgs3[0].content or "")
    finally:
        clear_active_coordination()


# --- synthesis 里程碑化（Task 3）-----------------------------------------------


def test_update_synthesis_tool_is_milestone_only():
    tool = UpdateSynthesisTool(sink=EventSink())
    desc = tool.schema.description
    assert "里程碑" in desc
    assert "例行的单个 worker 完成" in desc
    assert "禁止" in desc and "纯进度播报" in desc
    assert "新结论" in desc or "方向修正" in desc
    draft_desc = tool.schema.parameters["properties"]["draft"]["description"]
    assert "禁止纯进度播报" in draft_desc


def test_inject_footer_teaches_milestone_synthesis():
    session = CoordinationSession(execution_id="e", total_workers=3)
    text = format_coordination_events(session, [_wc("w1")])
    assert "只在【里程碑】写合成草稿" in text
    assert "例行的单个 worker 完成【不写】" in text
    assert "可静默" in text
    assert "三选一" in text
    assert "纯进度播报" in text
    assert "进度旁白不得焊进终稿" in text or "焊进终稿" in text
    assert "谁在后台、完成后会再汇报" not in text


def test_inject_interjection_requires_user_first_reply():
    session = CoordinationSession(execution_id="e", total_workers=2)
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.USER_INTERJECTION,
                payload={"interjection_id": "inj-1", "content": "优先做登录页"},
            )
        ],
    )
    assert "先回用户" in text or "先】用可见正文" in text or "响应该句" in text
    assert "旧进度旁白" in text
    assert "优先做登录页" in text


# --- suspect_missing_dep 搭车注入通道（Task 5）--------------------------------


def test_builder_collects_suspect_missing_dep_advisory():
    # DAG（另有节点声明 depends_on）里某节点提及上游产出却漏声明依赖 → 建图提示。
    plan, errs = build_run_plan(
        [
            {"id": "r1", "role": "研究员", "task": "调研竞品"},
            {"id": "a", "role": "分析师", "task": "整理数据", "depends_on": ["r1"]},
            {"id": "w", "role": "写手", "task": "基于上游产出撰写报告"},
        ],
        id_prefix="t",
    )
    assert errs == []
    assert any("depends_on 为空" in a for a in plan.advisories)
    assert any("写手" in a for a in plan.advisories)


def test_builder_no_advisory_when_dep_declared():
    plan, errs = build_run_plan(
        [
            {"id": "r1", "role": "研究员", "task": "调研竞品"},
            {
                "id": "w",
                "role": "写手",
                "task": "基于上游产出撰写报告",
                "depends_on": ["r1"],
            },
        ],
        id_prefix="t",
    )
    assert errs == []
    assert plan.advisories == []


def test_inject_surfaces_dep_advisories():
    session = CoordinationSession(execution_id="e", total_workers=2)
    session.dep_advisories = [
        "「写手」的任务提及上游产出，但 depends_on 为空（run_id=t_w）。"
    ]
    text = format_coordination_events(session, [_wc("w1")])
    assert "疑似缺依赖" in text
    assert "depends_on 为空" in text


async def test_await_injection_surfaces_and_clears_advisory_once():
    """缺依赖提示搭车首次团队事件注入呈现，随后消费清空（不新增独立唤醒事件）。"""
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-adv", total_workers=2)
    session.dep_advisories = [
        "「写手」的任务提及上游产出，但 depends_on 为空（run_id=t_w）。"
    ]
    set_active_coordination(session)
    session.post(_wc("w1"))
    session.post(_all_done(completed=1, total=2))
    try:
        msgs, _ = await _inject("e-adv", timeout=1.0)
        assert "疑似缺依赖" in (msgs[0].content or "")
        # 消费一次即清空——不搭上后续每一批事件；也从未 post 独立事件（不新增唤醒）。
        assert session.dep_advisories == []
    finally:
        clear_active_coordination()


# --- 两池预算：纯遥测（批次 4 不再 HOLD）------------------------------------


async def test_progress_pool_available_consumes_on_stash():
    """进度池尚有额度：例行成功完成不唤醒，但消耗一次进度池（不动决策池）。"""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-prog",
        total_workers=4,
        progress_budget_remaining=2,
        decision_budget_remaining=3,
    )
    set_active_coordination(session)
    session.post(_wc("w2"))
    try:
        with pytest.raises(TimeoutError):
            await _inject("e-prog", timeout=0.35)
    finally:
        clear_active_coordination()
    assert session.progress_budget_remaining == 1  # 进度池消耗 1
    assert session.decision_budget_remaining == 3  # 决策池不受影响


async def test_necessary_decision_wakes_when_progress_pool_at_floor():
    """进度池已到 floor：必要决策仍立即唤醒并记决策池遥测。"""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-nec",
        total_workers=4,
        progress_budget_remaining=0,
        decision_budget_remaining=2,
    )
    set_active_coordination(session)
    session.post(_esc("w4"))
    try:
        msgs, elapsed = await _inject("e-nec", timeout=1.0)
    finally:
        clear_active_coordination()
    assert "escalation" in (msgs[0].content or "")
    assert elapsed < 0.5
    assert session.decision_budget_remaining == 1
    assert session.progress_budget_remaining == 0


async def test_progress_pool_at_floor_still_does_not_wake_on_success():
    """进度池 floor：例行成功仍不唤醒（失败才叫醒）。"""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-floor",
        total_workers=6,
        progress_budget_remaining=0,
        decision_budget_remaining=3,
    )
    set_active_coordination(session)
    session.post(_wc("w2"))
    try:
        with pytest.raises(TimeoutError):
            await _inject("e-floor", timeout=0.35)
    finally:
        clear_active_coordination()
    assert session.progress_budget_remaining == 0
    assert session.decision_budget_remaining == 3


async def test_failed_worker_wakes_when_progress_pool_at_floor():
    """进度池 floor：失败仍立即唤醒并记决策池遥测。"""
    clear_active_coordination()
    session = CoordinationSession(
        execution_id="e-floor-fail",
        total_workers=6,
        progress_budget_remaining=0,
        decision_budget_remaining=3,
    )
    set_active_coordination(session)
    session.post(_wc("w2", status="failed"))
    try:
        msgs, elapsed = await _inject("e-floor-fail", timeout=1.0)
    finally:
        clear_active_coordination()
    content = msgs[0].content or ""
    assert content.count("- worker_completed（") == 1
    assert elapsed < 0.5
    assert session.progress_budget_remaining == 0
    assert session.decision_budget_remaining == 2
