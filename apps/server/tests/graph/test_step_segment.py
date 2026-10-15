"""One thought draws its things. Several things in that thought sit side by side."""

from types import SimpleNamespace

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.graph.facing.plan_events import plan_event
from agentcore.runtime.graph.shape.conversation_graph import fold_conversation_graph
from agentcore.runtime.graph.shape.opening_segment import spec_for_open_think
from agentcore.runtime.graph.shape.step_segment import (
    StepChain,
    bind_step_chain,
    current_step_chain,
    open_parallel,
    open_think,
    reply_events,
    reset_step_chain,
    seal_open_things,
    seal_think,
    things_from_calls,
)
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunSpec


def test_one_call_is_one_segment_and_uses_the_goal():
    chain = StepChain(execution_id="exec", upstream=["seg_prev"], goal="查遵义天气")
    events = open_think(chain, run_id="cap", round_idx=1)
    runs = events[0].payload["runs"]
    assert len(runs) == 1
    assert runs[0]["depends_on"] == ["seg_prev"]
    assert runs[0]["task"] == "查遵义天气"
    assert runs[0]["task"] != "web_search"
    assert len(events) == 2


def test_goal_stays_whole_for_the_panel():
    goal = "把登录页的错误提示改成用户能看懂的一句话，并写上下一步该做什么"
    chain = StepChain(execution_id="exec", upstream=[], goal=goal)
    events = open_think(chain, run_id="cap", round_idx=1)
    assert events[0].payload["runs"][0]["task"] == goal


def test_next_call_hangs_off_the_previous_one():
    chain = StepChain(execution_id="exec", upstream=["seg_prev"], goal="查天气")
    first = open_think(chain, run_id="cap", round_idx=1)
    first_id = first[0].payload["runs"][0]["id"]
    sealed = seal_think(chain, "")
    assert sealed is not None
    assert sealed.payload.get("debrief") is None
    nxt = open_think(chain, run_id="cap", round_idx=2)
    run = nxt[0].payload["runs"][0]
    assert run["depends_on"] == [first_id]
    assert run["task"] == "这段"
    assert run["id"] != first_id


def test_conclusion_seals_once_and_is_not_a_debrief():
    chain = StepChain(execution_id="exec", upstream=[], goal="先拆开")
    open_think(chain, run_id="cap", round_idx=1)
    first = seal_think(chain, "结论写在这里")
    second = seal_think(chain, "再写一次")
    assert first is not None
    assert second is None
    assert first.payload["output_summary"] == "结论写在这里"
    assert "debrief" not in first.payload


def test_one_thing_seals_with_its_result():
    chain = StepChain(execution_id="exec", upstream=["seg_prev"], goal="查天气")
    open_think(chain, run_id="cap", round_idx=1)
    sealed = seal_open_things(chain, ["遵义晴"])
    assert len(sealed) == 1
    assert sealed[0].payload["output_summary"] == "遵义晴"
    assert "debrief" not in sealed[0].payload


def test_parallel_things_share_the_upstream_and_the_next_thought_joins():
    chain = StepChain(execution_id="exec", upstream=["seg_prev"], goal="查两地")
    opened = open_parallel(
        chain,
        run_id="cap",
        round_idx=1,
        labels=["遵义天气", "贵阳天气"],
    )
    sealed = seal_open_things(chain, ["遵义晴", "贵阳雨"])
    journal = []
    for event in (*opened, *sealed):
        journal.append({"kind": event.type.value, "payload": event.payload})
    graph = fold_conversation_graph([journal])
    assert len(graph.segments) == 2
    assert [segment.depends_on for segment in graph.segments] == [
        ("seg_prev",),
        ("seg_prev",),
    ]
    assert {segment.status for segment in graph.segments} == {"sealed"}
    assert [event.payload["output_summary"] for event in sealed] == ["遵义晴", "贵阳雨"]
    tasks = opened[0].payload["runs"]
    assert [run["task"] for run in tasks] == ["遵义天气", "贵阳天气"]
    nxt = open_think(chain, run_id="cap", round_idx=2)
    assert set(nxt[0].payload["runs"][0]["depends_on"]) == {
        segment.id for segment in graph.segments
    }


def test_graph_call_is_not_split_into_things():
    fork = SimpleNamespace(id="1", function=SimpleNamespace(name="graph", arguments="{}"))
    other = SimpleNamespace(
        id="2",
        function=SimpleNamespace(name="web_search", arguments='{"query":"遵义"}'),
    )
    assert things_from_calls([fork, other], []) == []


def test_thing_label_uses_the_query_and_keeps_the_result():
    call = SimpleNamespace(
        id="1",
        function=SimpleNamespace(name="web_search", arguments='{"query":"遵义天气"}'),
    )
    message = LLMMessage(role="tool", content="晴", tool_call_id="1")
    assert things_from_calls([call], [message]) == [("遵义天气", "晴")]


def test_plan_frame_keeps_the_thought_upstream():
    token = bind_step_chain("exec", ["seg_prev"], goal="测试下graph功能")
    try:
        chain = current_step_chain()
        assert chain is not None
        opened = open_think(chain, run_id="cap", round_idx=0)
        spec = spec_for_open_think(chain, depth=1, parent_run_id="cap")
        sealed = seal_think(chain, "先看通路")
        worker = RunSpec(run_id="w1", task="回一句", depends_on=[spec.run_id])
        framed = plan_event(object(), "e1", RunPlan(nodes=[spec, worker]))
        framed_ids = [run["id"] for run in framed.payload["runs"]]
        assert spec.run_id not in framed_ids
        assert "w1" in framed_ids
        journal = [
            {"kind": event.type.value, "payload": event.payload}
            for event in (*opened, sealed, framed)
            if event is not None
        ]
        graph = fold_conversation_graph([journal])
        by_id = {segment.id: segment for segment in graph.segments}
        assert by_id[spec.run_id].depends_on == ("seg_prev",)
        assert by_id["w1"].depends_on == (spec.run_id,)
    finally:
        reset_step_chain(token)


def test_spoken_reply_hangs_off_the_sealed_call():
    chain = StepChain(execution_id="exec", upstream=[], goal="改登录页")
    opened = open_think(chain, run_id="cap", round_idx=1)
    sealed = seal_think(chain, "")
    spoken = reply_events(chain, run_id="cap", text="可以改登录页")
    journal = []
    for event in (*opened, sealed, *spoken):
        assert event is not None
        journal.append({"kind": event.type.value, "payload": event.payload})
    graph = fold_conversation_graph([journal])
    assert len(graph.segments) == 2
    reply = graph.segments[-1]
    assert reply.depends_on == (graph.segments[0].id,)
