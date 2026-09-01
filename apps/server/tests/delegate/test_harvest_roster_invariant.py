"""The harvest window must always carry the roster, whatever the prose costs.

Regression: worker prose and the roster once shared a 4000-char packaging
shaper on the coordination terminal payload, so big batches silently shipped a
harvest turn whose closing discipline ordered the CEO to reconcile against a
roster that had been truncated away (observed in production on a 2026-08-16
harvest). Bodies now share ``CEO_SYNTHESIS_BUDGET``; the package is composed
once; ToolResult and ``ALL_COMPLETED.output`` are the same string.
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.delegate.ceo_format import build_ceo_synthesis, format_for_ceo
from agentcore.runtime.delegate.drive_terminal import post_session_all_completed
from agentcore.runtime.delegate.terminal_output import ALL_COMPLETED_OUTPUT_LIMIT
from agentcore.runtime.runs.constants import CEO_SYNTHESIS_BUDGET
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from tests.delegate.conftest import Provider, tool

ROSTER_HEADING = "### 队员终态名册"
ROSTER_COUNTERS = "计划节点：完成"
CLOSING_DISCIPLINE = "【终稿纪律】"


class _Session:
    """Minimal coordination-session stand-in for the terminal post."""

    def __init__(self) -> None:
        self.execution_id = "exec-roster"
        self.completed_run_ids: set[str] = set()
        self.total_workers = 0
        self.harvest_user_facts: dict[str, Any] | None = None
        self.events: list[Any] = []

    def post(self, event: Any) -> None:
        self.events.append(event)

    @property
    def payload(self) -> dict[str, Any]:
        assert self.events, "terminal event was never posted"
        return self.events[-1].payload


def _bulky_plan_and_results(body_chars: int) -> tuple[RunPlan, dict[str, RunState]]:
    """Workers whose surviving prose blows past ``CEO_SYNTHESIS_BUDGET``.

    A failed worker's body is digested to one line, so the bulk has to come from
    completed ones; the failed node is here to make the roster non-trivial.
    """
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="w1", task="写长篇调研", role="调研员"),
            RunSpec(run_id="w2", task="写长篇实现", role="工程师"),
            RunSpec(run_id="w3", task="跑验收", role="验收员"),
        ]
    )
    results = {
        "w1": RunState(phase=RunPhase.COMPLETED, content="调研正文。" * body_chars),
        "w2": RunState(phase=RunPhase.COMPLETED, content="实现正文。" * body_chars),
        "w3": RunState(phase=RunPhase.FAILED, content="验收半成品。", error="编译失败"),
    }
    return plan, results


def test_bulky_batch_keeps_roster_in_terminal_payload():
    """raw ≥ CEO_SYNTHESIS_BUDGET is exactly the档 that used to lose the roster."""
    t = tool(Provider([]))
    plan, results = _bulky_plan_and_results(1500)
    raw_chars = sum(len(s.content) for s in results.values() if s and s.content)
    assert raw_chars >= CEO_SYNTHESIS_BUDGET

    synthesis = build_ceo_synthesis(t, plan, results)
    # Packaging (roster + closing) still pushes the assembled package past the
    # old 4000 shaper; that is no longer a second scrap-cut. Posting the
    # canonical text must be identity with ToolResult.
    assert len(synthesis.text) > 4000
    assert len(synthesis.text) <= ALL_COMPLETED_OUTPUT_LIMIT

    session = _Session()
    post_session_all_completed(
        session,
        output=synthesis.text,
        roster_facts=synthesis.roster_facts,
        completed=1,
        total=2,
    )

    posted = session.payload["output"]
    assert posted == synthesis.text
    assert ROSTER_HEADING in posted
    assert ROSTER_COUNTERS in posted
    assert CLOSING_DISCIPLINE in posted
    assert "失败" in posted and "w3" in posted
    assert len(posted) <= ALL_COMPLETED_OUTPUT_LIMIT
    assert len(posted) > len(synthesis.roster_text)
    assert posted.index(ROSTER_HEADING) < posted.index(CLOSING_DISCIPLINE)

    via_parts = _Session()
    post_session_all_completed(
        via_parts,
        output=synthesis.prose,
        roster_text=synthesis.roster_text,
        roster_facts=synthesis.roster_facts,
        closing_text=synthesis.closing_text,
        completed=1,
        total=2,
    )
    assert via_parts.payload["output"] == synthesis.text


def test_roster_survives_a_budget_smaller_than_the_prose():
    """Even a hostile budget cannot trade the roster away for worker prose."""
    session = _Session()
    post_session_all_completed(
        session,
        output="队员正文。" * 4000,
        roster_text="\n### 队员终态名册（地面真相）\n计划节点：完成 1 · 失败 1 · 跳过 0 · 取消 0。",
        roster_facts={"completed": 1, "failed": 1},
        closing_text="\n---\n【终稿纪律】失败必须写入，禁止编造「全部交付」。",
        output_limit=1500,
        completed=1,
        total=2,
    )

    posted = session.payload["output"]
    assert ROSTER_HEADING in posted
    assert ROSTER_COUNTERS in posted
    assert CLOSING_DISCIPLINE in posted
    assert posted.rstrip().endswith("禁止编造「全部交付」。")
    assert "队员正文。" in posted
    assert len(posted) <= 1500
    assert "系统视图截断" in posted or "已省略" in posted or "…" in posted


def test_huge_roster_keeps_counters_and_stays_within_limit():
    """A 400-line failure list must not push the payload past the ceiling."""
    session = _Session()
    post_session_all_completed(
        session,
        output="队员正文。" * 2000,
        roster_text="\n### 队员终态名册\n计划节点：完成 0 · 失败 40。\n" + ("- 失败节点\n" * 400),
        closing_text="\n---\n【终稿纪律】禁止编造「全部交付」。",
        output_limit=1000,
        completed=0,
        total=40,
    )

    posted = session.payload["output"]
    assert ROSTER_HEADING in posted
    assert ROSTER_COUNTERS in posted or "计划节点：完成 0 · 失败 40" in posted
    assert "失败节点" in posted
    assert len(posted) <= 1000
    assert "系统视图截断" in posted or "…" in posted


def test_roster_counters_ride_structured_user_facts():
    t = tool(Provider([]))
    plan, results = _bulky_plan_and_results(1500)
    synthesis = build_ceo_synthesis(t, plan, results)
    session = _Session()
    post_session_all_completed(
        session,
        output=synthesis.prose,
        roster_text=synthesis.roster_text,
        roster_facts=synthesis.roster_facts,
        closing_text=synthesis.closing_text,
        user_facts={"nodes": [], "files": [], "outstanding_tool_failures": []},
    )

    roster = session.payload["user_facts"]["roster"]
    assert roster["completed"] == 2
    assert roster["failed"] == 1
    # Stamped on the session too, so the no-LLM fallback reads the same numbers.
    assert session.harvest_user_facts is not None
    assert session.harvest_user_facts["roster"] == roster


def test_ceo_in_turn_text_still_carries_roster_before_closing():
    """The ToolResult read the CEO gets in-turn keeps its original section order."""
    t = tool(Provider([]))
    plan, results = _bulky_plan_and_results(20)
    text = format_for_ceo(t, plan, results)
    assert text.index(ROSTER_HEADING) < text.index(CLOSING_DISCIPLINE)
    assert text.startswith("## 团队执行结果")


def test_synthesis_prose_excludes_the_non_negotiable_parts():
    t = tool(Provider([]))
    plan, results = _bulky_plan_and_results(20)
    synthesis = build_ceo_synthesis(t, plan, results)
    assert ROSTER_HEADING not in synthesis.prose
    assert CLOSING_DISCIPLINE not in synthesis.prose
    assert ROSTER_HEADING in synthesis.roster_text
    assert CLOSING_DISCIPLINE in synthesis.closing_text
