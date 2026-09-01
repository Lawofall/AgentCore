"""Cutoff / token_budget delivery honesty latch.

定稿漂移 B′ · structured writing-cutoff gap reasons (or turn-token skip), not
posture-A word expansion. Does **not** expand the posture-A word list.

User-visible 【收口说明】 prefixes are gone. Continuation teach lives in the
private ceiling steer. Latch remains（观测）；空心措辞扫描已删。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_CUTOFF_DELIVERY_GAP_REASONS = frozenset(
    {"token_budget", "worker_timeout", "turn_token_budget", "max_rounds"}
)

_turn_cutoff_delivery_gap: ContextVar[bool] = ContextVar(
    "turn_cutoff_delivery_gap", default=False
)


def note_cutoff_delivery_gap() -> None:
    """Latch turn-scoped writing-cutoff / token-budget gap evidence."""
    _turn_cutoff_delivery_gap.set(True)


def clear_cutoff_delivery_gap() -> None:
    """Reset at turn entry (fresh arm / resume wire)."""
    _turn_cutoff_delivery_gap.set(False)


def turn_has_cutoff_delivery_gap() -> bool:
    """True when this turn noted structured cutoff / token_budget delivery gaps."""
    return bool(_turn_cutoff_delivery_gap.get())


def note_cutoff_delivery_gap_from_delivery(
    gaps: list[Any] | None = None,
) -> None:
    """Stamp latch from delivery_status gaps with writing-cutoff / budget reasons."""
    for gap in gaps or []:
        if not isinstance(gap, dict):
            continue
        reason = str(gap.get("reason") or "").strip()
        if reason in _CUTOFF_DELIVERY_GAP_REASONS:
            note_cutoff_delivery_gap()
            return


def enforce_cutoff_closing_honesty(content: str) -> str:
    """No user-visible prefix. Latch remains for observation; finish_guard 不再据此清气泡。"""
    return content or ""
