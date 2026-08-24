"""导出件台账：自报 ``derived_from`` → 用户面「文件位置」主推导出件，源 md 降为中间稿。

真实事故（用户要 Word，最终答复却把 `…起诉状.md` 报成「文件位置」）横跨两段：导出工具
不自报产物（.docx 从不进台账），且没有任何消费方读 ``derived_from``（补了自报也会 md +
docx 并列）。两段语义是一根的，故用同一条端到端断言钉死：真跑导出工具 → 引擎盖章 →
台账 → 路径验收 → 用户可见文案。
"""

from __future__ import annotations

from pathlib import Path

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.runs.file_acceptance import (
    REASON_CITATIONS_UNVERIFIED,
    build_file_acceptance,
    fold_exported_sources,
)
from agentcore.runtime.runs.serialize import (
    file_products_from_transcript,
    files_touched_from_transcript,
)
from agentcore.runtime.runs.types import RunPhase
from agentcore.tools.builtin.file_ops import FileWriteTool
from agentcore.tools.builtin.md_to_docx import MdToDocxTool
from agentcore.tools.builtin.md_to_pdf import MdToPdfTool
from agentcore.tools.file_products import with_file_products_marker
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.tools.sandbox import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _stamped(call_id: str, result: ToolResult) -> LLMMessage:
    """The tool message as the ENGINE writes it (tool_exec_call 单点盖章)。"""
    return LLMMessage(
        role="tool",
        content=with_file_products_marker(result.output, result.file_products),
        tool_call_id=call_id,
    )


def _accepted_row(path: str, *, kind: str = "", derived_from: str = "") -> dict:
    row: dict = {"path": path, "status": "accepted"}
    if kind:
        row["kind"] = kind
    if derived_from:
        row["derived_from"] = derived_from
    return row


async def test_export_tools_self_report_product_with_source_lineage(tmp_path: Path):
    """导出工具必须自报它真正落的盘 + 源文件（``file_write`` 那套自报契约）。"""
    (tmp_path / "报告.md").write_text("# 标题\n\n正文\n", encoding="utf-8")

    docx = await MdToDocxTool().execute({"path": "报告.md"}, _ctx(tmp_path))
    assert docx.success is True
    assert [(p.path, p.kind, p.derived_from) for p in docx.file_products] == [
        ("报告.docx", "docx", "报告.md")
    ]

    pdf = await MdToPdfTool().execute({"path": "报告.md"}, _ctx(tmp_path))
    assert pdf.success is True
    assert [(p.path, p.kind, p.derived_from) for p in pdf.file_products] == [
        ("报告.pdf", "pdf", "报告.md")
    ]


async def test_failed_export_reports_no_product(tmp_path: Path):
    """没导出成功就没有产物可自报（失败调用天然不入账）。"""
    result = await MdToDocxTool().execute({"path": "缺失.md"}, _ctx(tmp_path))
    assert result.success is False
    assert result.file_products == []


async def test_word_request_user_facing_location_points_at_docx(tmp_path: Path):
    """事故复现面：写 md → 导出 docx，用户看到的「文件位置」只主推 .docx。"""
    ctx = _ctx(tmp_path)
    md = "抚养费起诉状-昝雯.md"
    written = await FileWriteTool().execute(
        {"path": md, "content": "# 民事起诉状\n\n正文\n"}, ctx
    )
    assert written.success is True
    exported = await MdToDocxTool().execute({"path": md}, ctx)
    assert exported.success is True

    transcript = [_stamped("c1", written), _stamped("c2", exported)]
    products = file_products_from_transcript(transcript)
    touched = files_touched_from_transcript(transcript)
    assert touched == [md, "抚养费起诉状-昝雯.docx"]

    acceptance = build_file_acceptance(
        touched, phase=RunPhase.COMPLETED, products=products
    )
    assert acceptance == [
        {"path": md, "kind": "md", "status": "accepted"},
        {
            "path": "抚养费起诉状-昝雯.docx",
            "kind": "docx",
            "derived_from": md,
            "status": "accepted",
        },
    ]

    files, intermediates = fold_exported_sources(acceptance)
    assert files == ["抚养费起诉状-昝雯.docx"]
    assert intermediates == [md]


def test_fold_keeps_every_export_and_demotes_only_self_reported_sources():
    """一源多导（docx + pdf）：两件导出都主推，只有源 md 降级。"""
    acceptance = [
        _accepted_row("报告.md", kind="md"),
        _accepted_row("报告.docx", kind="docx", derived_from="报告.md"),
        _accepted_row("报告.pdf", kind="pdf", derived_from="报告.md"),
    ]
    assert fold_exported_sources(acceptance) == (
        ["报告.docx", "报告.pdf"],
        ["报告.md"],
    )


def test_fold_only_reads_self_report_not_extensions():
    """没自报 ``derived_from`` 就不折叠——禁止按扩展名/工具名猜派生关系。"""
    acceptance = [
        _accepted_row("报告.md", kind="md"),
        _accepted_row("报告.docx", kind="docx"),
    ]
    assert fold_exported_sources(acceptance) == (["报告.md", "报告.docx"], [])


def test_fold_keeps_source_when_export_was_rejected():
    """导出件未过验收 → 源 md 仍是用户唯一能拿的东西，不得连它一起折叠掉。"""
    acceptance = [
        _accepted_row("报告.md", kind="md"),
        {
            "path": "报告.docx",
            "kind": "docx",
            "derived_from": "报告.md",
            "status": "rejected",
            "reason": REASON_CITATIONS_UNVERIFIED,
        },
    ]
    assert fold_exported_sources(acceptance) == (["报告.md"], [])


def test_fold_no_op_when_source_absent_or_ledger_empty():
    """只落了导出件（源不在验收表 / 表为空）→ 无可折叠，导出件照常主推。"""
    only_export = [_accepted_row("报告.docx", kind="docx", derived_from="报告.md")]
    assert fold_exported_sources(only_export) == (["报告.docx"], [])
    assert fold_exported_sources([]) == ([], [])
    assert fold_exported_sources(None) == ([], [])


def test_fold_never_empties_the_list_on_pathological_lineage():
    """自报成环 / 自指：宁可不折叠，也不能把导出件藏掉让用户什么都看不到。"""
    cycle = [
        _accepted_row("a.docx", kind="docx", derived_from="b.docx"),
        _accepted_row("b.docx", kind="docx", derived_from="a.docx"),
    ]
    assert fold_exported_sources(cycle) == (["a.docx", "b.docx"], [])
    self_ref = [_accepted_row("报告.docx", kind="docx", derived_from="报告.docx")]
    assert fold_exported_sources(self_ref) == (["报告.docx"], [])


def test_fold_skips_preview_screenshots():
    """kind=image 的 derived_from 是预览注解，不得把源 HTML 折成中间稿。"""
    acceptance = [
        _accepted_row("site/index.html", kind="html"),
        _accepted_row(
            "site/preview-desktop.jpg",
            kind="image",
            derived_from="site/index.html",
        ),
    ]
    assert fold_exported_sources(acceptance) == (
        ["site/index.html", "site/preview-desktop.jpg"],
        [],
    )
