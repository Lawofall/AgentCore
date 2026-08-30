"""plan_review CONTINUE → gate_notes 注入（llm 压缩；deterministic / 旧帧不下发）。"""

from __future__ import annotations

from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.delegate.steer import (
    apply_gate_notes,
    apply_steer,
    compress_ceo_review_for_gate,
)
from agentcore.runtime.runs.executor.context import _build_context_blocks
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState


def _plan() -> RunPlan:
    return RunPlan(
        nodes=[
            RunSpec(run_id="r1", task="调研", role="调研", checkpoint_after=True),
            RunSpec(run_id="r2", task="实现", role="实现", depends_on=["r1"]),
            RunSpec(run_id="r3", task="旁支", role="旁支"),  # 非下游
        ]
    )


def _completed() -> dict[str, RunState]:
    return {"r1": RunState(phase=RunPhase.COMPLETED, content="ok")}


def test_compress_llm_review_template(monkeypatch):
    from agentcore.runtime import context_cap
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(context_cap, "logger", spy)
    body = compress_ceo_review_for_gate(
        {
            "source": "llm",
            "conclusion": "C" * 250,
            "risks": ["r1", "r2", "r3", "r4"],
            "suggestions": ["s1", "s2", "s3"],
        }
    )
    assert body is not None
    assert "用户已放行" in body
    assert "非否决" in body
    assert "结论：" in body
    assert "…" in body  # truncated
    assert body.count("- r") == 3  # Top3 risks
    assert "- s1" in body and "- s2" in body
    assert "- s3" not in body
    sites = {kw["site"]: kw for name, kw in spy.events if name == "delegate.context_capped"}
    assert sites["gate_conclusion"]["original_chars"] == 250
    assert sites["gate_conclusion"]["final_chars"] < 250
    assert sites["gate_risks"]["original_count"] == 4
    assert sites["gate_risks"]["final_count"] == 3
    assert sites["gate_suggestions"]["original_count"] == 3
    assert sites["gate_suggestions"]["final_count"] == 2


def test_compress_deterministic_and_absent_yield_none():
    assert compress_ceo_review_for_gate(None) is None
    assert compress_ceo_review_for_gate({"source": "deterministic", "conclusion": "x"}) is None
    assert compress_ceo_review_for_gate({"conclusion": "旧帧无 source"}) is None


def test_apply_gate_notes_replace_scoped_to_downstream():
    plan = _plan()
    apply_gate_notes(plan, _completed(), {"r1"}, "第一版要点")
    assert plan.by_id("r2").gate_notes == "第一版要点"
    assert plan.by_id("r1").gate_notes == ""
    assert plan.by_id("r3").gate_notes == ""
    # REPLACE（非 append）
    apply_gate_notes(plan, _completed(), {"r1"}, "第二版要点")
    assert plan.by_id("r2").gate_notes == "第二版要点"
    assert "第一版" not in plan.by_id("r2").gate_notes


def test_adjust_note_still_steers_and_outranks_gate_notes_in_blocks():
    plan = _plan()
    apply_gate_notes(plan, _completed(), {"r1"}, "把关压缩文")
    apply_steer(plan, _completed(), {"r1"}, "请更简洁")
    node = plan.by_id("r2")
    assert "把关压缩文" in node.gate_notes
    assert "请更简洁" in node.steer
    blocks = _build_context_blocks(plan, node, {}, "u", None)
    channels = [b.channel for b in blocks]
    assert "gate_notes" in channels
    assert "steer" in channels
    assert channels.index("gate_notes") < channels.index("steer")
    steer_block = next(b for b in blocks if b.channel == "steer")
    assert "优先级最高" in steer_block.heading
    gate_block = next(b for b in blocks if b.channel == "gate_notes")
    assert "非否决" in gate_block.heading
    assert gate_block.body == "把关压缩文"


def test_resume_plan_continue_llm_injects_gate_notes():
    """Unit-level: compress + apply path used by resume_plan CONTINUE."""
    plan = _plan()
    review = {
        "source": "llm",
        "conclusion": "可过",
        "risks": ["缺回滚"],
        "suggestions": ["先灰度"],
    }
    body = compress_ceo_review_for_gate(review)
    assert body is not None
    apply_gate_notes(plan, _completed(), {"r1"}, body)
    assert "可过" in plan.by_id("r2").gate_notes
    assert plan.by_id("r2").steer == ""


def test_resume_plan_continue_deterministic_no_gate_notes():
    plan = _plan()
    body = compress_ceo_review_for_gate(
        {
            "source": "deterministic",
            "conclusion": "回落摘要",
            "risks": ["x"],
            "suggestions": ["y"],
        }
    )
    assert body is None
    # 模拟 resume_plan：无 body → 不调用 apply；下游保持空
    assert plan.by_id("r2").gate_notes == ""


def test_old_frame_missing_ceo_review_safe():
    """旧帧无 ceo_review：compress 与 apply 均安全无炸。"""
    plan = _plan()
    assert compress_ceo_review_for_gate(None) is None
    apply_gate_notes(plan, _completed(), {"r1"}, "")
    assert plan.by_id("r2").gate_notes == ""


def test_plan_review_continue_note_does_not_steer():
    """定案：plan_review CONTINUE+note 仍不 apply_steer（与 kickoff 分叉）。"""
    # 行为钉在 resume_plan 条件：仅 plan_review ADJUST 或 kickoff CONTINUE 才 steer。
    # 此处钉 gate 与 steer 分通道：CONTINUE 路径只写 gate_notes。
    plan = _plan()
    body = compress_ceo_review_for_gate(
        {"source": "llm", "conclusion": "ok", "risks": [], "suggestions": []}
    )
    assert body is not None
    apply_gate_notes(plan, _completed(), {"r1"}, body)
    # 用户 CONTINUE 带的 note 不进 steer（由 resume_plan 守卫；此处断言通道未混用）
    assert plan.by_id("r2").steer == ""
    assert CheckpointDecision.CONTINUE.value == "continue"
