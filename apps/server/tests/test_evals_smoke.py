"""eval harness 冒烟自测（评估体系 §八 零 LLM 自测 / §十二）.

per-PR 硬门禁的关键：用**脚本化假 provider** 零成本验证 harness/runner/report 本身不坏，
真实模型只在 nightly 跑。覆盖：
- single 路径：真 ``react_loop`` + 脚本化 provider → ``TurnOutcome`` 归一化（含工具调用截获）；
- ``RecordingSink``：从 ``run_plan``/``tool_use_start`` 事件还原 roster / tool_calls；
- ``team_outcome`` / ``single_outcome`` 纯映射；
- runner + report：假 harness 跑两例 → 聚合、判定口径、JSON 序列化；
- workspace_fixture：挂副本而非源目录（防夹具污染）。
"""

import asyncio
from pathlib import Path

import pytest

from agentcore.evals.harness import EvalHarness, single_outcome, team_outcome
from agentcore.evals.recording_sink import RecordingSink
from agentcore.evals.report import format_report, report_to_dict
from agentcore.evals.runner import apply_checks, run_suite
from agentcore.evals.types import EvalCase, EvalConfigError, TurnOutcome
from agentcore.llm.profiles import ProfileParams
from agentcore.llm.provider.protocol import LLMChunk, TokenUsage
from agentcore.runtime.events import (
    FinishReason,
    run_completed,
    run_plan,
    tool_use_start,
)


class _ScriptedProvider:
    """每次 ``stream`` 吐一轮预脚本化 chunk（与 test_engine_governance 同款，鸭子类型）。"""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


def _content(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


class _TraceCapturingProvider:
    """Records the trace_id bound in the log context at the moment react_loop calls it —
    proving run_case wraps the engine in a correlation scope (so engine convergence logs
    carry a trace_id even though evals bypass turn_runner)."""

    def __init__(self) -> None:
        self.seen_trace: str | None = None
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        from agentcore.core.log_context import get_log_value

        self.seen_trace = get_log_value("trace_id")
        self.calls += 1
        yield _content("ok")


# --- workspace_fixture 隔离 ----------------------------------------------------


def test_fixture_root_copies_workspace_not_source(tmp_path: Path):
    """有 workspace_fixture 时必须 copytree 到临时目录，源 fixtures 只读不挂。"""
    fixtures = tmp_path / "fixtures"
    src = fixtures / "probe"
    src.mkdir(parents=True)
    (src / "config.yaml").write_text("debug: false\napp: demo\n", encoding="utf-8")
    harness = EvalHarness(fixtures_dir=fixtures)
    case = EvalCase(
        id="t_fixture_copy",
        category="qa",
        user_message="x",
        path="single",
        checks=[],
        workspace_fixture="probe",
    )
    root = harness._fixture_root(case)

    assert root.resolve() != src.resolve()
    assert (root / "config.yaml").read_text(encoding="utf-8") == "debug: false\napp: demo\n"
    (root / "config.yaml").write_text("debug: true\n", encoding="utf-8")
    assert (src / "config.yaml").read_text(encoding="utf-8") == "debug: false\napp: demo\n"


def test_fixture_root_missing_raises():
    harness = EvalHarness(fixtures_dir=Path("/nonexistent-fixtures-dir"))
    case = EvalCase(
        id="t_missing",
        category="qa",
        user_message="x",
        path="single",
        checks=[],
        workspace_fixture="nope",
    )
    with pytest.raises(EvalConfigError, match="workspace_fixture 目录不存在"):
        harness._fixture_root(case)


# --- single 路径：真 react_loop + 脚本化 provider ------------------------------


def test_harness_single_path_content_only():
    provider = _ScriptedProvider([[_content("北京")]])
    harness = EvalHarness(provider=provider)
    case = EvalCase(
        id="t_qa",
        category="qa",
        user_message="中国的首都是哪？",
        path="single",
        checks=[{"name": "FinishReason"}, {"name": "NonEmpty", "args": {"min_len": 2}}],
    )
    outcome = asyncio.run(harness.run_case(case))

    assert outcome.error is None
    assert outcome.content == "北京"
    assert outcome.finish_reason == "end_turn"
    assert outcome.rounds == 1
    assert outcome.tool_calls == []
    assert outcome.cost_usd == 0.0  # 脚本 chunk 不带 usage → 零 token 零成本

    checks = apply_checks(case, outcome)
    assert all(c.passed for c in checks)


def test_run_case_binds_trace_id_for_engine_logs():
    # Evals drive react_loop directly (bypassing turn_runner), so run_case must itself bind
    # a trace_id — otherwise the engine's loop_nudge / loop_finalize / max_rounds logs carry
    # none and can't be correlated (the skew this fix removes from offline log_stats).
    provider = _TraceCapturingProvider()
    harness = EvalHarness(provider=provider)
    case = EvalCase(id="t_trace", category="qa", user_message="hi", path="single", checks=[])

    outcome = asyncio.run(harness.run_case(case))

    assert outcome.error is None
    assert provider.calls == 1
    assert provider.seen_trace  # a trace_id was bound while the engine ran
    assert len(provider.seen_trace) == 32  # uuid4().hex


def test_harness_single_path_surfaces_degraded():
    # B2: the engine empties out (no content, no tool) twice → degraded. The eval
    # outcome must surface "degraded" (via finish_override_sink) instead of masking it
    # as a rounds-derived end_turn, so a regression in convergence is assertable.
    provider = _ScriptedProvider([[], []])
    harness = EvalHarness(provider=provider)
    case = EvalCase(
        id="t_degraded",
        category="qa",
        user_message="…",
        path="single",
        checks=[{"name": "FinishReason", "args": {"expected": "degraded"}}],
    )
    outcome = asyncio.run(harness.run_case(case))

    assert outcome.error is None
    assert outcome.content == ""
    assert outcome.finish_reason == "degraded"
    # and the FinishReason check can now assert that terminal reason directly
    assert all(c.passed for c in apply_checks(case, outcome))


# --- RecordingSink：事件 → 过程事实 -------------------------------------------


def test_recording_sink_captures_roster_and_tool_calls():
    sink = RecordingSink()
    sink.emit(tool_use_start("c1", "web_search", {"query": "x"}))
    sink.emit(
        run_plan(
            execution_id="e1",
            plan_type="multi_agent",
            task_summary="2 worker",
            agents=[
                {"id": "a1", "role": "研究员"},
                {"id": "a2", "role": "撰稿人"},
                {"id": "cap", "role": "CEO"},
            ],
            runs=[],
        )
    )
    # run_completed.role 是成本类目（member），不该污染 roster
    sink.emit(run_completed("a1", "a1", output_summary="done", duration_ms=1, role="member"))

    assert sink.roster == ["研究员", "撰稿人", "CEO"]
    assert sink.tool_calls == [("web_search", '{"query": "x"}')]


def test_recording_sink_dedups_roster():
    sink = RecordingSink()
    agents = [{"id": "a1", "role": "研究员"}, {"id": "a2", "role": "研究员"}]
    sink.emit(
        run_plan(
            execution_id="e",
            plan_type="multi_agent",
            task_summary="",
            agents=agents,
            runs=[],
        )
    )
    assert sink.roster == ["研究员"]


# --- 纯映射函数 ---------------------------------------------------------------


def test_team_outcome_maps_pipeline_result():
    sink = RecordingSink()
    sink.roster = ["研究员", "撰稿人"]
    result = {
        "content": "对比结论……",
        "finish_reason": "end_turn",
        "rounds": 3,
        "citations": [{"url": "a"}],
        "runs": {"events": [{"type": "run_plan"}]},
        "cost_runs": [{"cost": {"total": 1_500_000_000}, "cost_total_nano": 1_500_000_000}],
        "input_tokens": 100,
        "output_tokens": 50,
        "reasoning_tokens": 10,
    }
    oc = team_outcome(result, sink, latency_ms=1234)

    assert oc.content == "对比结论……"
    assert oc.finish_reason == "end_turn"
    assert oc.delegated is True
    assert oc.roster == ["研究员", "撰稿人"]
    assert oc.usage == {"input": 100, "output": 50, "reasoning": 10}
    assert oc.cost_usd == 1.5  # 1.5e9 nano = $1.5
    assert oc.latency_ms == 1234


def test_team_outcome_handles_error_result():
    result = {"content": "", "finish_reason": "error", "error": "boom"}
    oc = team_outcome(result, RecordingSink(), latency_ms=5)
    assert oc.error == "boom"
    assert oc.delegated is False
    assert oc.finish_reason == "error"
    assert oc.cost_usd == 0.0


def test_team_outcome_delegated_derives_from_roster_not_runs():
    # bool(runs) is a false-positive proxy: a CEO that answers directly / asks a
    # clarifying question still yields a non-empty ``runs`` (its own run record),
    # which mislabels zero-orchestration as delegation. ``delegated`` must derive
    # from an actual delegation plan — a non-CEO role in the roster.
    direct = RecordingSink()  # empty roster = CEO handled it itself
    oc = team_outcome(
        {"content": "直接回答", "finish_reason": "end_turn", "rounds": 1, "runs": {"x": 1}},
        direct,
        latency_ms=1,
    )
    assert oc.delegated is False  # would be True under the old bool(runs) proxy

    team = RecordingSink()
    team.roster = ["CEO", "研究员"]
    oc2 = team_outcome(
        {"content": "团队产出", "finish_reason": "end_turn", "rounds": 2, "runs": {}},
        team,
        latency_ms=1,
    )
    assert oc2.delegated is True


def test_single_outcome_derives_finish_and_cost():
    profile = ProfileParams(temperature=0.7, max_rounds=10)
    sink = RecordingSink()
    usage = TokenUsage(cache_miss_tokens=1_000_000, output_tokens=0)
    oc = single_outcome(
        "hi", usage, 3, profile=profile, model="deepseek-v4-flash", sink=sink, citations=[], latency_ms=7
    )
    assert oc.finish_reason == "end_turn"  # rounds(3) < max_rounds(10)
    assert oc.cost_usd > 0  # 1M cache_miss tokens @ flash 价 → 非零成本

    capped = single_outcome(
        "hi", usage, 10, profile=profile, model="deepseek-v4-flash", sink=sink, citations=[], latency_ms=1
    )
    assert capped.finish_reason == "max_rounds"  # rounds 达上限

    unlimited = single_outcome(
        "hi",
        usage,
        1,
        profile=ProfileParams(temperature=0.7, max_rounds=0),
        model="deepseek-v4-flash",
        sink=sink,
        citations=[],
        latency_ms=1,
    )
    assert unlimited.finish_reason == "end_turn"  # 0 = 无轮次熔断


def test_single_outcome_finish_override_wins_over_rounds():
    # When the engine hands back a non-default terminal reason, it must win over the
    # rounds-derivation (here rounds 3 < max 10 would otherwise read "end_turn").
    profile = ProfileParams(temperature=0.7, max_rounds=10)
    sink = RecordingSink()
    usage = TokenUsage()
    degraded = single_outcome(
        "",
        usage,
        3,
        profile=profile,
        model="deepseek-v4-flash",
        sink=sink,
        citations=[],
        latency_ms=1,
        finish_override=FinishReason.DEGRADED,
    )
    assert degraded.finish_reason == "degraded"
    unproductive = single_outcome(
        "salvaged",
        usage,
        3,
        profile=profile,
        model="deepseek-v4-flash",
        sink=sink,
        citations=[],
        latency_ms=1,
        finish_override=FinishReason.UNPRODUCTIVE,
    )
    assert unproductive.finish_reason == "unproductive"


# --- runner + report 端到端（假 harness） -------------------------------------


class _FakeHarness:
    """按 case.id 返回预设 ``TurnOutcome`` 的假 harness——验证 runner/report 不碰真实模型。"""

    async def run_case(self, case: EvalCase) -> TurnOutcome:
        if case.id == "pass_case":
            return TurnOutcome(content="ok", finish_reason="end_turn", rounds=1)
        return TurnOutcome(content="", finish_reason="error", rounds=0, error="x")


def test_run_suite_aggregates_and_serializes():
    cases = [
        EvalCase(
            id="pass_case",
            category="qa",
            user_message="q",
            checks=[{"name": "FinishReason"}, {"name": "NonEmpty"}],
        ),
        EvalCase(
            id="fail_case",
            category="qa",
            user_message="q",
            checks=[{"name": "FinishReason"}],
        ),
    ]
    report = asyncio.run(run_suite(cases, _FakeHarness()))

    assert report.total == 2
    assert report.passed == 1
    assert report.pass_rate == 0.5

    data = report_to_dict(report)
    assert data["summary"]["total"] == 2
    assert data["summary"]["passed"] == 1
    assert data["summary"]["by_category"]["qa"]["total"] == 2
    # error 用例的 FinishReason 应判失败
    fail = next(c for c in data["cases"] if c["case_id"] == "fail_case")
    assert fail["passed"] is False
    assert fail["outcome"]["error"] == "x"

    text = format_report(report)
    assert "总计: 1/2" in text


def test_run_suite_respects_samples():
    case = EvalCase(
        id="pass_case",
        category="qa",
        user_message="q",
        checks=[{"name": "FinishReason"}],
        samples=3,
    )
    report = asyncio.run(run_suite([case], _FakeHarness()))
    assert report.total == 3  # 同 case 跑 3 次 → 3 条
