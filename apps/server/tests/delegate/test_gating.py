"""Local-mode worker gating tests."""

import asyncio

from agentcore.runtime.coordination.session import (
    active_coordination,
    clear_active_coordination,
)
from agentcore.runtime.events import EventSink, EventType
from tests.delegate.conftest import (
    Provider,
    capture_gate,
    ctx,
    gate,
    local_ctx,
    tool,
    tool_with_gate,
)


async def _await_solo_drive() -> None:
    """Solo 默认进协调：须等后台 drive 跑完，gate / lifecycle 才落定。"""
    session = active_coordination("e")
    if session is not None and session.drive_task is not None:
        await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination("e")


async def test_workers_gated_in_local_mode(monkeypatch):
    clear_active_coordination()
    captured = capture_gate(monkeypatch)
    g = gate()
    t = tool_with_gate(local_ctx(), g)
    await t.execute({"tasks": [{"role": "A", "task": "a"}]}, local_ctx())
    await _await_solo_drive()
    assert captured["gate"] is g


async def test_workers_keep_gate_in_cloud_mode(monkeypatch):
    """云端 worker 也拿到 gate 对象——「弹不弹卡」归收口点，不归上游预判。

    这里曾断言 ``captured["gate"] is None``：上游按 ``location`` 预判「云端用不上逐次
    卡」，把 gate 直接吞掉。那份预判等于在 ``sandbox_approval`` 之外另抄一张表，漏过两
    次（恒确认曾因此失效；``file_write=ask`` 的云端实现根本执行不到）。现在一律往下
    传，云端该免的卡仍由 ``tool_exec_gates`` 查同一张表免掉。
    """
    clear_active_coordination()
    captured = capture_gate(monkeypatch)
    g = gate()
    t = tool_with_gate(ctx(), g)
    await t.execute({"tasks": [{"role": "A", "task": "a"}]}, ctx())
    await _await_solo_drive()
    assert captured["gate"] is g


async def test_second_call_namespaces_run_ids():
    # 阻塞臂：同回合二次合入仍为新节点铸独立 run_id（默认协调臂会提前返回，事件未齐）。
    sink = EventSink()
    t = tool(Provider(["X", "Y"]), sink=sink)
    await t.execute({"tasks": [{"role": "A", "task": "a"}], "coordinate": False}, ctx())
    await t.execute({"tasks": [{"role": "B", "task": "b"}], "coordinate": False}, ctx())
    sink.close()
    starts = [e async for e in sink if e.type == EventType.RUN_STARTED]
    run_ids = [e.payload["run_id"] for e in starts]
    assert len(run_ids) == 2
    assert run_ids[0] != run_ids[1]
