"""协作形状评测：RecordingSink 计划图/互动计数 + shape_score 零 LLM 单测."""

from __future__ import annotations

from agentcore.evals.checks import DIAGNOSTIC_CHECKS, build_check
from agentcore.evals.harness import team_outcome
from agentcore.evals.recording_sink import RecordingSink
from agentcore.evals.runner import load_cases
from agentcore.evals.shape_score import score_shape
from agentcore.evals.types import EvalCase, TurnOutcome
from agentcore.runtime.events import (
    escalation_raised,
    plan_revised,
    run_plan,
    run_started,
)


def _agents(*roles: tuple[str, str]) -> list[dict]:
    return [
        {"id": aid, "role": role, "thinking": False}
        for aid, role in roles
    ]


# --- RecordingSink：计划图 + 互动 -------------------------------------------------


def test_recording_sink_captures_plan_runs_filters_captain():
    sink = RecordingSink()
    sink.emit(
        run_plan(
            execution_id="e1",
            plan_type="multi_agent",
            task_summary="对比",
            agents=_agents(
                ("cap", "CEO"),
                ("a1", "OpenAI调研员"),
                ("a2", "Google调研员"),
                ("a3", "对比员"),
            ),
            runs=[
                {
                    "id": "cap",
                    "agent_id": "cap",
                    "task": "",
                    "depends_on": [],
                    "kind": "captain",
                    "parent_run_id": None,
                },
                {
                    "id": "r1",
                    "agent_id": "a1",
                    "task": "调研 OpenAI",
                    "depends_on": [],
                    "parent_run_id": None,
                },
                {
                    "id": "r2",
                    "agent_id": "a2",
                    "task": "调研 Google",
                    "depends_on": [],
                    "parent_run_id": None,
                },
                {
                    "id": "r3",
                    "agent_id": "a3",
                    "task": "横向对比",
                    "depends_on": ["r1", "r2"],
                    "parent_run_id": None,
                },
            ],
        )
    )

    assert sink.plan_type == "multi_agent"
    assert [r["id"] for r in sink.plan_runs] == ["r1", "r2", "r3"]
    assert all(r["role"] != "CEO" for r in sink.plan_runs)
    assert sink.plan_runs[2]["depends_on"] == ["r1", "r2"]
    assert sink.plan_runs[0]["role"] == "OpenAI调研员"


def test_recording_sink_merges_nested_plan_and_counts_interactions():
    sink = RecordingSink()
    sink.emit(
        run_plan(
            execution_id="e",
            plan_type="multi_agent",
            task_summary="顶层",
            agents=_agents(("lead", "分组Lead"), ("cmp", "对比员")),
            runs=[
                {
                    "id": "lead",
                    "agent_id": "lead",
                    "task": "带组",
                    "depends_on": [],
                    "parent_run_id": None,
                },
                {
                    "id": "cmp",
                    "agent_id": "cmp",
                    "task": "汇总",
                    "depends_on": ["lead"],
                    "parent_run_id": None,
                },
            ],
        )
    )
    # 嵌套子计划：lead 扇出子 worker
    sink.emit(
        run_plan(
            execution_id="e",
            plan_type="multi_agent",
            task_summary="子组",
            agents=_agents(("w1", "维度A"), ("w2", "维度B")),
            runs=[
                {
                    "id": "w1",
                    "agent_id": "w1",
                    "task": "A",
                    "depends_on": [],
                    "parent_run_id": "lead",
                },
                {
                    "id": "w2",
                    "agent_id": "w2",
                    "task": "B",
                    "depends_on": [],
                    "parent_run_id": "lead",
                },
            ],
        )
    )
    sink.emit(
        escalation_raised(
            "w2", "w2", question="缺数据", assumption="跳过", blocking=False, kind="dep"
        )
    )
    sink.emit(plan_revised(execution_id="e", revisions=[{"run_id": "cmp", "kind": "steer"}]))
    sink.emit(run_started("w1b", "w1", continues_run_id="w1"))

    assert {r["id"] for r in sink.plan_runs} == {"lead", "cmp", "w1", "w2"}
    assert any(r.get("parent_run_id") == "lead" for r in sink.plan_runs)
    assert sink.collab_interactions == {
        "escalate": 1,
        "replan": 1,
        "continue": 1,
    }


def test_team_outcome_carries_plan_and_interactions():
    sink = RecordingSink()
    sink.plan_runs = [{"id": "r1", "role": "调研", "task": "x", "depends_on": [], "parent_run_id": None}]
    sink.plan_type = "multi_agent"
    sink.collab_interactions = {"escalate": 2}
    sink.roster = ["调研"]
    oc = team_outcome(
        {"content": "ok", "finish_reason": "end_turn", "rounds": 1},
        sink,
        latency_ms=1,
    )
    assert oc.plan_runs == sink.plan_runs
    assert oc.plan_type == "multi_agent"
    assert oc.collab_interactions == {"escalate": 2}


# --- shape_score -----------------------------------------------------------------


def _plan(*nodes: dict) -> list[dict]:
    return list(nodes)


def test_score_shape_perfect_multi_object():
    plan = _plan(
        {"id": "a", "role": "实体A", "task": "", "depends_on": [], "parent_run_id": None},
        {"id": "b", "role": "实体B", "task": "", "depends_on": [], "parent_run_id": None},
        {"id": "c", "role": "实体C", "task": "", "depends_on": [], "parent_run_id": None},
        {"id": "d", "role": "实体D", "task": "", "depends_on": [], "parent_run_id": None},
        {
            "id": "j",
            "role": "对比员",
            "task": "",
            "depends_on": ["a", "b", "c", "d"],
            "parent_run_id": None,
        },
    )
    expected = {
        "min_workers": 5,
        "parallel_fanout_min": 4,
        "has_join": True,
        "min_roles": 2,
    }
    result = score_shape(plan, expected)
    assert result.score == 1.0
    assert all(v == 1.0 for v in result.details.values())


def test_score_shape_partial_and_reviewer():
    plan = _plan(
        {"id": "r", "role": "调研", "task": "", "depends_on": [], "parent_run_id": None},
        {"id": "w", "role": "撰稿", "task": "", "depends_on": ["r"], "parent_run_id": None},
    )
    # 缺独立审校、fanout 不足 → 分项拉低
    result = score_shape(
        plan,
        {
            "min_workers": 4,
            "parallel_fanout_min": 2,
            "pipeline_depth_min": 1,
            "independent_reviewer": True,
        },
    )
    assert 0.0 < result.score < 1.0
    assert result.details["independent_reviewer"] == 0.0
    assert result.details["pipeline_depth_min"] == 1.0

    plan2 = plan + [
        {
            "id": "rev",
            "role": "独立审校",
            "task": "",
            "depends_on": ["w"],
            "parent_run_id": None,
        }
    ]
    result2 = score_shape(
        plan2,
        {"min_workers": 3, "independent_reviewer": True, "pipeline_depth_min": 2},
    )
    assert result2.details["independent_reviewer"] == 1.0
    assert result2.score == 1.0


def test_score_shape_debate_plan_type():
    plan = _plan(
        {"id": "pro", "role": "正方", "task": "", "depends_on": [], "parent_run_id": None},
        {"id": "con", "role": "反方", "task": "", "depends_on": [], "parent_run_id": None},
    )
    result = score_shape(plan, {"plan_types": ["debate"], "min_workers": 2}, plan_type="debate")
    assert result.score == 1.0
    miss = score_shape(plan, {"plan_types": ["debate"]}, plan_type="multi_agent")
    assert miss.details["plan_types"] == 0.0


def test_shape_matches_check_is_diagnostic():
    assert "ShapeMatches" in DIAGNOSTIC_CHECKS
    case = EvalCase(
        id="t",
        category="team",
        user_message="q",
        expected_shape={"min_workers": 2},
        checks=[{"name": "ShapeMatches"}],
    )
    outcome = TurnOutcome(
        content="x",
        finish_reason="end_turn",
        rounds=1,
        plan_runs=[
            {"id": "a", "role": "A", "task": "", "depends_on": [], "parent_run_id": None},
            {"id": "b", "role": "B", "task": "", "depends_on": [], "parent_run_id": None},
        ],
    )
    ck = build_check({"name": "ShapeMatches", "args": {"threshold": 0.6}})
    result = ck.run(case, outcome)
    assert result.passed is True
    assert "score=" in result.detail


def test_collab_shapes_suite_loads_and_lints():
    cases = load_cases(suite="collab_shapes")
    assert len(cases) == 17
    ids = {c.id for c in cases}
    assert ids == {
        "collab_p1_multi_object_compare",
        "collab_p2_depth_writing",
        "collab_p3_build_tool",
        "collab_p4_open_decision",
        "collab_p5_creative_options",
        "collab_p6_review_diagnose",
        "collab_p7_composite_plan",
        "collab_par_align_brief",
        "collab_par_explore_all_angles",
        "collab_par_frontend_stack",
        "collab_par_km_tools",
        "collab_solo_config_line",
        "collab_solo_essay_file",
        "collab_xd_design_to_api",
        "collab_xd_meeting_notes_synth",
        "collab_xd_portfolio_pipeline",
        "collab_xd_ui_direction_mvp",
    }
    by_id = {c.id: c for c in cases}
    assert by_id["collab_solo_config_line"].workspace_fixture == "probe_workspace"
    ui = by_id["collab_xd_ui_direction_mvp"]
    assert ui.expected_shape is not None
    assert ui.expected_shape.get("max_workers") == 1
    assert any(c.get("name") == "DelegateCriteriaForbidden" for c in ui.checks)
    for c in cases:
        assert c.path == "team"
        assert c.mast is not None
        assert c.expected_shape is not None


def test_delegate_criteria_forbidden_check():
    case = EvalCase(id="t", category="team", user_message="x")
    check = build_check(
        {"name": "DelegateCriteriaForbidden", "args": {"forbid": ["code_verified"]}}
    )

    # S3: any completion_criteria key fails (field retired).
    legacy = check.run(
        case,
        TurnOutcome(
            content="ok",
            finish_reason="end_turn",
            rounds=1,
            tool_calls=[
                (
                    "delegate",
                    '{"completion_criteria":{"type":"files_written"},"tasks":[{"role":"a"}]}',
                )
            ],
        ),
    )
    assert legacy.passed is False

    bad = check.run(
        case,
        TurnOutcome(
            content="ok",
            finish_reason="end_turn",
            rounds=1,
            tool_calls=[
                (
                    "delegate",
                    '{"completion_criteria":{"type":"code_verified","verify_command":"npm run build"}}',
                )
            ],
        ),
    )
    assert bad.passed is False

    omit = check.run(
        case,
        TurnOutcome(
            content="ok",
            finish_reason="end_turn",
            rounds=1,
            tool_calls=[("delegate", '{"tasks":[{"role":"a"}]}')],
        ),
    )
    assert omit.passed is True

    none = check.run(
        case,
        TurnOutcome(content="ok", finish_reason="end_turn", rounds=1, tool_calls=[]),
    )
    assert none.passed is False
