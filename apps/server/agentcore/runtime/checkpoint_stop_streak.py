"""Same-turn consecutive checkpoint STOP streak (ask_user / team_preview / plan_review).

First STOP keeps the CONTINUE-feed-to-CEO path (拒答可见). A second consecutive
STOP in the same turn (same user message / journal) force-closes via terminal
``INTERACT`` so no further CEO round starts. Streak is derived from durable
``*_resolved`` journal facts — survives suspend / resume without a soft-reminder
counter. Non-STOP decisions reset the streak.

``ADJUST`` is excluded even though team_preview adjust shares STOP's no-grant
feed-CEO path: consecutive revises must not escalate the turn to terminal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentcore.runtime.checkpoints import CheckpointDecision

# User-settled cold cards that share CheckpointDecision on the wire.
_RESOLVED_KINDS = frozenset(
    {
        "checkpoint_resolved",
        "team_preview_resolved",
        "plan_review_resolved",
    }
)


def consecutive_checkpoint_stops(
    entries: Sequence[Mapping[str, Any]] | None,
    *,
    ignore_checkpoint_id: str | None = None,
) -> int:
    """Trailing consecutive ``decision=stop`` count from journal ``*_resolved`` facts.

    Walks settled checkpoint decisions in order; any non-STOP decision resets.
    Current (not-yet-journaled) decision is not included — callers compare
    ``>= 1`` before settling a new STOP to detect the second consecutive stop.

    ``ignore_checkpoint_id`` skips a matching ``*_resolved`` fact entirely
    (cold-path prewrite of the card being settled: neither counted as STOP
    nor treated as a non-STOP reset).
    """
    streak = 0
    ignore = (ignore_checkpoint_id or "").strip()
    for entry in entries or ():
        if not isinstance(entry, Mapping):
            continue
        kind = str(entry.get("kind") or entry.get("type") or "")
        if kind not in _RESOLVED_KINDS:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            streak = 0
            continue
        if ignore and str(payload.get("checkpoint_id") or "").strip() == ignore:
            continue
        raw = str(payload.get("decision") or "").strip().lower()
        if raw == CheckpointDecision.ADJUST.value:
            # team_preview adjust reuses STOP's no-grant feed-CEO path but must
            # never increment the consecutive-stop terminal (multi-round revise).
            streak = 0
        elif raw == CheckpointDecision.STOP.value:
            streak += 1
        else:
            streak = 0
    return streak


def is_repeated_checkpoint_stop(
    entries: Sequence[Mapping[str, Any]] | None,
    decision: CheckpointDecision,
    *,
    ignore_checkpoint_id: str | None = None,
) -> bool:
    """True when ``decision`` is STOP and the journal already ends on ≥1 STOP.

    ``ADJUST`` is never a repeated stop — even on team_preview, where it shares
    STOP's no-grant feed-CEO path.
    """
    return (
        decision is CheckpointDecision.STOP
        and consecutive_checkpoint_stops(
            entries, ignore_checkpoint_id=ignore_checkpoint_id
        )
        >= 1
    )


def compose_repeated_stop_closing(*, note: str = "") -> str:
    """User-facing closing when the second consecutive stop ends the turn.

    Joined with pre-pause deliverable text by ``finish_terminal_resume`` — never
    empty (禁止静默空结束).
    """
    note_text = (note or "").strip()
    if note_text:
        # 用户已在卡上写明下一步，别再要他「发新消息说明」——这一轮只是没接着做。
        return (
            f"已按你的意思停下（你说：{note_text}）。上面是这次已经完成的部分。"
            "你说的这点这一轮还没动手——要按它继续，回一句就行。"
        )
    return "已按你的意思停下。上面是这次已经完成的部分；如需继续，请发新消息说明。"
