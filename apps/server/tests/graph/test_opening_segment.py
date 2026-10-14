"""Opening segment: first graph step, sealed before later nodes run."""

from agentcore.runtime.graph.shape.opening_segment import (
    link_empty_deps_to_opening,
    opening_completed,
    opening_label,
    opening_segment_id,
    opening_spec,
    plan_contains_opening,
)
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunOrigin, RunPhase


def test_opening_id_is_not_the_captain_sink():
    assert opening_segment_id("cap") == "seg_cap"
    assert opening_segment_id("cap") != "cap"


def test_label_clips_the_user_message():
    assert opening_label("  做 一个  ") == "做 一个"
    assert opening_label("x" * 30).endswith("…")
    assert len(opening_label("x" * 30)) == 25


def test_empty_deps_wait_on_the_opening_segment():
    linked = link_empty_deps_to_opening(
        [
            {"id": "a", "goal": "甲", "depends_on": []},
            {"id": "b", "goal": "乙", "depends_on": ["a"]},
        ],
        "seg_cap",
    )
    assert linked[0]["depends_on"] == ["seg_cap"]
    assert linked[1]["depends_on"] == ["a"]


def test_sealed_state_is_the_conclusion_only():
    spec = opening_spec(captain_run_id="cap", user_message="先查价格")
    state = opening_completed("三家都高于预算")
    plan = RunPlan(nodes=[spec])
    assert plan_contains_opening(plan, "cap")
    assert state.phase is RunPhase.COMPLETED
    assert state.content == "三家都高于预算"
    assert spec.kind.value == "agent"
    assert spec.run_id == "seg_cap"


def test_later_nodes_can_name_the_opening_segment():
    spec = opening_spec(captain_run_id="cap", user_message="先查价格")
    host = RunPlan(nodes=[spec], origin=RunOrigin.CAPTAIN)
    tasks = link_empty_deps_to_opening(
        [{"id": "a", "role": "a", "task": "比价", "depends_on": []}],
        spec.run_id,
    )
    plan, errors = build_run_plan(tasks, existing_plan=host, id_prefix="del_x")
    assert errors == []
    assert plan.nodes[0].depends_on == [spec.run_id]
