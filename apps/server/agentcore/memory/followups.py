"""Journal helpers shared by stage_card / research_first (chips mint retired).

CEO→user「下一步」quick-reply chips are fully offline: no LLM mint, no
``followups_generated`` / ``followups_unavailable`` emit, no ``set_followups`` on
new turns. ``messages.followups`` column remains for historical rows.

``select_motion_card_from_journal`` stays — leftover cards / research-chain
detection still need the last compliant worker ``motion_card``. Persist no longer
auto-emits a stage card from it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def select_motion_card_from_journal(
    entries: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    """Pick one compliant motion_card from this turn's journal debriefs.

    Walks ``run_completed`` / ``run_failed`` entries in order; **last compliant card
    wins** (later workers — typically the synthesis / 汇总分析师 — override earlier
    research hands). Re-validates via ``parse_motion_card``. Used by leftover-card
    / research_first detection — persist no longer auto-emits a stage card.
    Returns ``None`` when none present.
    """
    if not entries:
        return None
    from agentcore.runtime.terminal import RUN_PRODUCT_EVENT_TYPES
    from agentcore.tools.builtin.motion_card import parse_motion_card

    chosen: dict[str, Any] | None = None
    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "")
        if kind not in RUN_PRODUCT_EVENT_TYPES:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            continue
        debrief = payload.get("debrief")
        if not isinstance(debrief, Mapping):
            continue
        card, err = parse_motion_card(debrief.get("motion_card"))
        if card is not None and not err:
            chosen = card
    return chosen
