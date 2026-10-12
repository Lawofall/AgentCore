"""Hard-ceiling honesty: informal verdict downgrade.

Covers ``max_rounds`` and ``token_budget`` symmetrically (worker salvage + CEO).
Does **not** expand the posture-A closed set.

User-visible 【收口说明】 prefixes are gone — the fuse is not a caption.
The model-facing ``[系统提示]`` steer is gone — honesty is the verdict downgrade.
Continuation HOW lives on the continuation tool, not a steer.
Round ceiling is a fuse, not a live countdown injected every ReAct round.
"""

from __future__ import annotations

from typing import Any

from .core import is_formal_complete_tier

# Hard-ceiling reasons that downgrade a formal ``delivered`` verdict.
_CEILING_HONESTY_REASONS = frozenset({"max_rounds", "token_budget"})


def enforce_ceiling_closing_honesty(content: str, *, reason: str) -> str:
    """No user-visible prefix. ``reason`` unused after banner and steer removal.

    Verdict still downgrades. Do not paste 【收口说明】 into the answer, and do
    not inject a user-role ``[系统提示]``.
    """
    del reason
    return content or ""


def downgrade_verdict_for_ceiling(
    *,
    reason: str = "max_rounds",
    promotion_ledger: Any = None,
) -> None:
    """Mark delivery informal when CEO hits a hard ceiling (cannot stay ``delivered``)."""
    from agentcore.runtime.delegate.delivery_status import (
        DeliveryVerdict,
        bind_delivery_verdict,
        read_delivery_verdict,
    )

    r = (reason or "").strip() or "max_rounds"
    if r not in _CEILING_HONESTY_REASONS:
        r = "max_rounds"
    verdict = read_delivery_verdict(promotion_ledger=promotion_ledger)
    if verdict is None:
        bind_delivery_verdict(
            DeliveryVerdict(
                state="partial",
                delivered_files=(),
                execution_id=f"ceiling_{r}",
            ),
            promotion_ledger=promotion_ledger,
        )
        return
    if not is_formal_complete_tier(verdict.state):
        return
    bind_delivery_verdict(
        DeliveryVerdict(
            state="partial",
            delivered_files=verdict.delivered_files,
            execution_id=verdict.execution_id,
            requires_draft_ack=verdict.requires_draft_ack,
            gap_reasons=getattr(verdict, "gap_reasons", ()),
            missing_declared=getattr(verdict, "missing_declared", ()),
            absent_claimed=getattr(verdict, "absent_claimed", ()),
        ),
        promotion_ledger=promotion_ledger,
    )


def downgrade_verdict_for_max_rounds(*, promotion_ledger: Any = None) -> None:
    """Alias: mark informal when CEO hits max_rounds."""
    downgrade_verdict_for_ceiling(reason="max_rounds", promotion_ledger=promotion_ledger)
