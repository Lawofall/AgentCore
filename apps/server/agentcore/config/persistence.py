"""Turn persistence: roster, compaction."""

from pydantic import BaseModel


class PersistenceSettings(BaseModel):
    incomplete_turn_persist_enabled: bool = True

    session_roster_persist_enabled: bool = True
    # 现场保留语义「对话在，现场就在」（同人连续委派，2026-07-12 拍板）：删对话级联清理是
    # 唯一默认清理路径；按时长清扫默认关（0 = 不清），>0 仅作放量后的存储保护兜底。
    session_roster_retention_days: int = 0
    session_roster_sweep_interval_seconds: int = 6 * 3600
    session_roster_sweep_batch_limit: int = 200

    audit_retention_days: int = 90
    audit_retention_sweep_interval_seconds: int = 24 * 3600
    audit_retention_sweep_batch_limit: int = 500

    # Write-side always-entry quota (闸在写侧，读侧全量). Caps the sum of frontmatter-stripped
    # always rule bodies in an injection context (global + optional project). Anchored to the
    # retired read-side ``max_instruction_chars`` (24_000) so behaviour does not jump. 0 = off.
    # Sole bound on the always pool — no read-side per-file char cap.
    memory_always_max_chars: int = 24_000

    # Dual-trigger compaction (长对话压缩定案 P0): schedule when token≥threshold OR
    # DB watermark-after batch passes ``_select_fold`` with message_trigger_min_fold.
    # Do NOT use turn ``history_len`` (summary block inflates it → false due).
    # Internal ``compaction_min_fold_messages`` stays 4 (skip trivial LLM spend); decoupled
    # from the message-side schedule trigger (16) so we do not re-fold every ~2 user turns.
    compaction_enabled: bool = True
    compaction_trigger_input_tokens: int = 32_000
    # Fold retain is a token budget packed from the newest user-led turn, not a
    # message count. ``compaction_recency_messages`` is leftover load padding only.
    compaction_recency_token_budget: int = 24_000
    compaction_recency_messages: int = 12
    compaction_message_trigger_min_fold: int = 16
    compaction_min_fold_messages: int = 4
    compaction_max_fold_messages: int = 200
    compaction_context_max_messages: int = 300
    compaction_summary_char_budget: int = 4_000
    # After a failed compact (LLM skip / empty / timeout / exception), both token and
    # message triggers refuse to schedule until this cooldown elapses. 0 = no cooldown.
    # In-process only (same posture as ``_inflight``); multi-worker skew is acceptable.
    compaction_failure_cooldown_seconds: int = 90
    # Near model-window pre-turn compact (定案⑦A / aa519): when last-turn input_tokens
    # reach this fraction of the resolved model ``context_length``, await fold(s)
    # BEFORE assembling the next turn so the turn sees the new summary — do not wait
    # for the user to type /compact. Distinct from post-turn fire-and-forget at
    # ``compaction_trigger_input_tokens``. Absolute floor applies when metadata has
    # no context_length.
    compaction_near_context_ratio: float = 0.8
    compaction_near_context_tokens: int = 200_000
    compaction_near_max_passes: int = 3
    # Lifespan flush of in-flight folds. Fold is best-effort; do not hold the
    # Docker stop window for a wedged LLM call.
    compaction_shutdown_seconds: float = 2.0
