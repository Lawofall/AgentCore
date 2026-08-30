"""Deterministic merge of ``*.audit.json`` ledgers for CEO close.

No extra worker: completed ``code_audit_gate`` nodes already paid for structured
findings. This module joins / dedups / flags conflicts so the CEO talks from a
table instead of a serial synthesizer hop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentcore.runtime.runs.code_audit_gate import (
    normalize_audit_evidence,
    normalize_audit_severity,
    normalize_audit_verdict,
    parse_audit_json,
)
from agentcore.runtime.runs.types import RunPhase

_N_SEVERITIES = frozenset({"高", "中"})
_MAX_N_ROWS = 32
_MAX_SIDE_ROWS = 8
AUDIT_LEDGER_HEADING = "### 审计台账（引擎合并）"


@dataclass(frozen=True)
class LedgerRow:
    finding_id: str
    module: str
    severity: str
    verdict: str
    summary: str
    evidence: str
    source_path: str


@dataclass
class MergedAuditLedger:
    n_rows: list[LedgerRow] = field(default_factory=list)
    conflicts: list[LedgerRow] = field(default_factory=list)
    pending: list[LedgerRow] = field(default_factory=list)
    unread: int = 0
    sources: int = 0


def collect_accepted_audit_json_paths(plan: Any, results: dict[str, Any]) -> list[str]:
    """Accepted ``*.audit.json`` on completed ``code_audit_gate`` nodes."""
    out: list[str] = []
    seen: set[str] = set()
    for node in getattr(plan, "nodes", []):
        deliverable = getattr(node, "deliverable", None)
        if deliverable is None or not getattr(deliverable, "code_audit_gate", False):
            continue
        state = results.get(getattr(node, "run_id", ""))
        if state is None or getattr(state, "phase", None) is not RunPhase.COMPLETED:
            continue
        for row in getattr(state, "file_acceptance", None) or []:
            if not isinstance(row, dict) or row.get("status") != "accepted":
                continue
            path = str(row.get("path") or "").replace("\\", "/").strip()
            if path.endswith(".audit.json") and path not in seen:
                seen.add(path)
                out.append(path)
    return out


async def load_audit_json_by_path(
    plan: Any,
    results: dict[str, Any],
    backend: Any,
) -> dict[str, str]:
    """Best-effort read of accepted audit JSON texts. Missing paths omitted."""
    paths = collect_accepted_audit_json_paths(plan, results)
    read = getattr(backend, "read", None) if backend is not None else None
    if not paths or read is None:
        return {}
    out: dict[str, str] = {}
    for path in paths:
        try:
            text = await read(path)
        except Exception:  # noqa: BLE001 — contents are best-effort
            continue
        if isinstance(text, str) and text.strip():
            out[path] = text
    return out


def module_from_audit_path(path: str) -> str:
    """Human skim label from ``code-audit-{slot}-{slug}.audit.json``."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if name.endswith(".audit.json"):
        name = name[: -len(".audit.json")]
    if name.startswith("code-audit-"):
        rest = name[len("code-audit-") :]
        slot, sep, slug = rest.partition("-")
        if sep and slot.isdigit() and slug:
            return slug
    return name or path


def _evidence_key(evidence: str) -> str:
    return " ".join(evidence.lower().split())


def merge_audit_json_texts(texts: dict[str, str]) -> MergedAuditLedger:
    """Join findings: same id or same evidence → one row; verdict clash → conflict."""
    parsed: list[tuple[str, dict[str, Any]]] = []
    unread = 0
    for path, raw in texts.items():
        data, err = parse_audit_json(raw)
        if data is None or err:
            unread += 1
            continue
        parsed.append((path, data))

    raw_rows: list[LedgerRow] = []
    for path, data in parsed:
        findings = data.get("findings")
        if not isinstance(findings, list):
            unread += 1
            continue
        module = module_from_audit_path(path)
        for i, item in enumerate(findings):
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or item.get("id") or "").strip()
            if not summary:
                continue
            finding_id = str(item.get("id") or "").strip() or f"{module}:{i}"
            raw_rows.append(
                LedgerRow(
                    finding_id=finding_id,
                    module=module,
                    severity=normalize_audit_severity(str(item.get("severity") or "")),
                    verdict=normalize_audit_verdict(str(item.get("verdict") or "")),
                    summary=summary,
                    evidence=normalize_audit_evidence(item.get("evidence")),
                    source_path=path,
                )
            )

    by_evidence: dict[str, list[LedgerRow]] = {}
    no_evidence: list[LedgerRow] = []
    for row in raw_rows:
        key = _evidence_key(row.evidence)
        if not key:
            no_evidence.append(row)
            continue
        by_evidence.setdefault(key, []).append(row)

    kept: list[LedgerRow] = []
    conflicts: list[LedgerRow] = []
    seen_ids: set[str] = set()

    def _take(row: LedgerRow, *, conflict: bool = False) -> None:
        if row.finding_id in seen_ids:
            return
        seen_ids.add(row.finding_id)
        if conflict:
            conflicts.append(row)
        else:
            kept.append(row)

    for group in by_evidence.values():
        signatures = {(r.verdict, r.severity) for r in group}
        if len(signatures) > 1:
            for row in group:
                _take(row, conflict=True)
            continue
        _take(group[0])

    for row in no_evidence:
        _take(row)

    n_rows = [
        r for r in kept if r.verdict == "属实" and r.severity in _N_SEVERITIES
    ]
    pending = [r for r in kept if r.verdict == "待核实"]
    return MergedAuditLedger(
        n_rows=n_rows,
        conflicts=conflicts,
        pending=pending,
        unread=unread,
        sources=len(texts),
    )


def _md_cell(value: str, *, cap: int = 80) -> str:
    text = " ".join(value.split())
    if len(text) > cap:
        text = text[: cap - 1] + "…"
    return text.replace("|", "\\|")


def _table(rows: list[LedgerRow], *, limit: int) -> list[str]:
    if not rows:
        return []
    shown = rows[:limit]
    extra = len(rows) - len(shown)
    lines = [
        "| id | 模块 | 严重度 | 定案 | 一句话 | 证据 |",
        "|---|---|---|---|---|---|",
    ]
    for row in shown:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(row.finding_id, cap=24),
                    _md_cell(row.module, cap=24),
                    _md_cell(row.severity, cap=12),
                    _md_cell(row.verdict, cap=12),
                    _md_cell(row.summary, cap=72),
                    _md_cell(row.evidence, cap=48),
                ]
            )
            + " |"
        )
    if extra:
        lines.append(f"另有 {extra} 条见分册 `.audit.json`。")
    return lines


def render_audit_ledger(texts: dict[str, str]) -> str:
    """CEO-facing table, or ``""`` when there is nothing to merge."""
    if not texts:
        return ""
    merged = merge_audit_json_texts(texts)
    if (
        not merged.n_rows
        and not merged.conflicts
        and not merged.pending
        and merged.unread == 0
    ):
        return ""
    lines = [
        AUDIT_LEDGER_HEADING,
        (
            f"属实中+ N={len(merged.n_rows)}（去重后）；"
            f"冲突 {len(merged.conflicts)} 条不进 N；"
            f"待核实 {len(merged.pending)}。"
            f"来源 {merged.sources} 份台账"
            + (f"，未读 {merged.unread}" if merged.unread else "")
            + "。"
        ),
        "设计如此 / 未覆盖见各分册。对人综述用本表，勿整段粘进终稿。",
    ]
    if merged.n_rows:
        lines.append("")
        lines.append("**属实中+**")
        lines.extend(_table(merged.n_rows, limit=_MAX_N_ROWS))
    if merged.conflicts:
        lines.append("")
        lines.append("**冲突·未定案（不进 N）**")
        lines.extend(_table(merged.conflicts, limit=_MAX_SIDE_ROWS))
    if merged.pending:
        lines.append("")
        lines.append("**待核实**")
        lines.extend(_table(merged.pending, limit=_MAX_SIDE_ROWS))
    return "\n".join(lines)


__all__ = [
    "AUDIT_LEDGER_HEADING",
    "LedgerRow",
    "MergedAuditLedger",
    "collect_accepted_audit_json_paths",
    "load_audit_json_by_path",
    "merge_audit_json_texts",
    "module_from_audit_path",
    "render_audit_ledger",
]
