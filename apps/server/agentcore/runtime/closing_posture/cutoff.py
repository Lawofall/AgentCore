"""Cutoff / token_budget delivery honesty latch + soft banner.

定稿漂移 B′ · CEO 综收软横幅
Truth source = structured writing-cutoff gap reasons (or turn-token skip), not
posture-A word expansion. Does **not** expand the posture-A word list.

Banner teaches executable continuation: ``continue_from_run_id`` same main file;
no parallel same-role race; ``replaces_run_id`` cold handoff only.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from .ceiling import _TOKEN_BUDGET_CONTINUE_TEACH
from .core import claims_draft_acknowledgment
from .hollow import (
    _CEILING_HOLLOW_TEACH_BANNER,
    claims_hollow_teach_invite,
)

_CUTOFF_DELIVERY_GAP_REASONS = frozenset(
    {"token_budget", "worker_timeout", "turn_token_budget", "max_rounds"}
)

_turn_cutoff_delivery_gap: ContextVar[bool] = ContextVar(
    "turn_cutoff_delivery_gap", default=False
)

_CUTOFF_CLOSING_HONESTY_BANNER = (
    "【收口说明】本回合存在预算/掐断类交付缺口（对账为部分交付），"
    "以下不得视为无条件完整收卷——"
    "请按「部分交付 + 未闭合项」理解。"
    f"{_TOKEN_BUDGET_CONTINUE_TEACH}\n\n"
)

# Positive skip markers: already framed as partial — do not stack the banner.
_CUTOFF_HONEST_PARTIAL_MARKERS = ("部分交付", "部分落地", "尚未齐备", "未闭合")


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
    """Prefix soft banner when structured cutoff/partial latch is set.

    Soft only — never discards/rejects. Not gated on posture A (those claims often
    sit outside the closed set for this case). Skips when prose already frames
    partial delivery. Does **not** expand the posture-A word list.
    Hollow teach-invites（请开讲）always get the ceiling-hollow banner when latch set.
    """
    text = content or ""
    if not turn_has_cutoff_delivery_gap():
        return text
    stripped = text.lstrip()
    if stripped.startswith("【收口说明】"):
        return text
    if claims_hollow_teach_invite(text):
        return _CEILING_HOLLOW_TEACH_BANNER + text
    if claims_draft_acknowledgment(text):
        return text
    if any(m in text for m in _CUTOFF_HONEST_PARTIAL_MARKERS):
        return text
    return _CUTOFF_CLOSING_HONESTY_BANNER + text
