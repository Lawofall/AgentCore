"""Hard-timeout kill is reported as a timeout, not as a re-task.

The kill reuses the cancel channel, and the cancel arg is the ONLY carrier of why
the worker died — it lands verbatim on the wire ``run_cancelled.reason`` (协作图
node label). Hardcoding ``redirect`` there told the user「已改派」for a worker that
nobody re-tasked: it hit the timeout ceiling and was killed.
"""

import asyncio

from agentcore.runtime.coordination.session import CoordinationSession
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.timeout_hard import (
    HardTimeoutPhase,
    arm_hard_timeout,
    disarm_hard_timeout,
    get_hard_timeout,
)
from agentcore.runtime.runs.types import RunPhase
from agentcore.runtime.runs.wave import WaveScheduler
from tests.runs_executor.conftest import _ContentProvider, _executor


async def _walk_to_post_grace(run_id: str):
    guard = arm_hard_timeout(run_id, timeout_s=0.01, warn_ratio=0.0, grace_wall_s=600)
    assert guard is not None
    for _ in range(200):
        if guard.phase is HardTimeoutPhase.TIMED_OUT:
            break
        await asyncio.sleep(0.01)
    assert guard.begin_grace_round() is True
    guard.end_grace_round()  # 宽限一轮已交卷/用尽 → 下一次入口即强杀
    return guard


async def test_hard_timeout_kill_emits_run_cancelled_reason_worker_timeout():
    plan, _ = build_run_plan([{"role": "A", "task": "做A"}], id_prefix="t")
    sink = EventSink()
    provider = _ContentProvider(["AOUT"])
    await _walk_to_post_grace("t_1")
    try:
        res = await WaveScheduler().run(plan, _executor(plan, provider, sink))
    finally:
        disarm_hard_timeout("t_1")

    state = res["t_1"]
    assert state.phase is RunPhase.CANCELLED
    assert state.error == "worker_timeout"
    assert provider.calls == 0  # killed at the round boundary, no new LLM work

    cancelled = [e for e in sink._history if e.type is EventType.RUN_CANCELLED]  # noqa: SLF001
    assert [e.payload.get("reason") for e in cancelled] == ["worker_timeout"]
    assert cancelled[0].payload.get("run_id") == "t_1"


def test_arm_hard_timeout_omitted_or_nonpositive_does_not_arm():
    run_id = "no-default-wall"
    try:
        assert arm_hard_timeout(run_id, timeout_s=None) is None
        assert get_hard_timeout(run_id) is None
        assert arm_hard_timeout(run_id, timeout_s=0) is None
        assert get_hard_timeout(run_id) is None
        assert arm_hard_timeout(run_id, timeout_s=-1) is None
        assert get_hard_timeout(run_id) is None
    finally:
        disarm_hard_timeout(run_id)


def test_arm_worker_timeout_without_threshold_registers_but_does_not_arm():
    """cancel_worker 短名解析仍要登记；缺省不建计时器。"""
    session = CoordinationSession(execution_id="exec-no-to", total_workers=1)
    session.arm_worker_timeout("w1", role="写手", timeout_s=None)
    assert session._running_workers["w1"] == "写手"
    assert get_hard_timeout("w1") is None
    session.arm_worker_timeout("w2", role="前端", timeout_s=0)
    assert session._running_workers["w2"] == "前端"
    assert get_hard_timeout("w2") is None
    session.disarm_worker_timeout("w1")
    session.disarm_worker_timeout("w2")
    assert "w1" not in session._running_workers
    assert "w2" not in session._running_workers
