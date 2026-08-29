"""Turn 级台账 SSE 通道收口（引用即出处 P1 地基 · P2 投影）。

台账全量/增量走独立 ``evidence_ledger`` 事件；``citations_event`` 在 settle 时
按成稿 ``cited_ids`` 从台账投影为**仅引用集**（无硬帽；对称提案 Q11）。
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.citations import (
    extract_ledger_ref_ids,
    project_cited_citations,
    stamp_citations_from_ledger,
)
from agentcore.runtime.events import EventSink, evidence_ledger_event

logger = get_logger(__name__)


def emit_turn_evidence_ledger(
    sink: EventSink,
    *,
    ledger: Any | None,
    content: str,
    citations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Settle/resume 收口：发射台账通道，返回 (cited_cards, entries, cited_ids)。

    P2：有台账时 ``citations_event`` 载荷 = ``project_cited_citations``（仅成稿引用）；
    未引用命中只留在 ``entries``。无台账时回退盖戳 mid-turn sink（遗留路径）。
    """
    if ledger is None:
        return citations, [], []

    try:
        entries = list(ledger.all_entries())
    except Exception:
        logger.warning("citations.ledger_snapshot_failed", exc_info=True)
        return citations, [], []

    cited_all = extract_ledger_ref_ids(content)
    citable = {
        str(e.get("id") or "")
        for e in entries
        if e.get("id") and e.get("citable", True)
    }
    cited_ids = [eid for eid in cited_all if eid in citable]
    if entries:
        cited_cards = project_cited_citations(entries, cited_ids)
    else:
        # 无登记条目：保留 mid-turn sink（若有）并尽量盖戳。
        cited_cards = stamp_citations_from_ledger(citations, entries) if citations else []

    if entries or cited_ids:
        # Drain any undrained mid-turn delta so cursor stays consistent; settle
        # authoritative payload is ``entries`` (full replace on the client).
        try:
            delta = list(ledger.drain_delta())
        except Exception:
            delta = []
        sink.emit(
            evidence_ledger_event(
                delta=delta,
                entries=entries,
                cited_ids=cited_ids,
            )
        )
    return cited_cards, entries, cited_ids


def emit_ledger_delta(sink: EventSink, ledger: Any | None) -> None:
    """工具登记后发射增量（live）；空 delta 不发射。"""
    if ledger is None:
        return
    try:
        delta = list(ledger.drain_delta())
    except Exception:
        logger.warning("citations.ledger_delta_failed", exc_info=True)
        return
    if not delta:
        return
    sink.emit(evidence_ledger_event(delta=delta))
