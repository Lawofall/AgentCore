"""回归：协调生命周期与聊天回合解耦（追加委派不被 teardown 掐断）。

覆盖三项已定案决策：
1. 回合 teardown 保留仍活跃的协调 session（release_turn_coordination）
2. 后台 drive 自建独立 LLM client，回合 llm.close() 不掐断 worker 流
3. 启动回显按「新增 N / 图共 M / 其中 K 已完成」计数，并 seed completed 集
"""

from __future__ import annotations

import asyncio

import pytest

from agentcore.llm.factory import spawn_independent_llm
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMChunk
from agentcore.llm.provider.router import ProviderRouter
from agentcore.runtime.coordination.host import try_start_coordination
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    active_coordination,
    clear_active_coordination,
    finish_detached_coordination,
    release_turn_coordination,
    set_active_coordination,
)
from agentcore.runtime.runs import build_run_plan
from agentcore.runtime.runs.types import RunPhase, RunState
from tests.delegate.conftest import Provider, ctx, tool


@pytest.fixture(autouse=True)
def _clean_coordination():
    clear_active_coordination()
    yield
    clear_active_coordination()


class ClosableProvider:
    """Scripted stream that fails after ``close()`` — models turn-level teardown."""

    def __init__(self, contents: list[str], *, delay_s: float = 0.0) -> None:
        self._contents = contents
        self._delay_s = delay_s
        self.calls = 0
        self.closed = False
        self.clone_count = 0

    def clone(self) -> ClosableProvider:
        self.clone_count += 1
        return ClosableProvider(list(self._contents), delay_s=self._delay_s)

    async def close(self) -> None:
        self.closed = True

    async def stream(self, request):
        if self.closed:
            raise RuntimeError("ReadError: turn llm already closed")
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        text = self._contents[self.calls] if self.calls < len(self._contents) else "done"
        self.calls += 1
        yield LLMChunk(delta_content=text)


def test_spawn_independent_llm_clones_router_and_openai():
    leaf = OpenAICompatibleProvider(
        name="platform",
        api_key="k",
        base_url="https://example.test/v1",
        extra_headers={"X-Test": "1"},
    )
    router = ProviderRouter(default=leaf, providers={})
    cloned, owns = spawn_independent_llm(router)
    assert owns is True
    assert cloned is not router
    assert cloned._default is not leaf
    assert cloned._default._api_key == "k"
    assert cloned._default._extra_headers == {"X-Test": "1"}


def test_spawn_independent_llm_fake_without_clone_borrows():
    fake = Provider(["x"])
    got, owns = spawn_independent_llm(fake)
    assert got is fake
    assert owns is False


def test_is_llm_client_closed_error_detects_httpx_wording():
    from agentcore.core.errors import LLMClientClosedError, is_llm_client_closed_error

    assert is_llm_client_closed_error(
        RuntimeError("Cannot send a request, as the client has been closed.")
    )
    assert is_llm_client_closed_error(LLMClientClosedError())
    assert not is_llm_client_closed_error(RuntimeError("other boom"))
    assert not is_llm_client_closed_error(ValueError("client has been closed"))


@pytest.mark.asyncio
async def test_release_turn_keeps_session_drive_finally_clears():
    """决策1：teardown 后宿主协调 session 存活；drive 结束后 detached finally 收口。"""

    async def _slow_drive():
        await asyncio.sleep(0.15)

    session = CoordinationSession(execution_id="e-detach", total_workers=2)
    session.drive_task = asyncio.create_task(_slow_drive())
    set_active_coordination(session)

    release_turn_coordination("e-detach")
    assert active_coordination("e-detach") is session
    assert session.turn_attached is False

    await asyncio.wait_for(session.drive_task, timeout=5)
    finish_detached_coordination(session)
    assert active_coordination("e-detach") is None
    assert session.active is False


@pytest.mark.asyncio
async def test_release_turn_clears_idle_session():
    session = CoordinationSession(execution_id="e-idle", total_workers=2, active=False)
    set_active_coordination(session)
    release_turn_coordination("e-idle")
    assert active_coordination("e-idle") is None


@pytest.mark.asyncio
async def test_background_drive_survives_turn_llm_close():
    """决策2：回合级 client close 后，后台 worker 流仍用独立 clone 跑完。"""
    turn_llm = ClosableProvider(["AOUT", "BOUT"], delay_s=0.05)
    t = tool(turn_llm)
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
    assert session.drive_task is not None

    # Simulate chat-turn teardown: detach coordination + close turn LLM.
    # Yield so ``_background_drive`` can spawn its independent clone first.
    await asyncio.sleep(0)
    release_turn_coordination("e")
    await turn_llm.close()
    assert turn_llm.closed is True

    await asyncio.wait_for(session.drive_task, timeout=10)
    assert turn_llm.clone_count >= 1
    assert len(session.completed_run_ids) == 2
    # Detached drive finally should have unregistered.
    assert active_coordination("e") is None


@pytest.mark.asyncio
async def test_coordinate_cancelled_posts_terminal_to_wake_host(monkeypatch):
    """进程级 cancel 时 drive 投递 DRIVE_CANCELLED，宿主 wait 不永挂。"""
    import importlib

    from agentcore.runtime.coordination.host import _background_drive
    from agentcore.runtime.coordination.session import CoordinationEventKind
    from agentcore.runtime.runs import build_run_plan

    # Package exports shadow submodule name ``drive`` — load via importlib.
    drive_mod = importlib.import_module("agentcore.runtime.delegate.drive")

    plan, errors = build_run_plan(
        [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}]
    )
    assert not errors
    session = CoordinationSession(execution_id="e-cancel", total_workers=2)
    set_active_coordination(session)
    t = tool(Provider(["x"]))

    async def _hang(*_a, **_k):
        await asyncio.sleep(3600)

    # _background_drive imports drive_coordinated late — patch the module attr.
    monkeypatch.setattr(drive_mod, "drive_coordinated", _hang)

    task = asyncio.create_task(
        _background_drive(
            t,
            plan,
            execution_id="e-cancel",
            seed_completed=None,
            seed_notes=None,
            complexity_hint="standard",
            call_idx=0,
                        session=session,
            coordination="wall",
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = session.drain_nowait()
    terminals = [e for e in events if e.kind is CoordinationEventKind.DRIVE_CANCELLED]
    assert terminals, f"expected DRIVE_CANCELLED wake, got {[e.kind for e in events]}"
    assert terminals[0].payload.get("reason") == "cancelled_without_rpc"
    assert "进程关闭或回合中断" not in (terminals[0].payload.get("error") or "")
    assert not any(e.kind is CoordinationEventKind.ALL_COMPLETED for e in events)


@pytest.mark.asyncio
async def test_soft_stop_cancel_skips_all_completed_wake(monkeypatch):
    """ask_user soft-stop：cancel 不投 ALL_COMPLETED，挂起快照无全员完成假象。"""
    import importlib

    from agentcore.runtime.coordination.host import _background_drive
    from agentcore.runtime.coordination.session import CoordinationEventKind
    from agentcore.runtime.runs import build_run_plan

    drive_mod = importlib.import_module("agentcore.runtime.delegate.drive")

    plan, errors = build_run_plan(
        [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}]
    )
    assert not errors
    session = CoordinationSession(execution_id="e-soft", total_workers=2)
    session.soft_stop = True
    session.completed_run_ids.add("w1")
    set_active_coordination(session)
    t = tool(Provider(["x"]))

    async def _hang(*_a, **_k):
        await asyncio.sleep(3600)

    monkeypatch.setattr(drive_mod, "drive_coordinated", _hang)

    task = asyncio.create_task(
        _background_drive(
            t,
            plan,
            execution_id="e-soft",
            seed_completed=None,
            seed_notes=None,
            complexity_hint="standard",
            call_idx=0,
                        session=session,
            coordination="wall",
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    snap = session.snapshot()
    pending_kinds = [e.get("kind") for e in snap.pending_events]
    assert CoordinationEventKind.ALL_COMPLETED.value not in pending_kinds
    assert CoordinationEventKind.DRIVE_CANCELLED.value not in pending_kinds
    assert len(snap.completed_run_ids) == 1
    assert snap.total_workers == 2


@pytest.mark.asyncio
async def test_background_drive_exception_posts_drive_cancelled(monkeypatch):
    """后台调度炸了投递 DRIVE_CANCELLED，禁止假 ALL_COMPLETED。"""
    import importlib

    from agentcore.runtime.coordination.host import _background_drive
    from agentcore.runtime.coordination.session import CoordinationEventKind
    from agentcore.runtime.runs import build_run_plan

    drive_mod = importlib.import_module("agentcore.runtime.delegate.drive")

    plan, errors = build_run_plan(
        [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}]
    )
    assert not errors
    session = CoordinationSession(execution_id="e-boom", total_workers=2)
    session.completed_run_ids.add("w1")
    set_active_coordination(session)
    t = tool(Provider(["x"]))

    async def _boom(*_a, **_k):
        raise RuntimeError("simulated drive scheduling crash")

    monkeypatch.setattr(drive_mod, "drive_coordinated", _boom)

    await _background_drive(
        t,
        plan,
        execution_id="e-boom",
        seed_completed=None,
        seed_notes=None,
        complexity_hint="standard",
        call_idx=0,
                session=session,
        coordination="wall",
    )

    events = session.drain_nowait()
    cancelled = [e for e in events if e.kind is CoordinationEventKind.DRIVE_CANCELLED]
    assert cancelled, f"expected DRIVE_CANCELLED, got {[e.kind for e in events]}"
    assert cancelled[0].payload.get("completed") == 1
    assert cancelled[0].payload.get("total") == 2
    assert "调度异常" in (cancelled[0].payload.get("error") or "")
    assert not any(e.kind is CoordinationEventKind.ALL_COMPLETED for e in events)


@pytest.mark.asyncio
async def test_background_drive_logs_contract_failure(monkeypatch):
    """后台 drive success=False 不得静默丢弃，须带 error 落日志（不推 CEO）。"""
    import importlib

    from structlog.testing import capture_logs

    from agentcore.core.types import ToolEffect
    from agentcore.runtime.coordination.host import _background_drive
    from agentcore.runtime.coordination.session import CoordinationEventKind
    from agentcore.runtime.runs import build_run_plan
    from agentcore.tools.protocol import ToolResult

    drive_mod = importlib.import_module("agentcore.runtime.delegate.drive")

    plan, errors = build_run_plan(
        [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}]
    )
    assert not errors
    session = CoordinationSession(execution_id="e-contract", total_workers=2)
    set_active_coordination(session)
    t = tool(Provider(["x"]))

    async def _reject(*_a, **_k):
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error="补跑拒绝：当前无失败/跳过缺口，禁止无缺口整团重开",
            effect=ToolEffect.CONTINUE,
            contract_failure=True,
        )

    monkeypatch.setattr(drive_mod, "drive_coordinated", _reject)

    with capture_logs() as logs:
        await _background_drive(
            t,
            plan,
            execution_id="e-contract",
            seed_completed=None,
            seed_notes=None,
            complexity_hint="standard",
            call_idx=0,
            session=session,
            coordination="wall",
        )

    events_logged = [e.get("event") for e in logs]
    hit = next((e for e in logs if e.get("event") == "delegate.coordinate_failed"), None)
    assert hit is not None, f"expected coordinate_failed log, got {events_logged}"
    assert "无缺口" in (hit.get("error") or "")
    assert hit.get("contract_failure") is True
    events = session.drain_nowait()
    assert not any(e.kind is CoordinationEventKind.ALL_COMPLETED for e in events)
    assert not any(e.kind is CoordinationEventKind.DRIVE_CANCELLED for e in events)


@pytest.mark.asyncio
async def test_coordination_start_echo_counts_and_seeds_completed():
    """决策3：回显新增/总数/已完成；seed 预填 completed_run_ids。"""
    provider = Provider(["NEWOUT"])
    t = tool(provider)
    plan, errors = build_run_plan(
        [
            {"id": "old", "role": "研究员", "task": "已完成调研"},
            {"id": "new", "role": "写手", "task": "撰写"},
        ],
        id_prefix="del_",
        parent_run_id="CEO",
        depth=1,
    )
    assert not errors
    assert len(plan.nodes) == 2
    done_id = plan.nodes[0].run_id
    seed = {
        done_id: RunState(phase=RunPhase.COMPLETED, content="旧产出"),
    }
    started = try_start_coordination(
        t,
        plan,
        execution_id="e",
        seed_completed=seed,
        seed_notes=None,
        complexity_hint="standard",
        call_idx=1,
                coordinate=True,
    )
    assert started is not None
    out = started.output or ""
    assert started.audience == "ceo"
    assert "队员已追加" in out
    assert "已追加 1 名" in out
    assert "图共 2 名" in out
    assert "其中 1 名已完成" in out
    assert "wait" not in out
    assert "update_synthesis" not in out
    assert "coordinate=false" not in out
    assert "人已派出" not in out
    assert "可见正文" not in out
    assert "谁在后台、完成后会再汇报" not in out

    session = active_coordination("e")
    assert session is not None
    assert done_id in session.completed_run_ids
    assert session.total_workers == 2
    assert len(session.completed_run_ids) == 1

    await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination("e")


@pytest.mark.asyncio
async def test_fresh_coordination_echo_includes_total_and_zero_completed():
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
    assert "团队已启动" in result.output
    assert result.audience == "ceo"
    assert "图共 2 名" in result.output
    assert "其中 0 名已完成" in result.output
    out = result.output or ""
    assert "wait" not in out
    assert "update_synthesis" not in out
    assert "coordinate=false" not in out
    assert "人已派出" not in out
    assert "可见正文" not in out
    assert "谁在后台、完成后会再汇报" not in out
    session = active_coordination("e")
    assert session is not None
    await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination("e")
