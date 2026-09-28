"""Nested delegation tests."""

import json
import re

from agentcore.llm.provider.protocol import LLMChunk, TokenUsage, ToolCallDelta
from agentcore.runtime.delegate.accumulate import collect_citations
from agentcore.runtime.delegate.nesting import absorb_children
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.runs.types import RunPhase, RunState
from agentcore.tools.builtin.delegate.nesting import make_lead_subteam
from agentcore.tools.builtin.delegate.tool import DelegateTool
from agentcore.tools.builtin.escalate import EscalateTool
from agentcore.tools.builtin.replan import ReplanTool
from agentcore.tools.registry import ToolRegistry
from tests.delegate.conftest import (
    SCOPE_DAG,
    NestingProvider,
    Provider,
    ScopeProvider,
    _upstream_body,
    ctx,
    nesting_tool,
    tool,
)


async def test_nested_delegation_runs_subteam_links_tree_and_rolls_up():
    sink = EventSink()
    usage = TokenUsage(
        input_tokens=10,
        output_tokens=5,
        reasoning_tokens=0,
        cache_hit_tokens=6,
        cache_miss_tokens=4,
    )
    provider = NestingProvider(usage=usage)
    t = nesting_tool(provider, sink)

    result = await t.execute(
        {"tasks": [{"role": "队长", "task": "主任务"}], "coordinate": False}, ctx()
    )

    assert result.success is True
    assert result.is_terminal is False
    assert provider.delegate_calls == 1
    assert "CAPTAIN_FINAL" in result.output

    sink.close()
    events = [e async for e in sink]
    starts = [e for e in events if e.type == EventType.RUN_STARTED]
    by_parent: dict[str | None, list[str]] = {}
    for e in starts:
        by_parent.setdefault(e.payload["parent_run_id"], []).append(e.payload["run_id"])
    assert len(by_parent["CEO"]) == 1
    cap_id = by_parent["CEO"][0]
    assert len(by_parent[cap_id]) == 2

    plan_runs = [r for e in events if e.type == EventType.RUN_PLAN for r in e.payload["runs"]]
    parents = {r["id"]: r["parent_run_id"] for r in plan_runs}
    assert parents[cap_id] == "CEO"
    assert all(parents[sub_id] == cap_id for sub_id in by_parent[cap_id])

    assert t.usage["input"] == 40
    assert t.usage["output"] == 20
    assert t.usage["cache_hit"] == 24

    assert len(t.run_ledger) == 3
    cap_rows = [r for r in t.run_ledger if r.parent_run_id == "CEO"]
    sub_rows = [r for r in t.run_ledger if r.parent_run_id == cap_id]
    assert len(cap_rows) == 1
    assert cap_rows[0].run_id == cap_id
    assert len(sub_rows) == 2


async def test_finalize_stopped_absorbs_nested_children_usage():
    """F2 regression: replan(stop)/dispose paths call finalize_stopped — nested lead
    sub-team spend on the child's _acc must fold into the parent, same as drive.py."""
    parent = tool(Provider([]))
    subteam = make_lead_subteam(parent, "cap1", 1)
    child = subteam.tools[0]
    child._acc.add_usage(
        {"input": 100, "output": 20, "reasoning": 0, "cache_hit": 0, "cache_miss": 0}
    )
    assert parent.usage.get("input", 0) == 0

    from agentcore.runtime.delegate.supervised import finalize_stopped
    from agentcore.runtime.runs.plan import RunPlan

    await finalize_stopped(parent, RunPlan(nodes=[]), {})
    assert parent.usage.get("input") == 100
    assert parent.usage.get("output") == 20
    assert parent._children == []


def test_absorb_children_keeps_lead_subteam_continuation_tally():
    """turn_metrics.revises 口径: 子团队的续派计数必须走和 usage / collab 同一条 merge 路。
    曾经它挂在子 tool 上、absorb 后随 children 一起被清空 → lead 子团队的续派系统性少计。"""
    parent = tool(Provider([]))
    subteam = make_lead_subteam(parent, "cap1", 1)
    child = subteam.tools[0]
    child.note_continuation("sub_w1")
    assert parent.continuation_count == 1  # 折叠前经在册 child 计入

    absorb_children(parent)
    assert parent._children == []
    assert parent.continuation_count == 1  # 折叠后仍在（不重复、也不丢）


async def test_depth_three_subworker_cannot_delegate_further():
    """MAX=3: depth-1 and depth-2 may nest; depth-3 leaf has no CAPTAIN_MARK."""

    class DeepProvider(NestingProvider):
        async def stream(self, request):
            system = next((m.content or "" for m in request.messages if m.role == "system"), "")
            user = next((m.content or "" for m in request.messages if m.role == "user"), "")
            is_captain = self.CAPTAIN_MARK in user or self.CAPTAIN_MARK in system
            has_result = any(m.role == "tool" for m in request.messages)
            if is_captain and not has_result:
                self.delegate_calls += 1
                args = json.dumps(
                    {"tasks": [{"role": "子队长", "task": "子任务"}]}
                )
                yield LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="sub-tc",
                            function_name="delegate",
                            arguments_delta=args,
                        )
                    ]
                )
            elif is_captain:
                yield LLMChunk(delta_content="CAPTAIN_FINAL")
            else:
                yield LLMChunk(delta_content=_upstream_body("SUBOUT"))

    provider = DeepProvider()
    t = nesting_tool(provider, EventSink())
    result = await t.execute(
        {"tasks": [{"role": "队长", "task": "主任务"}], "coordinate": False}, ctx()
    )
    assert result.success is True
    # depth-1 nests once, depth-2 nests once; depth-3 leaf never adds a call.
    assert provider.delegate_calls == 2


def test_make_lead_subteam_wires_delegate_plus_replan_bound_to_child():
    # 受监督子计划 B 去特例: a lead's bundle mints BOTH its own delegate AND a
    # replan bound to THAT child (not the root CEO's). Opening offer is delegate
    # only; replan is promoted once a sub-plan exists. Without the bound replan
    # a yielding sub-plan would be a dead-end. The child is also registered on
    # the parent so absorb_children later folds its ledger into the turn totals.
    parent = tool(Provider([]))
    subteam = make_lead_subteam(parent, "cap1", 1)

    assert subteam.tool_names == ("delegate", "replan")
    delegate_tool, replan_tool = subteam.tools
    assert isinstance(delegate_tool, DelegateTool)
    assert isinstance(replan_tool, ReplanTool)
    # the crux: replan targets THIS lead's child, so a replan steers the lead's own sub-plan
    assert replan_tool._delegate is delegate_tool
    assert delegate_tool._depth == 1
    assert parent._children == [delegate_tool]


async def test_lead_subteam_dispose_folds_yielded_subplan_before_parent_absorbs():
    # 堵漏账 (docs/03-AI核心/编排器与CEO主Agent.md §2.4 B 清单 ②): a lead opened a sub-plan that
    # YIELDed at a SCOPE boundary but its react loop ended without a replan. The bundle's
    # dispose runs the implicit-stop fold on the CHILD (the same path the CEO's host uses at turn
    # end), so the completed sub-team's spend lands on the child's ledger — which the parent's
    # absorb_children then merges. Timing matters: dispose MUST precede absorb, else the spend is
    # stranded unbilled. The executor's finally enforces exactly this ordering in production.
    parent = tool(ScopeProvider(usage=TokenUsage(input_tokens=100, output_tokens=20)))
    parent._tools.register(EscalateTool())
    subteam = make_lead_subteam(parent, "cap1", 1)
    child = subteam.tools[0]

    first = await child.execute({"tasks": SCOPE_DAG, "coordinate": False}, ctx())
    assert first.is_terminal is False  # the SCOPE boundary yielded a「计划已让出」brief
    assert child._supervised is not None
    assert child.usage.get("input", 0) == 0  # yield path left the upstream's spend un-folded

    await subteam.dispose()  # the bundle's closure → child.dispose_open_supervised()

    assert child._supervised is None  # dangling sub-plan released
    assert child.usage.get("input") == 100  # folded onto the child as an implicit stop
    absorb_children(parent)
    assert parent.usage.get("input") == 100  # …and the parent picks it up — nothing stranded


class _LeadScopeSteerProvider:
    """Drives a LEAD through the SCOPE arm of the 受监督 loop: one of its sub-workers reports a
    职责偏离 (`escalate kind=scope`) with an un-run downstream, so the sub-plan YIELDs a SCOPE
    「计划已让出」brief; the lead catches it and `replan`s with a `steer` on the un-run node,
    then the sub-plan resumes. Distinguishes lead / sub-a / sub-b by identity marker + task in
    the user message + round (tool-result presence)."""

    CAPTAIN_MARK = "你的子成员"

    def __init__(self, usage: TokenUsage | None = None) -> None:
        self._usage = usage
        self.lead_delegate_calls = 0
        self.lead_replan_calls = 0
        self.sa_calls = 0
        self.sb_calls = 0

    async def stream(self, request):
        system = next((m.content or "" for m in request.messages if m.role == "system"), "")
        user = next((m.content or "" for m in request.messages if m.role == "user"), "")
        # See _LeadBindReplanProvider: depth-1 only (MAX=3).
        is_lead = "你的子成员仍可再向下委派一层" in user or "你的子成员仍可再向下委派一层" in system
        tool_msgs = [m for m in request.messages if m.role == "tool"]
        last_tool = (tool_msgs[-1].content or "") if tool_msgs else ""
        if is_lead and not tool_msgs:
            self.lead_delegate_calls += 1
            args = json.dumps(
                {
                    "tasks": [
                        {"id": "sa", "role": "子研究员", "task": "子调研真实需求"},
                        {"id": "sb", "role": "子写手", "task": "撰写子报告", "depends_on": ["sa"]},
                    ]
                }
            )
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(index=0, id="ls1", function_name="delegate", arguments_delta=args)
                ]
            )
        elif is_lead and "计划已让出" in last_tool and self.lead_replan_calls == 0:
            # the lead caught the SCOPE brief → steer the un-run downstream (待跑) per the deviation
            self.lead_replan_calls += 1
            pending_id = re.search(r"待跑：.*?`([^`]+)`", last_tool).group(1)
            args = json.dumps({"tell": [{"run_id": pending_id, "note": "按真实需求X改写法"}]})
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(index=0, id="ls2", function_name="replan", arguments_delta=args)
                ]
            )
        elif is_lead:
            yield LLMChunk(delta_content="LEAD_FINAL")
        elif "子调研真实需求" in user and not tool_msgs:
            # sub-worker sa, first round: report a scope deviation (kind=scope), non-blocking
            self.sa_calls += 1
            args = json.dumps(
                {"question": "真问题是X不是Y", "assumption": "暂按X继续", "reason": "adjust"}
            )
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(index=0, id="esc1", function_name="escalate", arguments_delta=args)
                ]
            )
        elif "子调研真实需求" in user:
            self.sa_calls += 1
            yield LLMChunk(delta_content=_upstream_body("SA_OUT"))
        else:
            self.sb_calls += 1
            yield LLMChunk(delta_content=_upstream_body("SB_OUT"))
        if self._usage is not None:
            yield LLMChunk(usage=self._usage)


async def test_lead_resteers_subplan_on_subworker_scope_deviation_end_to_end():
    # 受监督子计划 B SCOPE 臂端到端 (docs/03-AI核心/编排器与CEO主Agent.md §2.4): a LEAD's sub-worker
    # reports a 职责偏离 (`escalate kind=scope`) with an un-run downstream → the sub-plan YIELDs a
    # SCOPE「计划已让出」brief → the lead catches it and `replan`s a `steer` on the un-run node,
    # then the sub-plan resumes to completion. Pins the bottom-up arm of the lead's 断头路 closed.
    usage = TokenUsage(input_tokens=10, output_tokens=5)
    provider = _LeadScopeSteerProvider(usage=usage)
    t = nesting_tool(provider, EventSink())
    t._tools.register(EscalateTool())  # the sub-worker needs escalate to raise a scope deviation

    result = await t.execute(
        {"tasks": [{"role": "队长", "task": "主任务"}], "coordinate": False}, ctx()
    )

    assert result.success is True
    assert "LEAD_FINAL" in result.output
    # the lead caught the SCOPE brief and re-steered its OWN un-run downstream (断头路被堵, 自底向上)
    assert provider.lead_replan_calls == 1
    assert provider.sb_calls == 1  # the downstream sb ran AFTER the lead's steer, never stranded
    # 账目不漏: lead (3) + sa (escalate round + deliver round) + sb = 6 LLM calls × 10, all folded
    assert t.usage["input"] == 60
    assert len(t.run_ledger) == 3


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
    """§十一 方案①: a delegate call's COMPLETED workers' NEW web sources ride the ToolResult.

    That is what lets the CEO-path engine number them into the turn's source cards and fold
    ``[n]=url`` back so the CEO can cite a worker-found 法条 by a card-aligned ``[n]`` (Gap A).
    Each call carries only its deduped delta; the accumulator keeps the full set for the
    idempotent turn-close backstop merge, so card numbering stays stable across calls.
    """
    a = {"url": "https://a.com", "title": "A"}
    b = {"url": "https://b.com", "title": "B"}
    seq = iter([[a], [a, b]])

    async def _exec(spec, completed):  # noqa: ANN001 — matches build_agent_executor's product
        return RunState(phase=RunPhase.COMPLETED, content="X", citations=next(seq))

    monkeypatch.setattr("agentcore.runtime.runs.build_agent_executor", lambda **kw: _exec)
    t = tool(Provider([]))

    # 阻塞臂：默认协调下同回合二次合入会提前返回，citations 尚未挂上 ToolResult。
    r1 = await t.execute(
        {"tasks": [{"role": "核验", "task": "查法条A"}], "coordinate": False}, ctx()
    )
    r2 = await t.execute(
        {"tasks": [{"role": "核验", "task": "查法条B"}], "coordinate": False}, ctx()
    )

    assert [c["url"] for c in (r1.citations or [])] == ["https://a.com"]
    # second call contributes ONLY the new source — a is deduped against the turn accumulator
    assert [c["url"] for c in (r2.citations or [])] == ["https://b.com"]
    # accumulator still holds the full deduped set for the turn-close backstop merge
    assert [c["url"] for c in t.citations] == ["https://a.com", "https://b.com"]


async def test_worker_captain_rejects_sub_fanout_over_cap():
    t = DelegateTool(
        llm=Provider([]),
        sink=EventSink(),
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx(),
        captain_run_id="cap_1",
        depth=1,
        folder_id="test_birth",
        approval_gate=None,
    )
    tasks = [{"role": f"子{i}", "task": f"任务{i}"} for i in range(5)]
    result = await t.execute({"tasks": tasks}, ctx())
    assert result.success is False
    assert "子团队扇出已达上限" in (result.error or "")
    assert "4" in (result.error or "")


async def test_worker_captain_rejects_cumulative_sub_fanout():
    t = DelegateTool(
        llm=Provider([]),
        sink=EventSink(),
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx(),
        captain_run_id="cap_1",
        depth=1,
        folder_id="test_birth",
        approval_gate=None,
    )
    t._sub_workers_spawned = 4
    result = await t.execute({"tasks": [{"role": "子5", "task": "收尾"}]}, ctx())
    assert result.success is False
    assert "子团队扇出已达上限" in (result.error or "")
