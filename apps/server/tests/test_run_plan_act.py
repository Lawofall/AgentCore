"""批 A1：幕声明 wire + fold 合成兼容。"""

from __future__ import annotations

from types import SimpleNamespace

from agentcore.conformance.projection import _act_from_plan, project_turn
from agentcore.runtime.debate.events import moderator_plan_event
from agentcore.runtime.delegate.plan_events import plan_event
from agentcore.runtime.events import graph_append, run_plan
from agentcore.runtime.events.payloads.run import GraphAppendPayload, RunPlanPayload
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunSpec


class _StubDelegate:
    _depth = 0
    _captain_run_id = "cap-1"


class _StubDebate:
    _captain_run_id = "cap-1"


def test_run_plan_payload_accepts_act():
    ev = run_plan(
        execution_id="e1",
        plan_type="multi_agent",
        task_summary="t",
        agents=[],
        runs=[],
        act={"act_id": "act-1", "kind": "multi_agent", "title": "调研"},
    )
    model = RunPlanPayload.model_validate(ev.payload)
    assert model.act is not None
    assert model.act.act_id == "act-1"
    assert model.act.kind == "multi_agent"
    assert model.act.title == "调研"


def test_graph_append_payload_accepts_act_fields():
    ev = graph_append(
        execution_id="e1",
        host_message_id="m1",
        append_message_id="m2",
        added_count=1,
        act_id="act-1",
        act_kind="multi_agent",
    )
    model = GraphAppendPayload.model_validate(ev.payload)
    assert model.act_id == "act-1"
    assert model.act_kind == "multi_agent"


def test_delegate_plan_event_emits_act_1_multi_agent():
    plan = RunPlan(nodes=[RunSpec(run_id="r1", task="调研", role="研究员")])
    ev = plan_event(_StubDelegate(), "e1", plan)
    assert ev.payload["act"] == {"act_id": "act-1", "kind": "multi_agent"}


def test_delegate_plan_event_injects_captain_on_first_dispatch():
    plan = RunPlan(nodes=[RunSpec(run_id="r1", task="调研", role="研究员")])
    ev = plan_event(_StubDelegate(), "e1", plan)
    runs = ev.payload["runs"]
    assert runs[0]["kind"] == "captain"
    assert runs[0]["id"] == "cap-1"
    assert ev.payload["agents"][0]["role"] == "CEO"


def test_delegate_plan_event_carries_prev_execution_id():
    plan = RunPlan(nodes=[RunSpec(run_id="r1", task="调研", role="研究员")])
    ev = plan_event(_StubDelegate(), "e2", plan, prev_execution_id="e1")
    assert ev.payload.get("prev_execution_id") == "e1"
    assert "host_message_id" not in ev.payload
    # 新图仍注入本回合 captain
    assert ev.payload["runs"][0]["kind"] == "captain"
    assert ev.payload["runs"][0]["id"] == "cap-1"


def test_run_plan_payload_accepts_prev_execution_id():
    ev = run_plan(
        execution_id="e2",
        plan_type="multi_agent",
        task_summary="t",
        agents=[],
        runs=[],
        prev_execution_id="e1",
    )
    model = RunPlanPayload.model_validate(ev.payload)
    assert model.prev_execution_id == "e1"


def test_run_plan_payload_accepts_note_wall():
    ev = run_plan(
        execution_id="e1",
        plan_type="multi_agent",
        task_summary="t",
        agents=[],
        runs=[],
        note_wall=True,
    )
    model = RunPlanPayload.model_validate(ev.payload)
    assert model.note_wall is True


def test_run_plan_omits_note_wall_when_false():
    ev = run_plan(
        execution_id="e1",
        plan_type="multi_agent",
        task_summary="t",
        agents=[],
        runs=[],
        note_wall=False,
    )
    assert "note_wall" not in ev.payload


class _WallDelegate:
    _depth = 0
    _captain_run_id = "cap-1"
    _coordination = "wall"


def test_delegate_plan_event_emits_note_wall_for_parallel_wall_batch():
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="r1", task="接口", role="后端"),
            RunSpec(run_id="r2", task="页面", role="前端"),
        ]
    )
    ev = plan_event(_WallDelegate(), "e1", plan)
    assert ev.payload.get("note_wall") is True


def test_delegate_plan_event_omits_note_wall_for_solo_or_none():
    solo = RunPlan(nodes=[RunSpec(run_id="r1", task="调研", role="研究员")])
    ev = plan_event(_WallDelegate(), "e1", solo)
    assert "note_wall" not in ev.payload
    parallel = RunPlan(
        nodes=[
            RunSpec(run_id="r1", task="接口", role="后端"),
            RunSpec(run_id="r2", task="页面", role="前端"),
        ]
    )
    ev = plan_event(_StubDelegate(), "e1", parallel)
    assert "note_wall" not in ev.payload


def test_project_turn_note_wall_without_notes():
    projected = project_turn(
        [
            {
                "type": "run_plan",
                "payload": {
                    "execution_id": "e1",
                    "plan_type": "multi_agent",
                    "task_summary": "t",
                    "note_wall": True,
                    "agents": [
                        {"id": "a1", "role": "后端", "thinking": True},
                        {"id": "a2", "role": "前端", "thinking": True},
                    ],
                    "runs": [
                        {"id": "r1", "agent_id": "a1", "task": "接口", "depends_on": []},
                        {"id": "r2", "agent_id": "a2", "task": "页面", "depends_on": []},
                    ],
                },
            }
        ]
    )
    assert projected["teamNotes"] == []
    assert projected.get("noteWall") is True


def test_debate_moderator_plan_event_emits_act_1_debate():
    cfg = SimpleNamespace(form="oxford", motion="是否应采用方案 A")
    ev = moderator_plan_event(_StubDebate(), "e1", "mod-1", cfg)  # type: ignore[arg-type]
    assert ev.payload["act"] == {"act_id": "act-1", "kind": "debate"}


def test_act_from_plan_synthesizes_legacy():
    act = _act_from_plan({"plan_type": "debate"})
    assert act == {
        "actId": "act-1",
        "kind": "debate",
        "title": None,
        "anchorRunId": None,
        "authorizedBy": None,
    }


def test_project_turn_synthesizes_single_act_for_legacy_run_plan():
    projected = project_turn(
        [
            {
                "type": "run_plan",
                "payload": {
                    "execution_id": "e1",
                    "plan_type": "multi_agent",
                    "task_summary": "t",
                    "agents": [
                        {
                            "id": "a1",
                            "role": "研究员",
                            "thinking": True,
                        }
                    ],
                    "runs": [
                        {
                            "id": "r1",
                            "agent_id": "a1",
                            "task": "调研",
                            "depends_on": [],
                        }
                    ],
                },
            }
        ]
    )
    assert projected["acts"] == [
        {
            "actId": "act-1",
            "kind": "multi_agent",
            "title": None,
            "anchorRunId": None,
            "authorizedBy": None,
        }
    ]
    assert projected["runs"][0]["actId"] == "act-1"


def test_project_turn_uses_wire_act_when_present():
    projected = project_turn(
        [
            {
                "type": "run_plan",
                "payload": {
                    "execution_id": "e1",
                    "plan_type": "debate",
                    "task_summary": "t",
                    "agents": [],
                    "runs": [
                        {
                            "id": "r1",
                            "agent_id": "a1",
                            "task": "主持",
                            "depends_on": [],
                        }
                    ],
                    "act": {
                        "act_id": "act-1",
                        "kind": "debate",
                        "title": "辩论对抗",
                    },
                },
            }
        ]
    )
    assert projected["acts"] == [
        {
            "actId": "act-1",
            "kind": "debate",
            "title": "辩论对抗",
            "anchorRunId": None,
            "authorizedBy": None,
        }
    ]
    assert projected["runs"][0]["actId"] == "act-1"
