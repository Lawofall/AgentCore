"""CEO 协调：队员产出一行摘要（注入 / host / drive terminal）。"""

from agentcore.runtime.graph.facing.team_synthesis import worker_output_blurb
from agentcore.runtime.runs.types import RunPhase, RunState


def test_worker_output_blurb_cancelled_is_stopped():
    assert worker_output_blurb(RunState(phase=RunPhase.CANCELLED)) == "已停止"


def test_worker_output_blurb_prefers_debrief():
    state = RunState(
        phase=RunPhase.COMPLETED,
        content="全文很长不应优先",
        debrief={"summary": "一句话结论"},
    )
    assert worker_output_blurb(state) == "一句话结论"


def test_worker_output_blurb_failed_clips_error():
    assert worker_output_blurb(RunState(phase=RunPhase.FAILED, error="timeout")) == (
        "失败：timeout"
    )
