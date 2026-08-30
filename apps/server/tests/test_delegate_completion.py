"""Unit tests for delegate completion soft checks (S3: no criteria kind)."""

from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.delegate.completion import (
    collect_completion_soft_notes,
    collect_worker_gaps,
    format_worker_gaps_block,
    plan_suggests_code_verification,
)
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState


def _run(*, files: list[str] | None = None, transcript: list[LLMMessage] | None = None):
    return RunState(
        phase=RunPhase.COMPLETED,
        content="done",
        files_touched=files or [],
        transcript=transcript or [],
    )


def test_omitted_criteria_yields_no_soft_notes():
    soft = collect_completion_soft_notes({"a": _run()})
    assert soft == []


def test_soft_overlay_typescript_without_verify():
    soft = collect_completion_soft_notes({"a": _run(files=["src/App.tsx"])})
    assert any("不阻断验收" in n and ".ts" in n for n in soft)


def test_soft_overlay_skipped_when_test_run_passes():
    transcript = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="1",
                    type="function",
                    function=ToolCallFunction(name="test_run", arguments='{"check":"test"}'),
                )
            ],
        ),
        LLMMessage(role="tool", tool_call_id="1", content="## 验证结果：通过\n通过：1"),
    ]
    soft = collect_completion_soft_notes(
        {"a": _run(files=["src/App.tsx"], transcript=transcript)}
    )
    assert not any("建议补一次验证" in n for n in soft)


def test_plan_suggests_code_verification():
    plan = RunPlan(
        nodes=[
            RunSpec(
                run_id="a",
                role="dev",
                task="跑通测试并修好",
            )
        ]
    )
    assert plan_suggests_code_verification(plan)


def test_plan_suggests_code_verification_skips_bare_open():
    """裸「打开文件 / 打开 .mdc」不得命中 plan_suggests_code_verification。"""
    for task in ("打开文件", "打开 `.cursor/rules/x.mdc`"):
        plan = RunPlan(
            nodes=[RunSpec(run_id="a", role="dev", task=task)]
        )
        assert not plan_suggests_code_verification(plan)


def test_plan_suggests_code_verification_open_acceptance():
    """「打开验收」仍经「验收」命中。"""
    plan = RunPlan(
        nodes=[RunSpec(run_id="a", role="dev", task="打开验收")]
    )
    assert plan_suggests_code_verification(plan)


async def test_cold_start_pending_allows_single_worker_delegate():
    """pending ∧ 1 worker：不再因节点数 contract_failure（组队靠提示词）。"""
    from tests.delegate.conftest import Provider, ctx, tool

    t = tool(Provider(["摸仓笔记"]))
    t._base_tool_context.cold_start_explore_pending = True
    result = await t.execute(
        {
            "tasks": [{"role": "调研", "task": "摸仓"}],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert result.contract_failure is not True
    err = result.error or ""
    assert "≥2" not in err
    assert "包办" not in err


def test_format_worker_gaps_block_empty():
    assert format_worker_gaps_block([]) == ""


_HARD_CLOSING = (
    "**【终稿诚实性·部分交付】**上方契约缺口非空：终稿必须使用「部分交付 / 尚未齐备」"
    "类措辞，点明未闭合缺口与建议下一步；"
    "【禁止】写「完整交付 / 全部完成 / 可运行无缺 / 无需审计 / 团队已交付完毕」等完成度断言。"
)


def test_format_soft_only_unverified_note_bans_completeness_not_partial():
    """仅软缺口：禁完成度断言，不强制「部分交付 / 尚未齐备」。"""
    gaps = [
        (
            "数据处理员",
            [
                {
                    "description": (
                        "含示例/虚构自注（1 处）：`structure.md` · 虚构/示意 · 「虚构演示账单」。"
                    ),
                    "reason": "unverified_note",
                    "severity": "warning",
                }
            ],
        )
    ]
    block = format_worker_gaps_block(gaps)
    assert "【禁止】" in block
    assert "完整交付" in block
    assert "全部完成" in block
    assert "可运行无缺" in block
    assert "部分交付" not in block
    assert "尚未齐备" not in block
    assert "终稿必须使用" not in block
    assert _HARD_CLOSING not in block


def test_format_soft_only_dogfood_601863c9_unverified_note_shape():
    """dogfood cid 601863c9 同形：仅 unverified_note，缺 .xlsx 不得改口成未交付。"""
    gaps = [
        (
            "数据处理员",
            [
                {
                    "description": (
                        "含示例/虚构自注（2 处）："
                        "`synthetic_bill_structure.md` · 虚构/示意 · 「虚构演示账单」。"
                    ),
                    "reason": "unverified_note",
                    "severity": "warning",
                }
            ],
        )
    ]
    block = format_worker_gaps_block(gaps)
    assert "部分交付" not in block
    assert "尚未齐备" not in block
    assert "完整交付" in block


def test_format_soft_only_dogfood_9628a2f7_unverified_note_shape():
    """dogfood cid 9628a2f7 同形：仅 unverified_note，诚实终稿不得被改口成部分交付。"""
    gaps = [
        (
            "数据处理员",
            [
                {
                    "description": (
                        "含示例/虚构自注（1 处）：`build_excel.py` 旁报告 · 示例数据 · 「示例行」。"
                    ),
                    "reason": "unverified_note",
                    "severity": "warning",
                }
            ],
        )
    ]
    block = format_worker_gaps_block(gaps)
    assert "部分交付" not in block
    assert "尚未齐备" not in block
    assert "【禁止】" in block
    assert "完整交付" in block


def test_format_hard_gap_keeps_partial_delivery_wording():
    """有硬缺口：收口指令与原先逐字一致。"""
    gaps = [
        (
            "研究员",
            [
                {
                    "description": "队员因 token 预算触顶被迫收口，产出可能不完整",
                    "reason": "token_budget",
                }
            ],
        )
    ]
    block = format_worker_gaps_block(gaps)
    assert _HARD_CLOSING in block
    assert "不必逐条复述" in block


def test_collect_worker_gaps_empty_when_clean():
    plan = RunPlan(
        nodes=[RunSpec(run_id="a", role="dev", task="写")]
    )
    assert collect_worker_gaps(plan, {"a": _run(files=["a.py"])}) == []
