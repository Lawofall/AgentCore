"""Deterministic code-audit ledger merge (no supervisor worker)."""

from __future__ import annotations

import json

from agentcore.runtime.runs.audit_ledger import (
    AUDIT_LEDGER_HEADING,
    collect_accepted_audit_json_paths,
    merge_audit_json_texts,
    module_from_audit_path,
    render_audit_ledger,
)
from agentcore.runtime.runs.file_acceptance import build_file_acceptance
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import Deliverable, RunPhase, RunSpec, RunState


def _finding(
    *,
    fid: str,
    summary: str,
    severity: str = "中",
    verdict: str = "属实",
    evidence: str = "a.py:1",
) -> dict:
    return {
        "id": fid,
        "summary": summary,
        "severity": severity,
        "verdict": verdict,
        "verification": "全文精读",
        "evidence": evidence,
    }


def _json(*findings: dict) -> str:
    return json.dumps({"findings": list(findings)})


def test_module_from_audit_path_strips_slot():
    assert (
        module_from_audit_path("AgentCore/文档/reviews/code-audit-0-server.audit.json")
        == "server"
    )


def test_merge_same_id_dedups():
    texts = {
        "code-audit-0-a.audit.json": _json(_finding(fid="X1", summary="同一洞")),
        "code-audit-1-b.audit.json": _json(_finding(fid="X1", summary="同一洞再写")),
    }
    merged = merge_audit_json_texts(texts)
    assert len(merged.n_rows) == 1
    assert merged.n_rows[0].finding_id == "X1"
    assert merged.conflicts == []


def test_merge_same_evidence_conflict_excluded_from_n():
    texts = {
        "code-audit-0-a.audit.json": _json(
            _finding(fid="A", summary="会炸", evidence="foo.py:10", verdict="属实")
        ),
        "code-audit-1-b.audit.json": _json(
            _finding(fid="B", summary="不会炸", evidence="foo.py:10", verdict="误报")
        ),
    }
    merged = merge_audit_json_texts(texts)
    assert merged.n_rows == []
    assert {r.finding_id for r in merged.conflicts} == {"A", "B"}


def test_merge_pending_and_low_not_in_n():
    texts = {
        "code-audit-0-a.audit.json": _json(
            _finding(fid="H", summary="高危", severity="高", evidence="h.py:1"),
            _finding(
                fid="P",
                summary="待核",
                verdict="待核实",
                severity="低",
                evidence="p.py:1",
            ),
            _finding(
                fid="L",
                summary="小卫生",
                severity="低",
                evidence="l.py:1",
            ),
        )
    }
    merged = merge_audit_json_texts(texts)
    assert [r.finding_id for r in merged.n_rows] == ["H"]
    assert [r.finding_id for r in merged.pending] == ["P"]


def test_render_empty_when_no_texts():
    assert render_audit_ledger({}) == ""


def test_render_lists_n_and_conflicts():
    texts = {
        "code-audit-0-a.audit.json": _json(
            _finding(fid="H1", summary="注入", severity="高", evidence="a.py:2")
        ),
        "code-audit-1-b.audit.json": _json(
            _finding(fid="C1", summary="甲说是", evidence="b.py:3", verdict="属实"),
            _finding(fid="C2", summary="乙说否", evidence="b.py:3", verdict="误报"),
        ),
    }
    text = render_audit_ledger(texts)
    assert AUDIT_LEDGER_HEADING in text
    assert "属实中+ N=1" in text
    assert "冲突 2 条不进 N" in text
    assert "H1" in text
    assert "冲突·未定案" in text


def test_unreadable_json_counts_unread():
    merged = merge_audit_json_texts({"x.audit.json": "not-json{"})
    assert merged.unread == 1
    assert merged.n_rows == []


def test_collect_only_accepted_audit_json_on_gated_completed():
    json_path = "AgentCore/文档/reviews/code-audit-0-a.audit.json"
    md = "AgentCore/文档/reviews/code-audit-0-a.md"
    plan = RunPlan(
        nodes=[
            RunSpec(
                run_id="audit_0",
                task="审",
                role="代码审计员",
                deliverable=Deliverable(
                    form="files",
                    artifacts=[md, json_path],
                    code_audit_gate=True,
                ),
            ),
            RunSpec(
                run_id="other",
                task="写",
                role="撰稿",
                deliverable=Deliverable(form="files", artifacts=["n.md"]),
            ),
        ]
    )
    results = {
        "audit_0": RunState(
            phase=RunPhase.COMPLETED,
            file_acceptance=build_file_acceptance(
                [md, json_path], phase=RunPhase.COMPLETED
            ),
        ),
        "other": RunState(
            phase=RunPhase.COMPLETED,
            file_acceptance=build_file_acceptance(["n.md"], phase=RunPhase.COMPLETED),
        ),
    }
    assert collect_accepted_audit_json_paths(plan, results) == [json_path]


def test_collect_skips_unaccepted_json():
    json_path = "AgentCore/文档/reviews/code-audit-0-a.audit.json"
    plan = RunPlan(
        nodes=[
            RunSpec(
                run_id="audit_0",
                task="审",
                deliverable=Deliverable(code_audit_gate=True, artifacts=[json_path]),
            )
        ]
    )
    results = {
        "audit_0": RunState(
            phase=RunPhase.COMPLETED,
            files_touched=[json_path],
        )
    }
    assert collect_accepted_audit_json_paths(plan, results) == []
