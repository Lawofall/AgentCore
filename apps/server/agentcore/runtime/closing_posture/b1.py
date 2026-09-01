"""B1 batch clear + latch reset (prepare / resume wire).

``clear_b1_closing_latches`` resets turn-scoped B1 latches.
Does **not** clear browser_assembled — ``build_workspace_context`` re-stamps
it each turn (often after this clear on the prepare path).

空心措辞扫描已删（不观测、不清气泡）。超席 / 超时 / 掐断 latch 留下。
浏览器声称 ``_browser_claim_rework`` 恒 None，仍 re-export 供测试。
"""

from __future__ import annotations

from typing import Any

from .browser import (
    _browser_claim_rework,  # withdrawn: always None; keep export for tests
    clear_browser_tool_success,
)
from .cancel_zero import (
    clear_cancel_zero_output,
)
from .cloud_web import clear_cloud_web_verify_gap
from .cutoff import (
    clear_cutoff_delivery_gap,
)
from .empty_handoff import (
    clear_empty_handoff_storm,
)
from .over_seat import (
    clear_over_seat_reject,
)
from .verify_budget import (
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


__all__ = [
    "_browser_claim_rework",
    "clear_b1_closing_latches",
    "reset_turn_scoped_closing_state",
]
