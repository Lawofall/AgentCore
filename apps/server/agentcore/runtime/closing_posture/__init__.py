"""用户可见收口诚实性：对账档位真源 + 极窄姿势探测。

真源是 ``delivery_verdict.state``（非完成话术词表）：

- ``delivered`` = 正式完成（允许姿势 A）
- ``partial`` / ``notes`` ≈ 草稿·部分（禁止姿势 A；``requires_draft_ack`` 时另须正文承认缺口）
- ``blocked`` = 阻塞（禁止姿势 A；``requires_draft_ack`` 时另须承认缺口）

姿势 A = 宣称完整交付 / 全员收卷 / 完整可用 / 修好验绿。
探测用**闭集**正则，仅作「是否在说 A」的薄信号；**禁止**靠案面加完成话术词修案。
文献证据降档时用正向「草稿/缺口承认」闭集（``requires_draft_ack``），不靠把「综述已完成」加进黑名单。
``requires_draft_ack`` 亦闩 ``thin_review``（已声明复核落盘未对齐）、``verify_failed``
（丙轴验证失败）、以及 ``node_failed`` / ``artifact_rejected``（契约硬失败·节点 FAILED·
拒收产物）——仍不扩姿势 A 词表。
无对账卡 / ``no_batch``：不拦正文（团队状态走结构面，禁止完成话术拦截）。

resume / plan_review：派工过程 kickoff（方向：派团队…）不进用户可见续写基底与 G6 重灌，
终稿另写交付说明，避免过程流水账（ce1ecfc2）。

``finish_guard`` / resume ``join`` / 确认姿势 steer 均消费本模块。

Thin facade — implementation split by axis (under ``runtime/closing_posture/``):

* ``.core`` — tier & posture-A/C/draft + honesty rework
* ``.resume`` — resume / continuity join & steer
* ``.ceiling`` — hard-ceiling steer / banner / verdict downgrade
* ``.ceo_mutation`` — CEO mutation / disk-landing claims
* ``.cloud_web`` — cloud web verify latch + enforce
* ``.cutoff`` — cutoff / token_budget latch + enforce
* ``.write_ownership`` — write-ownership latch + enforce
* ``.browser`` — browser assemble/tool-success latch
* ``.over_seat`` / ``.empty_handoff`` / ``.cancel_zero`` — storm latches
* ``.verify_budget`` — verify-budget latch
* ``.hollow`` — hollow teach / in-progress claims
* ``.b1`` — prepare clear_b1 + cross-latch probes

Public import paths stay stable via re-exports below
(``agentcore.runtime.closing_posture`` / ``.<leaf>``; no flat root shims).
"""

from __future__ import annotations

from .b1 import clear_b1_closing_latches, reset_turn_scoped_closing_state
from .browser import (
    claims_browser_open_or_login,
    clear_browser_assembled,
    clear_browser_tool_success,
    note_browser_assembled,
    note_browser_tool_success,
    note_browser_tool_success_from_messages,
    turn_browser_assembled,
    turn_has_browser_tool_success,
)
from .cancel_zero import (
    clear_cancel_zero_output,
    note_cancel_zero_output,
    turn_has_cancel_zero_output,
)
from .ceiling import (
    ceiling_honesty_steer,
    downgrade_verdict_for_ceiling,
    downgrade_verdict_for_max_rounds,
    enforce_ceiling_closing_honesty,
)
from .ceo_mutation import (
    asks_whole_file_user_paste,
    claims_ceo_mutation_done,
    claims_disk_landing,
    enforce_ceo_mutation_honesty,
    turn_has_product_write_evidence,
)
from .cloud_web import (
    claims_cloud_web_verify_green,
    clear_cloud_web_verify_gap,
    enforce_cloud_web_verify_honesty,
    note_cloud_web_verify_gap,
    note_cloud_web_verify_gap_from_delivery,
    turn_has_cloud_web_verify_gap,
)
from .core import (
    claims_draft_acknowledgment,
    claims_full_delivery,
    claims_needs_confirm,
    claims_posture_a,
    claims_posture_c,
    closing_honesty_rework,
    closing_honesty_verdict_hit,
    is_formal_complete_tier,
    mutual_exclusion_rework,
    tier_forbids_posture_a,
)
from .cutoff import (
    clear_cutoff_delivery_gap,
    enforce_cutoff_closing_honesty,
    note_cutoff_delivery_gap,
    note_cutoff_delivery_gap_from_delivery,
    turn_has_cutoff_delivery_gap,
)
from .empty_handoff import (
    clear_empty_handoff_storm,
    note_empty_handoff_storm,
    turn_has_empty_handoff_storm,
)
from .hollow import (
    claims_hollow_in_progress,
    claims_hollow_teach_invite,
)
from .over_seat import (
    clear_over_seat_reject,
    note_over_seat_reject,
    turn_has_over_seat_reject,
)
from .resume import (
    is_process_dispatch_preamble,
    pre_pause_for_user_visible_continuity,
    reconcile_resume_closing,
    resume_continuity_steer,
    rewrite_stale_ask_after_dispatch,
)
from .verify_budget import (
    clear_verify_budget_exhausted,
    note_verify_budget_exhausted,
    note_verify_budget_from_delivery,
    turn_has_verify_budget_exhausted,
)
from .write_ownership import (
    apply_write_ownership_honesty_for_session,
    clear_unresolved_write_ownership,
    collect_unresolved_write_ownership_paths,
    downgrade_verdict_for_unresolved_write_ownership,
    enforce_write_ownership_honesty,
    note_unresolved_write_ownership,
    note_unresolved_write_ownership_from_ledger,
    reconcile_unresolved_write_ownership_latch,
    run_ids_for_write_ownership_scan,
    turn_has_unresolved_write_ownership,
)

__all__ = [
    "apply_write_ownership_honesty_for_session",
    "asks_whole_file_user_paste",
    "ceiling_honesty_steer",
    "claims_browser_open_or_login",
    "claims_ceo_mutation_done",
    "claims_cloud_web_verify_green",
    "claims_disk_landing",
    "claims_draft_acknowledgment",
    "claims_full_delivery",
    "claims_hollow_in_progress",
    "claims_hollow_teach_invite",
    "claims_needs_confirm",
    "claims_posture_a",
    "claims_posture_c",
    "clear_b1_closing_latches",
    "clear_browser_assembled",
    "clear_browser_tool_success",
    "clear_cancel_zero_output",
    "clear_cloud_web_verify_gap",
    "clear_cutoff_delivery_gap",
    "clear_empty_handoff_storm",
    "clear_over_seat_reject",
    "clear_unresolved_write_ownership",
    "clear_verify_budget_exhausted",
    "closing_honesty_rework",
    "closing_honesty_verdict_hit",
    "collect_unresolved_write_ownership_paths",
    "downgrade_verdict_for_ceiling",
    "downgrade_verdict_for_max_rounds",
    "downgrade_verdict_for_unresolved_write_ownership",
    "enforce_ceiling_closing_honesty",
    "enforce_ceo_mutation_honesty",
    "enforce_cloud_web_verify_honesty",
    "enforce_cutoff_closing_honesty",
    "enforce_write_ownership_honesty",
    "is_formal_complete_tier",
    "is_process_dispatch_preamble",
    "mutual_exclusion_rework",
    "note_browser_assembled",
    "note_browser_tool_success",
    "note_browser_tool_success_from_messages",
    "note_cancel_zero_output",
    "note_cloud_web_verify_gap",
    "note_cloud_web_verify_gap_from_delivery",
    "note_cutoff_delivery_gap",
    "note_cutoff_delivery_gap_from_delivery",
    "note_empty_handoff_storm",
    "note_over_seat_reject",
    "note_unresolved_write_ownership",
    "note_unresolved_write_ownership_from_ledger",
    "note_verify_budget_exhausted",
    "note_verify_budget_from_delivery",
    "pre_pause_for_user_visible_continuity",
    "reconcile_resume_closing",
    "reconcile_unresolved_write_ownership_latch",
    "reset_turn_scoped_closing_state",
    "resume_continuity_steer",
    "rewrite_stale_ask_after_dispatch",
    "run_ids_for_write_ownership_scan",
    "tier_forbids_posture_a",
    "turn_browser_assembled",
    "turn_has_browser_tool_success",
    "turn_has_cancel_zero_output",
    "turn_has_cloud_web_verify_gap",
    "turn_has_cutoff_delivery_gap",
    "turn_has_empty_handoff_storm",
    "turn_has_over_seat_reject",
    "turn_has_product_write_evidence",
    "turn_has_unresolved_write_ownership",
    "turn_has_verify_budget_exhausted",
]
