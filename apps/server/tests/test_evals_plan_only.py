"""plan-only 评测模式：零 LLM 单测（假 provider / 合成事件）。"""

from __future__ import annotations

import asyncio

from agentcore.core.types import ToolEffect
from agentcore.evals.harness import _clamp_ceo_rounds
from agentcore.evals.recording_sink import RecordingSink
from agentcore.evals.report import format_report, report_to_dict, shape_means_by_case
from agentcore.evals.runner import apply_checks, run_suite
from agentcore.evals.types import CaseReport, EvalCase, EvalReport, TurnOutcome
from agentcore.llm.profiles import TurnProfiles
from agentcore.runtime.plan_only import (
    PLAN_ONLY_CEO_MAX_ROUNDS,
    PlanOnlyAbortError,
    is_plan_only,
    use_plan_only,
)
from tests.delegate.conftest import Provider, ctx, tool


def test_plan_only_contextvar_defaults_off_and_resets():
    assert is_plan_only() is False
    with use_plan_only(True):
        assert is_plan_only() is True
    assert is_plan_only() is False
    with use_plan_only(False):
        assert is_plan_only() is False


def test_clamp_ceo_rounds_only_affects_chat():
    profiles = TurnProfiles(model="deepseek-v4-flash")
    clamped = _clamp_ceo_rounds(profiles, PLAN_ONLY_CEO_MAX_ROUNDS)
    assert clamped.get("chat").max_rounds == PLAN_ONLY_CEO_MAX_ROUNDS
    assert clamped.get("agent").max_rounds == profiles.get("agent").max_rounds
    assert clamped.model == profiles.model


def test_apply_checks_plan_only_marks_content_na():
    case = EvalCase(
        id="t",
        category="team",
        user_message="q",
        expected_shape={"min_workers": 2},
        checks=[
            {"name": "FinishReason"},
            {"name": "NonEmpty", "args": {"min_len": 80}},
            {"name": "Delegated"},
            {"name": "ShapeMatches", "args": {"threshold": 0.6}},
        ],
    )
    outcome = TurnOutcome(
        content="",
        finish_reason="error",
        rounds=1,
        delegated=True,
        roster=["调研"],
        plan_runs=[
            {"id": "a", "role": "A", "task": "", "depends_on": [], "parent_run_id": None},
            {"id": "b", "role": "B", "task": "", "depends_on": [], "parent_run_id": None},
        ],
    )
    checks = apply_checks(case, outcome, plan_only=True)
    by_name = {c.name: c for c in checks}
    assert by_name["FinishReason"].detail == "n/a (plan-only)"
    assert by_name["FinishReason"].gating is False
    assert by_name["NonEmpty"].detail == "n/a (plan-only)"
    assert by_name["NonEmpty"].gating is False
    assert by_name["Delegated"].passed is True
    assert by_name["Delegated"].gating is False
    assert by_name["ShapeMatches"].passed is True
    assert "score=" in by_name["ShapeMatches"].detail


def test_shape_means_by_case_and_report():
    def _rep(case_id: str, score: float) -> CaseReport:
        return CaseReport(
            case_id=case_id,
            category="team",
            outcome=TurnOutcome(content="", finish_reason="end_turn", rounds=1),
            shape_score=score,
        )

    report = EvalReport(
        cases=[
            _rep("a", 0.5),
            _rep("a", 1.0),
            _rep("b", 0.8),
        ]
    )
    means = shape_means_by_case(report)
    assert means == {"a": 0.75, "b": 0.8}
    d = report_to_dict(report)
    assert d["summary"]["shape_score_by_case"] == means
    assert d["summary"]["shape_score_mean"] == 0.7667
    text = format_report(report)
    assert "shape@a: 0.75" in text
    assert "shape@b: 0.80" in text


def test_run_suite_plan_only_skips_judge_and_averages_samples():
    class _FakeHarness:
        def __init__(self) -> None:
            self.calls = 0

        async def run_case(self, case: EvalCase) -> TurnOutcome:
            self.calls += 1
            # Alternate scores via plan_runs length so shape_score varies by sample.
            n = 2 if self.calls % 2 else 1
            return TurnOutcome(
                content="",
                finish_reason="end_turn",
                rounds=1,
                delegated=n >= 2,
                roster=["A"] * n,
                plan_runs=[
                    {
                        "id": f"r{i}",
                        "role": "A",
                        "task": "",
                        "depends_on": [],
                        "parent_run_id": None,
                    }
                    for i in range(n)
                ],
                plan_type="multi_agent",
            )

    case = EvalCase(
        id="c1",
        category="team",
        user_message="q",
        samples=2,
        expected_shape={"min_workers": 2},
        checks=[
            {"name": "FinishReason"},
            {"name": "ShapeMatches", "args": {"threshold": 0.5}},
        ],
        milestones=[{"id": "x", "desc": "should skip", "weight": 1}],
    )

    class _BoomJudge:
        async def score(self, case, outcome):  # noqa: ANN001
            raise AssertionError("judge must not run in plan-only")

        async def score_milestones(self, case, outcome):  # noqa: ANN001
            raise AssertionError("milestone must not run in plan-only")

    harness = _FakeHarness()
    report = asyncio.run(
        run_suite(
            [case],
            harness=harness,
            judge=_BoomJudge(),
            milestone_judge=_BoomJudge(),
            layer=2,
            plan_only=True,
        )
    )
    assert harness.calls == 2
    assert len(report.cases) == 2
    assert all(c.judge is None and c.milestone is None for c in report.cases)
    assert all(
        any(ck.name == "FinishReason" and ck.detail == "n/a (plan-only)" for ck in c.checks)
        for c in report.cases
    )
    means = shape_means_by_case(report)
    assert "c1" in means


def test_plan_only_abort_propagates_as_named_exception():
    with use_plan_only(True):
        try:
            raise PlanOnlyAbortError()
        except PlanOnlyAbortError:
            pass
        else:
            raise AssertionError("PlanOnlyAbortError not raised")


def test_cli_plan_only_lint_only_collab_shapes():
    from agentcore.evals.cli import main

    code = main(["lint", "--suite", "collab_shapes"])
    assert code == 0


async def test_debate_plan_only_emits_debater_plan_skips_speakers():
    """真实 build_run_plan + debater run_plan；plan-only 下不跑辩手 stream。"""
    from tests.test_debate_tool import _DebateLLM, _sides, _tool

    sink = RecordingSink()
    llm = _DebateLLM(converge_at=1)
    debate = _tool(llm, sink=sink)
    with use_plan_only(True):
        result = await debate.execute(
            {
                "motion": "该不该做 X",
                "form": "debate",
                "sides": _sides(),
            },
            debate._base_tool_context,
        )
    assert result.success is True
    assert result.effect is ToolEffect.HANDOFF
    assert "plan-only" in (result.final_text or "")
    assert llm.stream_calls == 0  # 辩手未执行
    assert sink.plan_type == "debate"
    # 主持人 + 两辩手（RecordingSink 合并多次 run_plan）
    roles = {r["role"] for r in sink.plan_runs}
    assert "主持人" in roles
    assert len(sink.plan_runs) >= 3
