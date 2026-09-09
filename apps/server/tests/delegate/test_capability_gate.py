"""委派前能力软警告（S3：无 code_verified / runtime_ready kind 硬闸）。"""

from __future__ import annotations

from agentcore.runtime.delegate.completion import (
    execution_capability_warning,
    plan_suggests_exec_office_deliverable,
)
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import Deliverable, RunSpec
from tests.delegate.conftest import LocalBackend


class _CloudBackend:
    location = "server"


def _plan(task: str = "写文件", *, artifacts: list[str] | None = None) -> RunPlan:
    deliverable = (
        Deliverable(artifacts=list(artifacts)) if artifacts else None
    )
    return RunPlan(
        nodes=[RunSpec(run_id="a", role="dev", task=task, deliverable=deliverable)]
    )


def test_soft_warning_on_run_text_without_execution_class():
    plan = _plan("运行脚本生成可播放 pptx")
    warn = execution_capability_warning(plan, _CloudBackend())
    assert warn is not None
    assert "未装配" in warn or "能力提示" in warn


def test_soft_warning_silent_on_local_with_execution():
    # LocalBackend is treated as execution-capable in unit env.
    plan = _plan("运行 pytest")
    warn = execution_capability_warning(plan, LocalBackend())
    # May be None when execution class enabled; never blocks.
    assert warn is None or warn.startswith("[能力提示]")


def test_soft_warning_office_without_execution():
    plan = _plan("生成演示文稿.pptx")
    warn = execution_capability_warning(plan, _CloudBackend())
    assert warn is not None
    assert "Office" in warn or "docx" in warn or "pptx" in warn
    assert "禁止" in warn or "不要" in warn
    # Already on cloud: must not prescribe re-import as the fix.
    assert "推荐**引导 Composer「导入到云" not in warn
    assert "**推荐**引导 Composer「导入到云" not in warn


def test_exec_office_predicate_splits_by_deterministic_exporter():
    """粒度按真实能力：docx/pdf 有 md_to_docx / md_to_pdf，不算「需执行」Office。"""
    assert plan_suggests_exec_office_deliverable(_plan("产出 课件.pptx")) is True
    assert plan_suggests_exec_office_deliverable(_plan("产出 台账.xlsx")) is True
    assert (
        plan_suggests_exec_office_deliverable(_plan("写文件", artifacts=["deck.pptx"]))
        is True
    )
    assert plan_suggests_exec_office_deliverable(_plan("产出 报告.docx")) is False
    assert plan_suggests_exec_office_deliverable(_plan("写一份 Word 文档")) is False
    assert (
        plan_suggests_exec_office_deliverable(
            _plan("写文件", artifacts=["AgentCore/文档/报告.docx"])
        )
        is False
    )


def test_no_office_warning_for_docx_and_pdf_without_execution():
    """`.docx` / `.pdf` 与执行沙箱正交 → 无执行环境也不报能力缺失。"""
    for plan in (
        _plan("写一份 Word 报告 报告.docx"),
        _plan("产出 说明.pdf"),
    ):
        assert execution_capability_warning(plan, _CloudBackend()) is None


def test_office_warning_still_fires_for_pptx_and_xlsx_without_execution():
    """`.pptx` / `.xlsx` 无确定性导出器，仍须执行环境 → 预警保留。"""
    for plan in (_plan("产出 课件.pptx"), _plan("产出 台账.xlsx")):
        warn = execution_capability_warning(plan, _CloudBackend())
        assert warn is not None
        assert "未装配" in warn


def test_office_warning_does_not_claim_word_pdf_impossible():
    """混批命中 pptx 时，文案不得顺手把 Word/PDF 也说成做不到。"""
    warn = execution_capability_warning(
        _plan("产出 课件.pptx 与 讲义.docx"), _CloudBackend()
    )
    assert warn is not None
    assert "md_to_docx" in warn and "md_to_pdf" in warn


def test_soft_warning_silent_when_no_run_smell():
    assert execution_capability_warning(_plan("写一段说明文字"), _CloudBackend()) is None


def test_soft_warning_silent_on_bare_open_file():
    """裸「打开文件 / 打开 .mdc」无运行味，不再触发 execution soft warning。"""
    assert execution_capability_warning(_plan("打开文件"), _CloudBackend()) is None
    assert (
        execution_capability_warning(
            _plan("打开 `.cursor/rules/x.mdc`"), _CloudBackend()
        )
        is None
    )


def test_soft_warning_on_open_acceptance_via_yanshou():
    """「打开验收」仍经「验收」命中运行类 soft warning。"""
    warn = execution_capability_warning(_plan("打开验收"), _CloudBackend())
    assert warn is not None
    assert "未装配" in warn or "能力提示" in warn
