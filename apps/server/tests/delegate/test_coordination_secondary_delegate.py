"""Secondary delegate during coordination: merge into same session (not overwrite)."""

from __future__ import annotations

import asyncio

from agentcore.llm.provider.protocol import LLMChunk
from agentcore.runtime.coordination.session import (
    active_coordination,
    clear_active_coordination,
)
from tests.delegate.conftest import Provider, ctx, tool


class _SlowWorkers:
    """All workers sleep so the CEO can fire a second delegate mid-flight."""

    def __init__(self, texts: list[str], delay: float = 0.35) -> None:
        self._texts = texts
        self.calls = 0
        self.delay = delay

    async def stream(self, request):  # noqa: ANN001
        idx = self.calls
        self.calls += 1
        await asyncio.sleep(self.delay)
        text = self._texts[idx] if idx < len(self._texts) else "done"
        yield LLMChunk(delta_content=text)


async def test_secondary_delegate_merges_into_same_coordination_session():
    """契约：协调中二次 delegate → 同一 CoordinationSession，worker 追加，不串台。

    根因（修复前）：``set_active_coordination`` 按 execution_id 覆盖旧 session，
    旧 drive_task 仍跑但事件进被丢弃的队列；cancel / 仲裁态丢失。
    """
    clear_active_coordination()
    t = tool(_SlowWorkers(["A", "B", "C", "D"], delay=0.4))

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
    assert "团队已启动" in first.output
    session = active_coordination("e")
    assert session is not None
    session_id = id(session)
    first_drive = session.drive_task
    assert first_drive is not None and not first_drive.done()
    session.request_cancel("sentinel-keep")
    session.update_draft("保留草稿")
    budget_before = session.budget_remaining
    assert session.total_workers == 2

    second = await t.execute(
        {
            "tasks": [
                {"role": "审查", "task": "做C"},
                {"role": "校对", "task": "做D"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert second.success is True
    assert "队员已追加" in second.output
    assert "wait" not in (second.output or "")
    assert "update_synthesis" not in (second.output or "")
    assert "coordinate=false" not in (second.output or "")
    assert "人已派出" not in (second.output or "")

    after = active_coordination("e")
    assert after is not None
    assert id(after) == session_id, "must keep the same CoordinationSession object"
    assert after.total_workers == 4
    assert after.draft == "保留草稿"
    assert "sentinel-keep" in after.cancel_ids
    assert after.budget_remaining >= budget_before  # topped up, not reset/lost
    assert after.live_plan is not None
    assert len(after.live_plan.nodes) == 4
    # Live merge: original drive still owns the wave (not replaced by a second drive).
    assert after.drive_task is first_drive
    assert not first_drive.done()

    await asyncio.wait_for(first_drive, timeout=15)
    # All four workers should complete into the same session.
    assert len(after.completed_run_ids) == 4
    events = after.drain_nowait()
    from agentcore.runtime.coordination.session import CoordinationEventKind

    kinds = [e.kind for e in events]
    assert CoordinationEventKind.ALL_COMPLETED in kinds
    all_done = next(e for e in events if e.kind is CoordinationEventKind.ALL_COMPLETED)
    assert all_done.payload.get("total") == 4
    clear_active_coordination("e")


async def test_secondary_delegate_preserves_arbitration_state():
    clear_active_coordination()
    t = tool(_SlowWorkers(["A", "B", "C"], delay=0.35))
    await t.execute(
        {
            "tasks": [
                {"role": "研究员", "task": "做A"},
                {"role": "写手", "task": "做B"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    session = active_coordination("e")
    assert session is not None
    session.register_arbitration(
        "w1",
        escalation_id="esc-1",
        conversation_id="c",
        question="要不要加第三个人？",
    )
    await t.execute(
        {"tasks": [{"role": "补充", "task": "做C"}], "coordinate": True},
        ctx(),
    )
    after = active_coordination("e")
    assert after is session
    assert after.get_arbitration("w1") is not None
    assert after.total_workers == 3
    await asyncio.wait_for(session.drive_task, timeout=15)
    clear_active_coordination("e")


async def test_secondary_delegate_replaces_rewrites_downstream_depends_on():
    """Bug B: 协调补派带 replaces_run_id → 下游 depends_on 改写为新 run。"""
    clear_active_coordination()
    t = tool(_SlowWorkers(["R", "W", "R2"], delay=0.5))
    first = await t.execute(
        {
            "tasks": [
                {"id": "r1", "role": "调研", "task": "做R", "depends_on": []},
                {"id": "w", "role": "写手", "task": "做W", "depends_on": ["r1"]},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert first.success is True
    session = active_coordination("e")
    assert session is not None and session.live_plan is not None
    r1 = next(n for n in session.live_plan.nodes if n.run_id.endswith("_r1"))
    writer = next(n for n in session.live_plan.nodes if n.run_id.endswith("_w"))
    assert r1.run_id in writer.depends_on

    second = await t.execute(
        {
            "tasks": [
                {
                    "id": "r1b",
                    "role": "调研",
                    "task": "补跑R",
                    "depends_on": [],
                    "replaces_run_id": r1.run_id,
                }
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert second.success is True
    assert "队员已追加" in second.output
    after = active_coordination("e")
    assert after is session and after.live_plan is not None
    replacement = next(
        n for n in after.live_plan.nodes if n.replaces_run_id == r1.run_id
    )
    writer_after = after.live_plan.by_id(writer.run_id)
    assert writer_after is not None
    assert replacement.run_id in writer_after.depends_on
    assert r1.run_id not in writer_after.depends_on

    await asyncio.wait_for(session.drive_task, timeout=15)
    clear_active_coordination("e")


async def test_secondary_delegate_depends_on_host_role_via_live_plan():
    """同回合二次 + depends_on 角色名：无显式 append，经 live_plan 解析宿主节点。"""
    clear_active_coordination()
    t = tool(_SlowWorkers(["A", "B", "C"], delay=0.4))

    first = await t.execute(
        {
            "tasks": [
                {"id": "recon", "role": "调研员", "task": "做A"},
                {"role": "写手", "task": "做B"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert first.success is True
    session = active_coordination("e")
    assert session is not None and session.live_plan is not None
    recon = next(n for n in session.live_plan.nodes if n.role == "调研员")
    assert recon.run_id.endswith("_recon")

    second = await t.execute(
        {
            "tasks": [
                {
                    "id": "synth",
                    "role": "汇总",
                    "task": "基于调研汇总",
                    "depends_on": ["调研员"],
                }
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert second.success is True, second.error
    assert "队员已追加" in second.output
    after = active_coordination("e")
    assert after is session and after.live_plan is not None
    synth = next(n for n in after.live_plan.nodes if n.role == "汇总")
    assert recon.run_id in synth.depends_on

    await asyncio.wait_for(session.drive_task, timeout=15)
    clear_active_coordination("e")


async def test_secondary_delegate_depends_on_previous_batch_id_via_live_plan(
    monkeypatch,
):
    """同回合二次 + depends_on 上一批声明 id：mock live_plan → 解析成功。"""
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        set_active_coordination,
    )
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec
    from agentcore.tools.protocol import ToolResult

    clear_active_coordination()
    host_id = "del_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_recon"
    live = RunPlan(
        nodes=[
            RunSpec(run_id=host_id, agent_id=host_id, role="调研员", task="做A"),
        ]
    )
    session = CoordinationSession(
        execution_id="e",
        total_workers=1,
        conversation_id="c",
    )
    session.live_plan = live
    session.host_turn_id = "m"
    set_active_coordination(session)

    captured: dict = {}

    async def fake_drive(tool, plan, **kwargs):  # noqa: ANN001
        captured["deps"] = {n.run_id: list(n.depends_on) for n in plan.nodes}
        captured["node_ids"] = [n.run_id for n in plan.nodes]
        # Merge new nodes into live_plan like host would.
        for n in plan.nodes:
            if session.live_plan.by_id(n.run_id) is None:
                session.live_plan.add(n)
        return ToolResult(tool_call_id="", success=True, output="队员已追加")

    monkeypatch.setattr("agentcore.tools.builtin.delegate.tool.drive", fake_drive)

    t = tool(_SlowWorkers(["X"], delay=0.01))
    t._message_id = "m"
    t._calls = 1  # 已有本回合上一批

    second = await t.execute(
        {
            "tasks": [
                {
                    "id": "write",
                    "role": "写手",
                    "task": "基于调研写",
                    "depends_on": ["recon"],
                }
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert second.success is True, second.error
    assert any(host_id in deps for deps in captured["deps"].values())
    clear_active_coordination("e")


async def test_same_turn_blocking_last_graph_depends_on_declared_id(monkeypatch):
    """dogfood 形：同回合单 worker 阻塞跑完 → 二次无 append，靠 _last_graph 合入。

    第一批 flat+id=recon 阻塞完成；第二批 depends_on:["recon"] 无显式 append，
    经 _last_graph_execution_id + 内存 plan 解析成功并合入同一 execution_id。
    """
    from agentcore.tools.protocol import ToolResult

    clear_active_coordination()
    t = tool(Provider(["调研完成"]))

    first = await t.execute(
        {
            "tasks": [{"id": "recon", "role": "调研员", "task": "做调研"}],
            "coordinate": False,
        },
        ctx(),
    )
    assert first.success is True, first.error
    assert t._calls == 1
    host_eid = t._last_graph_execution_id
    assert host_eid
    assert t._last_graph_plan is not None
    recon = next(n for n in t._last_graph_plan.nodes if n.role == "调研员")
    assert recon.run_id.endswith("_recon")
    # 阻塞单人：不应残留活跃协调（合入走 last_graph，非 live_plan）。
    active = active_coordination("e")
    assert active is None or not active.active

    captured: dict = {}

    async def fake_drive(tool, plan, **kwargs):  # noqa: ANN001
        captured["execution_id"] = kwargs.get("execution_id")
        captured["seed"] = kwargs.get("seed_completed")
        captured["deps"] = {n.run_id: list(n.depends_on) for n in plan.nodes}
        return ToolResult(tool_call_id="", success=True, output="ok")

    monkeypatch.setattr("agentcore.tools.builtin.delegate.tool.drive", fake_drive)

    second = await t.execute(
        {
            "tasks": [
                {
                    "id": "write",
                    "role": "写手",
                    "task": "基于调研写",
                    "depends_on": ["recon"],
                }
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert second.success is True, second.error
    assert captured["execution_id"] == host_eid
    assert any(recon.run_id in deps for deps in captured["deps"].values())
    # 内存宿主 seed：上一批完成态须带入，供依赖调度。
    assert captured["seed"] is not None
    assert recon.run_id in captured["seed"]
    clear_active_coordination("e")


async def test_same_turn_blocking_last_graph_depends_on_role_name(monkeypatch):
    """同上 dogfood 形：depends_on 填无歧义角色名亦可解析。"""
    from agentcore.tools.protocol import ToolResult

    clear_active_coordination()
    t = tool(Provider(["调研完成"]))

    first = await t.execute(
        {
            "tasks": [{"id": "recon", "role": "调研员", "task": "做调研"}],
            "coordinate": False,
        },
        ctx(),
    )
    assert first.success is True, first.error
    host_eid = t._last_graph_execution_id
    recon = next(n for n in t._last_graph_plan.nodes if n.role == "调研员")

    captured: dict = {}

    async def fake_drive(tool, plan, **kwargs):  # noqa: ANN001
        captured["deps"] = {n.run_id: list(n.depends_on) for n in plan.nodes}
        captured["execution_id"] = kwargs.get("execution_id")
        return ToolResult(tool_call_id="", success=True, output="ok")

    monkeypatch.setattr("agentcore.tools.builtin.delegate.tool.drive", fake_drive)

    second = await t.execute(
        {
            "tasks": [
                {
                    "role": "写手",
                    "task": "基于调研写",
                    "depends_on": ["调研员"],
                }
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert second.success is True, second.error
    assert captured["execution_id"] == host_eid
    assert any(recon.run_id in deps for deps in captured["deps"].values())
    clear_active_coordination("e")
