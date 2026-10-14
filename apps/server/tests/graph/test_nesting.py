"""再开一层时，账目和引用仍折回这段。走法本身见 test_graph_walk。"""

from agentcore.runtime.graph.drive.accumulate import collect_citations
from agentcore.runtime.graph.drive.nesting import absorb_children
from agentcore.runtime.runs.types import RunPhase, RunState
from agentcore.tools.builtin.graph.nesting import make_lead_subteam
from agentcore.tools.builtin.graph.tool import GraphTool
from agentcore.tools.builtin.replan import ReplanTool
from tests.delegate.conftest import Provider, ctx, tool


async def test_finalize_stopped_absorbs_nested_children_usage():
    """停掉这段时，下一层已经花掉的用量折回这段。"""
    parent = tool(Provider([]))
    subteam = make_lead_subteam(parent, "cap1", 1)
    child = subteam.tools[0]
    child._acc.add_usage(
        {"input": 100, "output": 20, "reasoning": 0, "cache_hit": 0, "cache_miss": 0}
    )
    assert parent.usage.get("input", 0) == 0

    from agentcore.runtime.graph.drive.supervised import finalize_stopped
    from agentcore.runtime.runs.plan import RunPlan

    await finalize_stopped(parent, RunPlan(nodes=[]), {})
    assert parent.usage.get("input") == 100
    assert parent.usage.get("output") == 20
    assert parent._children == []


def test_absorb_children_keeps_lead_subteam_continuation_tally():
    parent = tool(Provider([]))
    subteam = make_lead_subteam(parent, "cap1", 1)
    child = subteam.tools[0]
    child.note_continuation("sub_w1")
    assert parent.continuation_count == 1

    absorb_children(parent)
    assert parent._children == []
    assert parent.continuation_count == 1


def test_make_lead_subteam_wires_delegate_plus_replan_bound_to_child():
    parent = tool(Provider([]))
    subteam = make_lead_subteam(parent, "cap1", 1)

    assert subteam.tool_names == ("graph", "replan")
    delegate_tool, replan_tool = subteam.tools
    assert isinstance(delegate_tool, GraphTool)
    assert isinstance(replan_tool, ReplanTool)
    assert replan_tool._delegate is delegate_tool
    assert delegate_tool._depth == 1
    assert parent._children == [delegate_tool]


def test_collect_citations_folds_completed_workers_deduped_excludes_failed():
    t = tool(Provider([]))
    a = {"url": "https://a.com", "title": "A"}
    b = {"url": "https://b.com", "title": "B"}
    results = {
        "r1": RunState(phase=RunPhase.COMPLETED, content="x", citations=[a, b]),
        "r2": RunState(
            phase=RunPhase.COMPLETED,
            content="y",
            citations=[{"url": "https://a.com/#frag", "title": "A again"}],
        ),
        "r3": RunState(
            phase=RunPhase.FAILED,
            content="z",
            citations=[{"url": "https://secret.com", "title": "S"}],
        ),
    }
    collect_citations(t, results)
    assert [c["url"] for c in t.citations] == ["https://a.com", "https://b.com"]


async def test_delegate_result_carries_this_calls_new_citations(monkeypatch):
    a = {"url": "https://a.com", "title": "A"}
    b = {"url": "https://b.com", "title": "B"}
    seq = iter([[a], [a, b]])

    async def _exec(spec, completed):  # noqa: ANN001
        return RunState(phase=RunPhase.COMPLETED, content="X", citations=next(seq))

    monkeypatch.setattr("agentcore.runtime.runs.build_agent_executor", lambda **kw: _exec)
    t = tool(Provider([]))

    r1 = await t.execute(
        {"nodes": [{"id": "核验", "goal": "查法条A"}], "coordinate": False},
        ctx(),
    )
    r2 = await t.execute(
        {"nodes": [{"id": "核验", "goal": "查法条B"}], "coordinate": False},
        ctx(),
    )

    assert [c["url"] for c in (r1.citations or [])] == ["https://a.com"]
    assert [c["url"] for c in (r2.citations or [])] == ["https://b.com"]
    assert [c["url"] for c in t.citations] == ["https://a.com", "https://b.com"]
