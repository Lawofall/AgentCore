"""Outer-loop verify timeout latch.

test_run idle hang / disaster forced-stop → incomplete（进程已中止，非仍在跑）.
Symbol names keep ``verify_budget`` for import stability.
空心措辞扫描已删；本模块只留 latch。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_turn_verify_budget_exhausted: ContextVar[bool] = ContextVar(
    "turn_verify_budget_exhausted", default=False
)


def note_verify_budget_exhausted() -> None:
    """Latch when test_run timed out incomplete（进程已中止，非仍在跑）."""
    _turn_verify_budget_exhausted.set(True)


def clear_verify_budget_exhausted() -> None:
    _turn_verify_budget_exhausted.set(False)


def turn_has_verify_budget_exhausted() -> bool:
    return bool(_turn_verify_budget_exhausted.get())


def _gap_text_looks_like_verify_timeout(text: str) -> bool:
    if "非仍在跑" not in text and "验证未完成" not in text:
        return False
    return (
        "预算耗尽" in text
        or "无响应" in text
        or "强制中止" in text
        or "灾难顶" in text
    )


def note_verify_budget_from_delivery(gaps: list[Any] | None = None) -> None:
    """Stamp latch from delivery_status gaps with reason=verify_budget（结构化真源）."""
    for gap in gaps or []:
        if not isinstance(gap, dict):
            if _gap_text_looks_like_verify_timeout(str(gap or "")):
                note_verify_budget_exhausted()
                return
            continue
        reason = str(gap.get("reason") or "").strip()
        if reason == "verify_budget":
            note_verify_budget_exhausted()
            return
        if _gap_text_looks_like_verify_timeout(str(gap.get("description") or "")):
            note_verify_budget_exhausted()
            return
