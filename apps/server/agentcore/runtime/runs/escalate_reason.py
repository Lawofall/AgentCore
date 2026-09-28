"""Shared escalate reason: wait | adjust. 无法识别则 wait。"""

from __future__ import annotations

ESCALATE_REASONS = frozenset({"wait", "adjust"})


def parse_escalate_reason(raw: object) -> str:
    """Tool / transcript 共用。不读旧参数 blocking/kind。"""
    reason = str(raw or "wait").strip().lower()
    return reason if reason in ESCALATE_REASONS else "wait"
