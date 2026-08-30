"""Turn persistence: roster, memory consolidation, compaction."""

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

    memory_consolidation_enabled: bool = True
    # Episodic layer: idle debounce / turn-cap still trigger a per-conversation session
    # summary write (not a direct semantic write). Names kept for existing env overrides.
    memory_consolidation_idle_seconds: float = 90.0
    memory_consolidation_turn_cap: int = 8
    memory_consolidation_window_messages: int = 40
    memory_consolidation_sweep_interval_seconds: int = 300
    memory_consolidation_sweep_batch_limit: int = 100
    # After a retryable consolidation failure that is conversation-local (not shared
    # upstream), the sweeper / live debounce skip that conversation until this
    # cooldown elapses. 0 = no per-conversation cooldown. In-process only (same
    # posture as compaction_failure_cooldown_seconds); multi-worker skew is OK.
    memory_consolidation_failure_cooldown_seconds: int = 600
    # Shared-upstream failures (rate limit / quota / upstream unavailable): abort the
    # rest of the current sweep batch and refuse new consolidations until cooldown
    # elapses. Base grows exponentially with consecutive shared failures, capped at
    # max — expiry is the recovery path (never permanent). 0 base or max = disabled.
    memory_consolidation_shared_failure_cooldown_base_seconds: int = 300
    memory_consolidation_shared_failure_cooldown_max_seconds: int = 1800
    # Episodic session summary hard cap (chars); LLM output is truncated to this.
    memory_episodic_summary_max_chars: int = 200
    # Digested episodes have no reader left (semantic already merged them); the sweeper
    # hard-deletes them past this window. 0 disables the purge (keeps them forever).
    memory_episode_retention_days: int = 30
    # Semantic consolidation: live path after each episode write is eager (bypasses
    # these). The numbers below are the non-eager leak-scan backstop (undigested
    # count, or hours since the last successful semantic pass for that (user, scope)).
    memory_semantic_min_episodes: int = 3
    memory_semantic_max_age_hours: float = 24.0
    memory_section_bullet_cap: int = 20
    # Max on-demand topic notes (主题/<slug>.md) per user; new ones beyond this are
    # dropped by the consolidation pass (anti-bloat backstop, 记忆文件夹化 §七).
    memory_max_topic_files: int = 24
    # R1 fingerprint-dirty explore refresh (旁路): per-folder debounce; never blocks the turn.
    memory_explore_refresh_enabled: bool = True
    memory_explore_refresh_idle_seconds: float = 45.0
    # Write-side always-entry quota (闸在写侧，读侧全量). Caps the sum of frontmatter-stripped
    # always rule bodies in an injection context (global + optional project). Anchored to the
    # retired read-side ``max_instruction_chars`` (24_000) so behaviour does not jump. 0 = off.
    # Sole bound on the always pool — no read-side per-file char cap (COST-001 read-side
    # backstop retired with 读侧全量定案).
    memory_always_max_chars: int = 24_000

    # CEO-only derived folder catalog (跨文件夹找文件夹): max Folder rows injected as
    # path＋画像首句. Orthogonal to always-on ``<设定>`` (read side injects all always
    # entries; write-side quota owns 常驻满了). 0 = disable catalog injection.
    folder_catalog_max_entries: int = 12

    # One-time file→document memory migration (§5.7 换底): copy file-backed memory into the
    # documents tree at startup. Idempotent + best-effort; safe to leave on (a no-op once done).
    memory_documents_migration_enabled: bool = True

    # Dual-trigger compaction (长对话压缩定案 P0): schedule when token≥threshold OR
    # DB watermark-after batch passes ``_select_fold`` with message_trigger_min_fold.
    # Do NOT use turn ``history_len`` (summary block inflates it → false due).
    # Internal ``compaction_min_fold_messages`` stays 4 (skip trivial LLM spend); decoupled
    # from the message-side schedule trigger (16) so we do not re-fold every ~2 user turns.
    compaction_enabled: bool = True
    compaction_trigger_input_tokens: int = 32_000
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

    # Standing tasks / 定时自动化 L1: in-process DB poll of next_run_at + lease.
    standing_task_scheduler_enabled: bool = True
    standing_task_poll_interval_seconds: int = 30
    standing_task_poll_batch_limit: int = 10
    standing_task_lease_seconds: int = 30 * 60
    # L2a webhook: per-task sliding window + optional idempotency key TTL.
    standing_task_webhook_rate_limit_max: int = 30
    standing_task_webhook_rate_limit_window_seconds: int = 60
    standing_task_webhook_idempotency_ttl_seconds: int = 3600

    # Assembled system-prompt budget (项目审计-成本性能专项 COST-004). Observe-only today:
    # ``cost.prompt_assembled`` logs per-section chars, ``assembly_hash``, and whether the
    # turn's CEO system prompt exceeds this soft cap, to gather data (无真实数据期 → 先观测,
    # 后开「仅裁易变尾」软闸). ~120k chars ≈ 数万 token, far below DeepSeek's 1M window but
    # enough to flag abnormal bloat.
    prompt_budget_char_soft_cap: int = 120_000
