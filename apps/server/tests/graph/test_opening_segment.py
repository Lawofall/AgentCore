"""Opening segment: first graph step, sealed before later nodes run."""

from agentcore.runtime.graph.shape.opening_segment import (
    link_empty_deps_to_opening,
    opening_completed,
    opening_label,
    opening_segment_id,
    opening_spec,
    plan_contains_opening,
    plan_with_opening,
    spec_for_open_think,
)
from agentcore.runtime.graph.shape.step_segment import StepChain, open_think
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunOrigin, RunPhase, RunSpec


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


def test_open_think_spec_does_not_carry_conversation_edges():
    chain = StepChain(execution_id="exec", upstream=["seg_prev"], goal="测试下graph功能")
    open_think(chain, run_id="cap", round_idx=0)
    spec = spec_for_open_think(chain, depth=1, parent_run_id="cap")
    assert spec.run_id == chain.think_id
    assert spec.depends_on == []
    assert chain.opened_from == ["seg_prev"]
    assert spec.task == "测试下graph功能"
    assert spec.parent_run_id == "cap"


def test_plan_with_opening_is_shared_by_both_producers():
    opening = opening_spec(captain_run_id="cap", user_message="先查价格")
    fresh = plan_with_opening(None, opening)
    assert [node.run_id for node in fresh.nodes] == ["seg_cap"]
    assert fresh.origin is RunOrigin.CAPTAIN

    host = RunPlan(nodes=[opening], origin=RunOrigin.TEMPLATE)
    again = plan_with_opening(host, opening)
    assert again is host

    other = RunPlan(
        nodes=[RunSpec(run_id="old", task="旧")],
        origin=RunOrigin.TEMPLATE,
    )
    combined = plan_with_opening(other, opening)
    assert [node.run_id for node in combined.nodes] == ["seg_cap", "old"]
    assert combined.origin is RunOrigin.TEMPLATE


def test_later_nodes_can_name_the_open_think():
    chain = StepChain(execution_id="exec", upstream=["seg_prev"], goal="测试")
    open_think(chain, run_id="cap", round_idx=0)
    spec = spec_for_open_think(chain, depth=1, parent_run_id="cap")
    host = plan_with_opening(None, spec)
    tasks = link_empty_deps_to_opening(
        [{"id": "a", "role": "a", "task": "做", "depends_on": []}],
        spec.run_id,
    )
    plan, errors = build_run_plan(tasks, existing_plan=host, id_prefix="del_x")
    assert errors == []
    assert plan.nodes[0].depends_on == [spec.run_id]


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
