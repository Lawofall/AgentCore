"""成篇质量定案：research_quality 谓词、空 handoff、审计硬门、检索空 streak."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agentcore.runtime.graph.admit.batch_shape import annotate_batch_meta
from agentcore.runtime.graph.drive.delivery_status import build_delivery_status
from agentcore.runtime.loop_controller import LoopController
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.research_quality import (
    academic_usable_citation_count,
    brief_may_satisfy_body_floor,
    collect_evidence_deficit_gaps,
    has_landed_prose_artifact,
    is_academic_usable_url,
    literature_evidence_deficit_hit,
    plan_is_literature_report_delivery,
    plan_signals_long_form_audit,
    promote_brief_to_deliverable,
    upstream_body_floor_satisfied,
)
from agentcore.runtime.runs.types import Deliverable, RunPhase, RunSpec, RunState
from agentcore.tools.builtin.file_ops import FileWriteTool
from agentcore.tools.builtin.handoff import HandoffTool
from agentcore.tools.protocol import RetrievalBudgetState, ToolContext, ToolResult
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

_PROSE_BODY = "# 报告\n\n" + ("这是实质正文段落。" * 50)
_SKELETON_BODY = "# 报告\n\n## 一\n\n## 二\n\n<!-- OUTLINE -->\n"


def test_cabinet_path_no_longer_marks_report_delivery():
    """``reviews/`` / ``research/`` 路径不再抬 report_delivery 闸。"""
    assert plan_is_literature_report_delivery(
        [Deliverable(artifacts=["AgentCore/文档/reviews/审校.md"])]
    ) is False


def _ctx(tmp_path: Path, **kwargs) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox()),
        user_id="u",
        **kwargs)


def test_long_form_audit_does_not_scan_task_free_text():
    """成篇硬审计不扫自由文。"""
    assert not plan_signals_long_form_audit(
        [
            {
                "role": "撰稿",
                "task": "写一篇起诉第三者立案实务研究报告，约 5000–8000 字",
            }
        ]
    )
    assert not plan_signals_long_form_audit(
        [
            {
                "role": "撰稿",
                "task": "随便写点",
            }
        ]
    )


def test_paper_parallel_merge_discipline_constant():
    from agentcore.runtime.runs.research_quality import (
        PAPER_PARALLEL_MERGE_DISCIPLINE,
        research_report_main_artifact,
    )

    assert "单主文件" in PAPER_PARALLEL_MERGE_DISCIPLINE
    assert "合并责任" in PAPER_PARALLEL_MERGE_DISCIPLINE
    assert "建站" in PAPER_PARALLEL_MERGE_DISCIPLINE  # 明示不误伤多产物
    assert research_report_main_artifact(None) == ""
    assert research_report_main_artifact("paper/thesis.md") == "paper/thesis.md"
    assert research_report_main_artifact("\\drafts\\a.md") == "drafts/a.md"


def test_research_handwritten_ok_without_named_pipeline():
    """调研意图手写 tasks：不再强推成文审校座。"""
    assert plan_is_literature_report_delivery(
        [{"role": "调研员", "task": "写实务研究报告"}]
    ) is False


def test_annotate_batch_meta_audit_flags():
    result = ToolResult(tool_call_id="", success=True, output="ok")
    stamped = annotate_batch_meta(
        result,
        node_count=5,
        has_deps=True,
        audit_hard=True,
        includes_review=True)
    assert "batch_playbook" not in stamped.metadata
    assert stamped.metadata["audit_hard"] is True
    assert stamped.metadata["batch_includes_review"] is True


def test_parallel_brief_does_not_signal_long_form_audit():
    """摸底批：硬门只认 reviews/ 结构，不因多人进门。"""
    from agentcore.runtime.runs.research_quality import plan_signals_long_form_audit

    tasks = [
        {"role": "方向专员", "task": "摸底兼容"},
        {"role": "方向专员", "task": "摸底闭源风险"},
        {"role": "方向专员", "task": "摸底生态"},
    ]
    assert plan_signals_long_form_audit(tasks) is False


def test_audit_hard_block_after_soft_nudge():
    """Engine no longer hard-blocks wrap-up after a review-shaped batch; stamps remain."""
    from agentcore.runtime.engine import governance as gov
    from agentcore.runtime.loop_controller import LoopController

    assert not hasattr(gov, "maybe_inject_audit_hard_block")
    c = LoopController()
    c.mark_post_delegate(node_count=5, has_deps=True, audit_hard=True)
    assert c.audit_hard_required is True


def test_research_report_includes_review_skips_hard_block():
    c = LoopController()
    c.mark_post_delegate(
        node_count=5, has_deps=True, audit_hard=True, includes_review=True
    )
    assert c.audit_includes_review is True
    assert c.audit_hard_required is True


def test_upstream_body_floor_predicate():
    # No contract floor: non-empty body OK; empty blocked unless prose landed.
    assert upstream_body_floor_satisfied(
        body_chars=1, landed_artifact_kinds={}, min_body_chars=0
    )
    assert not upstream_body_floor_satisfied(
        body_chars=0, landed_artifact_kinds={}, min_body_chars=0
    )
    # Optional internal floor (not a live CEO knob).
    assert upstream_body_floor_satisfied(
        body_chars=80,
        landed_artifact_kinds={},
        min_body_chars=80)
    assert not upstream_body_floor_satisfied(
        body_chars=10, landed_artifact_kinds={}, min_body_chars=80
    )
    assert not upstream_body_floor_satisfied(
        body_chars=0, landed_artifact_kinds={"a.md": "skeleton"}, min_body_chars=80
    )
    assert has_landed_prose_artifact({"a.md": "prose"})
    assert upstream_body_floor_satisfied(
        body_chars=0, landed_artifact_kinds={"a.md": "prose"}, min_body_chars=80
    )


def test_brief_may_satisfy_body_floor():
    """钉路径才允许便条升格正文地板；省略不催写。"""
    assert not brief_may_satisfy_body_floor(expects_landing=False)
    assert brief_may_satisfy_body_floor(expects_landing=True)


def test_promote_brief_to_deliverable():
    assert promote_brief_to_deliverable("") == ""
    assert promote_brief_to_deliverable("  ") == ""
    assert promote_brief_to_deliverable("核心结论") == "核心结论"
    body = promote_brief_to_deliverable("核心结论", ["要点甲", "要点乙"])
    assert body.startswith("核心结论")
    assert "- 要点甲" in body
    assert "- 要点乙" in body
    # Empty / blank points ignored; lone string tolerated.
    assert promote_brief_to_deliverable("结论", ["", "  "]) == "结论"
    assert promote_brief_to_deliverable("结论", "单条要点") == "结论\n\n- 单条要点"


@pytest.mark.asyncio
async def test_handoff_execute_ignores_arguments_and_does_not_promote(tmp_path: Path):
    """参数已清空：execute 不升格、final_text 恒空。升格改由 harvest 后的 terminal 做。"""
    from agentcore.tools.builtin.handoff import HANDOFF_RECEIPT

    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        handoff_expects_landing=False,
        round_content_chars=0)
    result = await HandoffTool().execute(
        {"summary": "Greeter 问好", "key_points": ["已完成打招呼"]},
        ctx)
    assert result.success is True
    assert (result.final_text or "") == ""
    assert result.output == HANDOFF_RECEIPT


@pytest.mark.asyncio
async def test_handoff_allows_brief_for_prose_with_dependents(tmp_path: Path):
    """有下游 prose：body=0 + 仅 summary → 仍交接（空交不再硬拒；prose 不升格 summary）。"""
    summary = "诊断结论：" + ("根因分析充分。" * 20)
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        handoff_expects_landing=False,
        round_content_chars=0)
    result = await HandoffTool().execute({"summary": summary}, ctx)
    assert result.success is True
    assert (result.final_text or "") == ""


@pytest.mark.asyncio
async def test_handoff_promotes_short_brief_when_below_floor(tmp_path: Path):
    """非 prose：地板>0 且升格仍短 → 仍交接（升格短文，不拒）。"""
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        handoff_expects_landing=False,
        round_content_chars=0)
    result = await HandoffTool().execute({"summary": "太短"}, ctx)
    assert result.success is True
    assert (result.final_text or "") == ""


@pytest.mark.asyncio
async def test_handoff_promotes_brief_when_meets_floor_non_prose(tmp_path: Path):
    """非 prose + 有下游：地板>0 但升格正文够长 → 仍可升格。"""
    summary = "调研结论：" + ("要点充分。" * 20)
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        handoff_expects_landing=True,
        round_content_chars=0)
    result = await HandoffTool().execute({"summary": summary}, ctx)
    assert result.success is True
    assert (result.final_text or "") == ""


@pytest.mark.asyncio
async def test_handoff_prose_allows_when_real_body_meets_floor(tmp_path: Path):
    """有下游 prose：真正文够长 → 放行（不依赖 summary 升格）。"""
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        handoff_expects_landing=False,
        round_content_chars=85)
    result = await HandoffTool().execute({"summary": "诊断已写入正文"}, ctx)
    assert result.success is True
    assert (result.final_text or "") == ""


@pytest.mark.asyncio
async def test_handoff_allows_empty_body_when_required(tmp_path: Path):
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        round_content_chars=10)
    result = await HandoffTool().execute({"summary": "结论够长" * 10}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_handoff_allows_empty_summary_when_body_zero(tmp_path: Path):
    """空 summary + 正文 0 → 仍交接。"""
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        round_content_chars=0)
    result = await HandoffTool().execute({"summary": "   "}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_handoff_real_body_keeps_empty_final_text(tmp_path: Path):
    """有真实正文时 final_text 仍为空串（不升格覆盖）。"""
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        round_content_chars=19)
    result = await HandoffTool().execute({"summary": "Greeter 问好"}, ctx)
    assert result.success is True
    assert (result.final_text or "") == ""


def test_handoff_schema_has_no_brief_fields():
    """便条在收尾轮正文，参数表为空。"""
    schema = HandoffTool().schema.parameters
    assert schema.get("properties") == {}
    assert "required" in schema
    assert schema.get("required") == []


@pytest.mark.asyncio
async def test_handoff_allows_short_body_when_no_contract_floor(tmp_path: Path):
    """有下游也不挡「一句话」级短正文。"""
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        round_content_chars=19)
    result = await HandoffTool().execute({"summary": "Greeter 问好"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_handoff_allows_empty_body_when_prose_landed(tmp_path: Path):
    """有下游 prose：body=0 但已落盘 prose artifact → 仍放行（summary 升格关闭后仍豁免）。"""
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        handoff_expects_landing=False,
        round_content_chars=0,
        landed_artifact_kinds={"notes.md": "prose"})
    result = await HandoffTool().execute({"summary": "已落盘调研"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_handoff_allows_empty_body_when_only_skeleton_landed(tmp_path: Path):
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        round_content_chars=0,
        landed_artifact_kinds={"outline.md": "skeleton"},
        has_landed_files=True,
    )
    result = await HandoffTool().execute({"summary": "骨架已落盘"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_handoff_prose_landed_survives_replace_empty_body(tmp_path: Path):
    """生产路径：多轮 replace + write 置位后，下一轮空 body handoff 应成功。"""
    assert len(_PROSE_BODY) >= 400
    base = _ctx(
        tmp_path,
        handoff_requires_body=True,
)
    # tool_round stamps round_content_chars; tool_exec replace()s again per call.
    write_round = replace(base, round_content_chars=12)
    write_ctx = replace(write_round)
    written = await FileWriteTool().execute(
        {"file_path": "miro-research.md", "content": _PROSE_BODY}, write_ctx
    )
    assert written.success is True
    assert write_ctx.landed_artifact_kinds.get("miro-research.md") == "prose"
    # Shared dict survives; bool on replace-copy does not propagate to base.
    assert base.landed_artifact_kinds.get("miro-research.md") == "prose"
    assert base.has_landed_files is False
    handoff_ctx = replace(base, round_content_chars=0)
    assert handoff_ctx.has_landed_files is False
    result = await HandoffTool().execute({"summary": "Miro 调研已落盘"}, handoff_ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_handoff_skeleton_write_after_replace_still_handoffs(tmp_path: Path):
    base = _ctx(
        tmp_path,
        handoff_requires_body=True,
)
    write_ctx = replace(replace(base, round_content_chars=0))
    written = await FileWriteTool().execute(
        {"file_path": "outline.md", "content": _SKELETON_BODY}, write_ctx
    )
    assert written.success is True
    assert base.landed_artifact_kinds.get("outline.md") == "skeleton"
    handoff_ctx = replace(base, round_content_chars=0)
    result = await HandoffTool().execute({"summary": "提纲已落盘"}, handoff_ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_handoff_allows_sufficient_body(tmp_path: Path):
    ctx = _ctx(
        tmp_path,
        handoff_requires_body=True,
        round_content_chars=85)
    result = await HandoffTool().execute({"summary": "调研要点已齐"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_write_marks_landed_files(tmp_path: Path):
    ctx = _ctx(tmp_path)
    assert ctx.has_landed_files is False
    result = await FileWriteTool().execute(
        {"file_path": "a.md", "content": "hello"}, ctx
    )
    assert result.success is True
    assert ctx.has_landed_files is True


def test_delivery_status_no_continue_writing_action():
    """成篇未写完：标 partial + 成篇未写完摘要，不再挂 continue_writing 按钮。"""
    from agentcore.runtime.runs.file_acceptance import build_file_acceptance

    plan = RunPlan()
    plan.add(
        RunSpec(
            run_id="write",
            role="撰稿人",
            task="写成报告",
            deliverable=Deliverable())
    )
    results = {
        "write": RunState(
            phase=RunPhase.COMPLETED,
            content="",
            files_touched=["report.md"],
            file_acceptance=build_file_acceptance(
                ["report.md"], phase=RunPhase.COMPLETED
            ),
            delivery_gaps=[
                {
                    "description": "队员因 token 预算触顶被迫收口，产出可能不完整",
                    "reason": "token_budget",
                }
            ])
    }
    payload = build_delivery_status(plan, results, execution_id="e1")
    assert payload is not None
    assert payload["state"] == "partial"
    assert "成篇未写完" in payload["summary"]
    kinds = {a.get("kind") for a in payload.get("actions") or []}
    assert "continue_writing" not in kinds


def test_retrieval_empty_streak_helpers():
    budget = RetrievalBudgetState(limit=5)
    assert budget.note_search_empty() == 1
    assert budget.note_search_empty() == 2
    budget.note_search_hit()
    assert budget.consecutive_empty_searches == 0


def test_plan_is_literature_report_delivery_binds_reviews_not_role_name():
    # 同等成文：已声明 reviews/ files 审校座
    assert plan_is_literature_report_delivery(
        [
            {
                "role": "撰稿人",
                "deliverable": {
                    "artifacts": ["AgentCore/文档/research/报告.md"],
                },
            },
            {
                "role": "学术审校员",
                "deliverable": {
                    "artifacts": ["AgentCore/文档/reviews/审校报告.md"],
                },
            },
        ]
    ) is False
    assert plan_is_literature_report_delivery(
        [
            {"role": "方向专员", "task": "摸底兼容"},
            {"role": "方向专员", "task": "摸底生态"},
        ]
    ) is False
    # 仅角色名叫审校、未声明 reviews files → 不进文献降档
    assert plan_is_literature_report_delivery(
        [
            {
                "role": "撰稿人",
                "deliverable": {
                    "artifacts": ["AgentCore/文档/research/报告.md"],
                },
            },
            {"role": "学术审校员", "deliverable": {"name": "审校"}},
        ]
    ) is False


def test_academic_usable_url_and_citation_count():
    assert is_academic_usable_url("https://arxiv.org/abs/2301.1")
    assert is_academic_usable_url("https://doi.org/10.1000/xyz")
    assert not is_academic_usable_url("https://baike.baidu.com/item/x")
    cites = [
        {"url": "https://baike.baidu.com/item/x"},
        {"url": "https://arxiv.org/abs/1"},
        {"url": "https://arxiv.org/abs/1"},  # dedupe
    ]
    assert academic_usable_citation_count(cites) == 1


def test_collect_evidence_deficit_gaps_combinable_triggers():
    from agentcore.runtime.runs.types import Deliverable, RunPhase, RunState

    nodes = [
        RunSpec(
            run_id="write",
            role="撰稿人",
            task="写",
            deliverable=Deliverable(
                artifacts=["notes/报告.md"]),
        ),
        RunSpec(
            run_id="review",
            role="学术审校员",
            task="审",
            deliverable=Deliverable(
                artifacts=["notes/审校报告.md"])),
    ]
    # Adequate → no gap
    ok = {
        "write": RunState(
            phase=RunPhase.COMPLETED,
            content="成稿",
            citations=[
                {"url": "https://arxiv.org/abs/1"},
                {"url": "https://pubmed.ncbi.nlm.nih.gov/1/"},
            ]),
        "review": RunState(phase=RunPhase.COMPLETED, content="可接受"),
    }
    assert collect_evidence_deficit_gaps(nodes, ok) == []
    hit, bits = literature_evidence_deficit_hit(nodes, ok)
    assert hit is False
    assert bits == []

    junk = {
        "write": RunState(
            phase=RunPhase.COMPLETED,
            content="成稿",
            citations=[
                {"url": "https://baike.baidu.com/a"},
                {"url": "https://www.iciba.com/b"},
            ]),
        "review": RunState(phase=RunPhase.COMPLETED, content="ok"),
    }
    assert collect_evidence_deficit_gaps(nodes, junk) == []

    stamped_writer = RunState(
        phase=RunPhase.COMPLETED,
        content="成稿",
        citations=[{"url": "https://arxiv.org/abs/1"}])
    stamped_writer.evidence_meta = {
        "evidence_gap": True,
        "search_policy": "academic_literature",
    }
    stamped = {
        "write": stamped_writer,
        "review": RunState(phase=RunPhase.COMPLETED, content="ok"),
    }
    assert collect_evidence_deficit_gaps(nodes, stamped) == []
    assert literature_evidence_deficit_hit(nodes, stamped) == (False, [])


def test_stamp_retrieval_evidence_gap_copies_sticky_budget(tmp_path: Path):
    """Executor helper: RetrievalBudgetState.evidence_gap → RunState readable fields."""
    from agentcore.runtime.runs.executor.node import _stamp_retrieval_evidence_gap

    budget = RetrievalBudgetState(limit=3)
    budget.note_evidence_gap()
    ctx = _ctx(tmp_path, retrieval_budget=budget, search_policy="academic_literature")
    state = RunState(phase=RunPhase.COMPLETED, content="ok")
    out = _stamp_retrieval_evidence_gap(
        state, ctx, search_policy="academic_literature"
    )
    assert out.evidence_gap is True
    assert out.evidence_meta == {
        "evidence_gap": True,
        "search_policy": "academic_literature",
    }
    # No-op when sticky unset
    clean = RunState(phase=RunPhase.COMPLETED, content="ok")
    ctx2 = replace(ctx, retrieval_budget=RetrievalBudgetState(limit=1))
    out2 = _stamp_retrieval_evidence_gap(clean, ctx2, search_policy="academic_literature")
    assert getattr(out2, "evidence_gap", False) is False
    assert getattr(out2, "evidence_meta", None) is None


def test_transcript_web_search_evidence_gap_triggers_deficit():
    """web_search tool JSON with evidence_gap no longer keys a reviews/ literature gate."""
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction

    nodes = [
        RunSpec(
            run_id="write",
            role="撰稿人",
            task="写",
            deliverable=Deliverable(
                artifacts=["notes/报告.md"]),
        ),
        RunSpec(
            run_id="review",
            role="学术审校员",
            task="审",
            deliverable=Deliverable(
                artifacts=["notes/审校报告.md"])),
    ]
    transcript = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="tc1",
                    function=ToolCallFunction(name="web_search", arguments="{}"))
            ]),
        LLMMessage(
            role="tool",
            content=(
                '{"query":"q","results":[],"evidence_gap":true,'
                '"search_policy":"academic_literature"}'
            ),
            tool_call_id="tc1"),
    ]
    writer = RunState(
        phase=RunPhase.COMPLETED,
        content="成稿",
        citations=[{"url": "https://arxiv.org/abs/1"}],
        transcript=transcript)
    assert collect_evidence_deficit_gaps(
        nodes,
        {"write": writer, "review": RunState(phase=RunPhase.COMPLETED, content="ok")},
    ) == []


def test_named_review_without_files_not_elevated():
    """名叫审校但未声明 files 不再被抬契约。"""
    from agentcore.runtime.runs.builder import build_run_plan

    plan, errors = build_run_plan(
        [
            {
                "id": "fix",
                "role": "修补员",
                "task": "改炮塔购买",
                "deliverable": {"artifacts": ["src/a.py"]},
            },
            {
                "id": "review",
                "role": "独立复核员",
                "task": "只读复核本轮改动",
                "depends_on": ["fix"],
            },
            {
                "id": "verify",
                "role": "验证员",
                "task": "跑测试",
                "depends_on": ["fix"],
            },
        ],
        id_prefix="thin_review")
    assert errors == []
    by_role = {n.role: n for n in plan.nodes}
    review = by_role["独立复核员"]
    assert review.deliverable is not None
    assert review.deliverable.artifacts == []

    verify = by_role["验证员"]
    assert verify.deliverable is not None
    assert verify.deliverable.artifacts == []
