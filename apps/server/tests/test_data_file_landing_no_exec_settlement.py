"""No-exec data_file_landing: soft self-notes stay soft; hand-copied tables stay hard."""

from __future__ import annotations

from agentcore.runtime.delegate.completion import (
    collect_worker_gaps,
    format_worker_gaps_block,
)
from agentcore.runtime.delegate.delivery_status import (
    REASON_NO_EXEC_TABLE,
    build_delivery_status,
)
from agentcore.runtime.runs.contract import (
    check_contract,
    collect_opaque_source_data_paths,
)
from agentcore.runtime.runs.executor.shared import _delivery_gaps_from_warnings
from agentcore.runtime.runs.file_acceptance import build_file_acceptance
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import Deliverable, RunPhase, RunSpec, RunState

_REPORT = (
    "# 原件结构报告\n\n"
    "源文件是虚构演示账单 attachments/synthetic_bill.pdf。\n"
    "列：日期、类型、金额、备注。共 57 笔。\n"
)
_CLEAN_REPORT = "# 原件结构报告\n\n列：日期、类型、金额。共 57 笔。\n"
_SCRIPT = (
    "# 待跑变换脚本：按类型拆收入/支出，本回合不跑\n"
    "SAMPLE_ROWS = [('2024-03-01', '工资', 8000)]  # 示例行\n"
)
_CLEAN_CSV = "日期,类型,金额\n2024-03-01,工资,8000\n"
_SOURCE_PDF = "attachments/synthetic_bill.pdf"
_SOURCE_CSV = "attachments/synthetic_bill.csv"


def _accepted(*paths: str) -> list[dict]:
    return build_file_acceptance(list(paths), phase=RunPhase.COMPLETED)


def _settle(paths: list[str], verdict):
    delivery_gaps = _delivery_gaps_from_warnings(
        list(verdict.warnings),
        None,
        files_landed=True,
        stamped_rows=verdict.warning_rows,
    )
    plan = RunPlan(
        nodes=[RunSpec(run_id="w1", task="整理账单", role="数据处理员")]
    )
    state = RunState(
        phase=RunPhase.COMPLETED,
        content="三件套已落盘",
        files_touched=paths,
        file_acceptance=_accepted(*paths),
        warnings=list(verdict.warnings),
        delivery_gaps=delivery_gaps,
    )
    results = {"w1": state}
    gaps = collect_worker_gaps(plan, results)
    block = format_worker_gaps_block(gaps)
    payload = build_delivery_status(plan, results, execution_id="e-landing")
    return gaps, block, payload


def _assert_bans_completeness(block: str) -> None:
    assert "【禁止】" in block
    assert "完整交付" in block
    assert "全部完成" in block
    assert "可运行无缺" in block


def test_collect_opaque_source_skips_historical_attachments_and_drafts():
    """本回合附件算源；历轮 attachments/ 与 AgentCore/ 草稿不算。"""
    this_turn = collect_opaque_source_data_paths(
        material_paths=[_SOURCE_PDF],
        workspace_paths=[
            _SOURCE_PDF,
            "attachments/old_bill.pdf",
            "AgentCore/文档/工作稿/income.csv",
            "收入.csv",
        ],
        landed_paths=["收入.csv"],
    )
    assert this_turn == [_SOURCE_PDF]

    inline_only = collect_opaque_source_data_paths(
        material_paths=[],
        workspace_paths=["收入.csv", "支出.csv"],
        landed_paths=["收入.csv", "支出.csv"],
    )
    assert inline_only == []

    project_xlsx = collect_opaque_source_data_paths(
        material_paths=[],
        workspace_paths=["data/q1.xlsx", "AgentCore/文档/工作稿/note.md"],
        landed_paths=["AgentCore/文档/工作稿/structure.md"],
    )
    assert project_xlsx == ["data/q1.xlsx"]

    attached_csv = collect_opaque_source_data_paths(
        material_paths=[_SOURCE_CSV],
        workspace_paths=[_SOURCE_CSV, "收入.csv"],
        landed_paths=["收入.csv"],
    )
    assert attached_csv == [_SOURCE_CSV]

    preparsed_md = collect_opaque_source_data_paths(
        material_paths=["attachments/synthetic_bill.pdf.md"],
        workspace_paths=["attachments/synthetic_bill.pdf.md"],
        landed_paths=[],
    )
    assert preparsed_md == []


def test_no_exec_trio_soft_notes_do_not_force_partial_delivery():
    """无执行：报告+脚本凑齐即交付完成；报告里的虚构自注不再进合同。"""
    paths = [
        "AgentCore/文档/工作稿/synthetic_bill_structure.md",
        "AgentCore/文档/工作稿/build_excel.py",
    ]
    contents = {
        paths[0]: _REPORT,
        paths[1]: _SCRIPT,
    }
    verdict = check_contract(
        "结构报告与待跑脚本已落盘",
        Deliverable(form="files", artifacts=paths),
        files_written=2,
        workspace_paths=paths,
        artifact_contents=contents,
        can_execute=False,
        source_data_paths=[_SOURCE_PDF],
    )
    assert verdict.ok
    assert verdict.failures == []
    assert not any("虚构" in w or "示例" in w or "骨架" in w for w in verdict.warnings)
    assert not any(
        row.get("reason") == "unverified_note" for row in verdict.warning_rows
    )
    assert not any(row.get("reason") == REASON_NO_EXEC_TABLE for row in verdict.warning_rows)

    _gaps, block, payload = _settle(paths, verdict)
    assert "部分交付" not in block
    assert "尚未齐备" not in block
    assert "终稿必须使用" not in block
    assert payload is not None
    assert payload["state"] == "delivered"
    assert not any(
        isinstance(g, dict) and g.get("reason") == "unverified_note"
        for g in payload["gaps"]
    )
    assert set(payload["delivered_files"]) == set(paths)


def test_no_exec_source_csv_itself_is_not_a_landed_table():
    """源附件 csv 出现在工作区索引里，不等于手抄表；三件套仍是完整交付。"""
    paths = [
        "AgentCore/文档/工作稿/synthetic_bill_structure.md",
        "AgentCore/文档/工作稿/build_excel.py",
    ]
    contents = {paths[0]: _CLEAN_REPORT, paths[1]: "print('ok')\n"}
    verdict = check_contract(
        "结构报告与待跑脚本已落盘",
        Deliverable(form="files", artifacts=paths),
        files_written=2,
        workspace_paths=[_SOURCE_CSV, *paths],
        artifact_contents=contents,
        can_execute=False,
        source_data_paths=[_SOURCE_CSV],
    )
    assert verdict.ok
    assert not any(row.get("reason") == REASON_NO_EXEC_TABLE for row in verdict.warning_rows)
    _gaps, block, payload = _settle(paths, verdict)
    assert "部分交付" not in block
    assert payload is not None
    assert payload["state"] == "delivered"


def test_no_exec_fabricated_table_still_flagged():
    """无执行 + 源数据文件（PDF）+ 手抄表：硬缺口。不靠自注文案、不靠产出队形。"""
    paths = [
        "AgentCore/文档/工作稿/synthetic_bill_structure.md",
        "AgentCore/文档/工作稿/build_excel.py",
        "AgentCore/文档/工作稿/income.csv",
    ]
    contents = {
        paths[0]: _CLEAN_REPORT,
        paths[1]: "print('ok')\n",
        paths[2]: _CLEAN_CSV,
    }
    verdict = check_contract(
        "交了一张结果表",
        Deliverable(form="files", artifacts=paths),
        files_written=3,
        workspace_paths=paths,
        artifact_contents=contents,
        can_execute=False,
        source_data_paths=[_SOURCE_PDF],
    )
    assert verdict.ok
    assert any("表文件" in w for w in verdict.warnings)
    assert any(
        row.get("reason") == REASON_NO_EXEC_TABLE for row in verdict.warning_rows
    )
    assert not any("示例" in w or "虚构" in w for w in verdict.warnings)

    gaps, block, payload = _settle(paths, verdict)
    assert gaps
    assert "部分交付" in block
    assert "尚未齐备" in block
    _assert_bans_completeness(block)
    assert payload is not None
    assert payload["state"] == "partial"
    assert any(
        isinstance(g, dict) and g.get("reason") == REASON_NO_EXEC_TABLE
        for g in payload["gaps"]
    )


def test_no_exec_attached_csv_source_still_flagged():
    """无执行 + 本回合 CSV 附件 + 落结果表：硬缺口（源是类型+附件，不靠文件名）。"""
    paths = ["AgentCore/文档/工作稿/income.csv"]
    verdict = check_contract(
        "已落盘",
        Deliverable(form="files", artifacts=paths),
        files_written=1,
        workspace_paths=paths,
        artifact_contents={paths[0]: _CLEAN_CSV},
        can_execute=False,
        source_data_paths=[_SOURCE_CSV],
    )
    assert any(row.get("reason") == REASON_NO_EXEC_TABLE for row in verdict.warning_rows)
    _gaps, block, payload = _settle(paths, verdict)
    assert "部分交付" in block
    assert payload is not None
    assert payload["state"] == "partial"


def test_no_exec_inline_table_is_not_a_gap():
    """内联小数据、无源文件：落 csv 是产品，不判硬缺口、不逼部分交付。"""
    paths = [
        "AgentCore/文档/工作稿/收入.csv",
        "AgentCore/文档/工作稿/支出.csv",
    ]
    contents = {p: _CLEAN_CSV for p in paths}
    verdict = check_contract(
        "已整理成收入/支出分表",
        Deliverable(form="files", artifacts=paths),
        files_written=2,
        workspace_paths=paths,
        artifact_contents=contents,
        can_execute=False,
    )
    assert verdict.ok
    assert not any("表文件" in w for w in verdict.warnings)
    assert not any(row.get("reason") == REASON_NO_EXEC_TABLE for row in verdict.warning_rows)

    _gaps, block, payload = _settle(paths, verdict)
    assert "部分交付" not in block
    assert "尚未齐备" not in block
    assert payload is not None
    assert payload["state"] == "delivered"
    assert not any(
        isinstance(g, dict) and g.get("reason") == REASON_NO_EXEC_TABLE
        for g in payload["gaps"]
    )
    assert set(payload["delivered_files"]) == set(paths)


def test_no_exec_xlsx_flagged_without_file_text():
    """二进制 xlsx 不进正文扫描：有源 PDF 时仍靠路径后缀判硬缺口。"""
    paths = ["structure.md", "build.py", "out.xlsx"]
    contents = {
        "structure.md": _CLEAN_REPORT,
        "build.py": "print(1)\n",
    }
    verdict = check_contract(
        "已落盘",
        Deliverable(form="files", artifacts=paths),
        files_written=3,
        workspace_paths=paths,
        artifact_contents=contents,
        can_execute=False,
        source_data_paths=[_SOURCE_PDF],
    )
    assert verdict.ok
    assert any("表文件" in w and "out.xlsx" in w for w in verdict.warnings)
    gaps, block, payload = _settle(paths, verdict)
    assert any(
        isinstance(row, dict) and row.get("reason") == REASON_NO_EXEC_TABLE
        for _, rows in gaps
        for row in rows
    )
    assert "部分交付" in block
    assert payload is not None
    assert payload["state"] == "partial"


def test_with_exec_trio_self_note_is_not_a_contract_warning():
    """有执行路径：同样的报告+脚本也不再因自注文案进合同。"""
    paths = [
        "AgentCore/文档/工作稿/synthetic_bill_structure.md",
        "AgentCore/文档/工作稿/build_excel.py",
    ]
    contents = {paths[0]: _REPORT, paths[1]: _SCRIPT}
    no_exec = check_contract(
        "已落盘",
        Deliverable(form="files", artifacts=paths),
        files_written=2,
        workspace_paths=paths,
        artifact_contents=contents,
        can_execute=False,
        source_data_paths=[_SOURCE_PDF],
    )
    with_exec = check_contract(
        "已落盘",
        Deliverable(form="files", artifacts=paths),
        files_written=2,
        workspace_paths=paths,
        artifact_contents=contents,
        can_execute=True,
        source_data_paths=[_SOURCE_PDF],
    )
    default_exec = check_contract(
        "已落盘",
        Deliverable(form="files", artifacts=paths),
        files_written=2,
        workspace_paths=paths,
        artifact_contents=contents,
        source_data_paths=[_SOURCE_PDF],
    )
    assert not any("虚构" in w or "示例" in w for w in no_exec.warnings)
    assert with_exec.warnings == default_exec.warnings == no_exec.warnings
    assert not any(row.get("reason") == REASON_NO_EXEC_TABLE for row in with_exec.warning_rows)

    _gaps, block, payload = _settle(paths, with_exec)
    assert "部分交付" not in block
    assert payload is not None
    assert payload["state"] == "delivered"


def test_no_exec_trio_keeps_skeleton_warning_as_soft():
    """TODO / 虚构演示文案不再进合同；不强制部分交付。"""
    paths = ["structure.md", "build.py"]
    contents = {
        "structure.md": "# 报告\n\nTODO: 补列说明\n这份是虚构演示账单。\n",
        "build.py": "print('ok')\n",
    }
    verdict = check_contract(
        "已落盘",
        Deliverable(form="files", artifacts=paths),
        files_written=2,
        workspace_paths=paths,
        artifact_contents=contents,
        can_execute=False,
        source_data_paths=[_SOURCE_PDF],
    )
    assert verdict.ok
    assert not any("骨架" in w or "TODO" in w or "虚构" in w for w in verdict.warnings)
    assert verdict.warning_rows == []
    _gaps, block, payload = _settle(paths, verdict)
    assert "部分交付" not in block
    assert payload is not None
    assert payload["state"] == "delivered"
