"""B1 batch clear + cross-latch finish_guard probes (storm / hollow / re-exports).

``clear_b1_closing_latches`` resets turn-scoped B1 latches at prepare / resume wire.
Does **not** clear browser_assembled — ``build_workspace_context`` re-stamps
it each turn (often after this clear on the prepare path).

Cross-latch rework probes are re-exported here so ``closing_honesty_rework`` has
a single late-import surface.
"""

from __future__ import annotations

from typing import Any

from .browser import (
    _browser_claim_rework,
    clear_browser_tool_success,
)
from .cancel_zero import (
    clear_cancel_zero_output,
    turn_has_cancel_zero_output,
)
from .cloud_web import clear_cloud_web_verify_gap
from .core import (
    claims_draft_acknowledgment,
    claims_posture_a,
)
from .cutoff import (
    clear_cutoff_delivery_gap,
    turn_has_cutoff_delivery_gap,
)
from .empty_handoff import (
    clear_empty_handoff_storm,
    turn_has_empty_handoff_storm,
)
from .hollow import (
    claims_hollow_in_progress,
    claims_hollow_teach_invite,
)
from .over_seat import (
    clear_over_seat_reject,
    turn_has_over_seat_reject,
)
from .verify_budget import (
    _verify_budget_hollow_rework,
    clear_verify_budget_exhausted,
)
from .write_ownership import clear_unresolved_write_ownership


def clear_b1_closing_latches() -> None:
    """Reset B1 turn-scoped latches at turn entry (prepare / resume wire).

    Does **not** clear browser_assembled — ``build_workspace_context`` re-stamps
    it each turn (often after this clear on the prepare path).
    """
    clear_browser_tool_success()
    clear_over_seat_reject()
    clear_empty_handoff_storm()
    clear_cancel_zero_output()
    clear_verify_budget_exhausted()


def reset_turn_scoped_closing_state(*, promotion_ledger: Any = None) -> None:
    """Forget everything a previous turn latched, so ``finish_guard`` judges this one.

    The single owner of「新回合必须忘掉什么」。Both turn entries (prepare / resume wire)
    and the test isolation fixture call this — when the list lived inline at each site
    it drifted: the fixture cleared only the B1 latches, so a leaked
    ``current_delivery_verdict`` made unrelated tests demand a 缺口承认 depending on
    collection order. Add a new turn-scoped latch here, not at the call sites.
    """
    # Lazy: delivery_status imports this package, so a module-level import cycles.
    from agentcore.runtime.delegate.delivery_status import bind_delivery_verdict

    bind_delivery_verdict(None, promotion_ledger=promotion_ledger)
    clear_cloud_web_verify_gap()
    clear_cutoff_delivery_gap()
    clear_unresolved_write_ownership()
    clear_b1_closing_latches()


def _partial_storm_rework(content: str) -> str | None:
    """超席 / 空交接风暴 / cancel·0 产出：强制 PARTIAL 缺口清单，禁空悬·空心开讲."""
    text = content or ""
    if not text.strip():
        return None
    storm = (
        turn_has_over_seat_reject()
        or turn_has_empty_handoff_storm()
        or turn_has_cancel_zero_output()
    )
    if not storm:
        return None
    if claims_posture_a(text) or claims_hollow_in_progress(text):
        return (
            "本回合存在超席拒绝 / 大量空交接 / 取消且零落盘——"
            "对账须按 PARTIAL：禁止姿势 A 与『仍在进行』空悬终态。"
            "请给出已完成席位摘要 + 未交付缺口清单 + 可继续动作。"
        )
    if claims_hollow_teach_invite(text):
        return (
            "本回合存在空交接或硬顶类缺口——禁止空心『请开讲/我在听』邀请；"
            "请改为点名缺口与下一步。"
        )
    if not claims_draft_acknowledgment(text):
        return (
            "本回合超席/空交接/取消零产出——终稿须承认部分完成或点名未交付清单"
            "（缺口 + 可继续动作），禁止仅写『重新派工』短句。"
        )
    return None


def _ceiling_hollow_teach_rework(content: str) -> str | None:
    """cutoff/ceiling latch 后禁空心请开讲（finish_guard 轴；与 enforce 横幅双保险）。"""
    text = content or ""
    if not text.strip() or not claims_hollow_teach_invite(text):
        return None
    if not (
        turn_has_cutoff_delivery_gap()
        or turn_has_empty_handoff_storm()
        or turn_has_over_seat_reject()
    ):
        return None
    return (
        "本回合存在预算掐断/空交接类缺口——正文不得空心邀请用户开讲或『我在听·请讲』；"
        "请按部分交付点名已落地与未闭合项。"
    )


__all__ = [
    "_browser_claim_rework",
    "_ceiling_hollow_teach_rework",
    "_partial_storm_rework",
    "_verify_budget_hollow_rework",
    "clear_b1_closing_latches",
    "reset_turn_scoped_closing_state",
]
