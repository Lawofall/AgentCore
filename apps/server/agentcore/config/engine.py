"""ReAct engine loop: timeouts, governance, tool-clear, finish-guard."""

from pydantic import BaseModel


class EngineSettings(BaseModel):
    tool_default_timeout_seconds: float = 60.0
    tool_execution_timeout_seconds: float = 90.0

    engine_tool_failure_warn: int = 2
    engine_tool_failure_disable: int = 3
    engine_unproductive_threshold: int = 3
    # Consecutive investigation-only rounds re-reading the same targets before finalize.
    engine_convergence_spin_rounds: int = 3
    engine_finish_guard_max_reworks: int = 2
    # C2 概览契约：本回合已发 delivery_status 时，CEO 终稿超过此字数 → finish_guard
    # 影子观测（hit=overview_length），不回炉。细节在终稿路径与 run 详情。
    # ≤0 关闭探测。无交付卡的 prose 回合不设顶。默认值勿当「未复述 UI」硬闸。
    engine_ceo_overview_max_chars: int = 1000

    # Captain (CEO) optional ReAct round fuse. Product default 0 = do not raise
    # (chat/agent profiles already have no product round cap). Ops may set >0
    # to restore an explicit captain ceiling; 0 = inherit profile unchanged.
    engine_captain_max_rounds: int = 0

    # 委派并发预算：ONE knob for (a) root CEO fan-out ContextVar shares（分而不乘 —
    # see runs/concurrency.py）and (b) a single WaveScheduler's dispatch width
    # (runs/wave.py). Nested delegate (depth≥1) reseeds this full value per sub-team
    # (各嵌套满额；spawn 仍受 MAX_WORKER_SUBDELEGATIONS). Overflow queues (bounds
    # latency, not team size). 12 非上游硬限——F9 中转限速已实测通过（2026-07-20
    # 运营方确认无虞），余量收紧或想再放宽时直接改此值（内测计费翻转后上游走合作
    # 中转，旧「本机是唯一约束、远低于 DeepSeek 单账号并发」的标定已过时）。runs
    # 包读它走延迟解析（settings 不可用时回落到
    # runs/constants.py::MAX_PARALLEL_DELEGATIONS，值同步为 12）。
    engine_max_parallel_delegations: int = 12

    # Retired sliding-clear knobs (not on ``build_request_window``). Settings
    # remain so library projections / tests stay byte-stable. Same-window rewrite
    # is window compact only.
    engine_tool_clear_keep_recent: int = 2
    engine_tool_clear_min_chars: int = 2000
    # host / terminal 独立 keep-recent（library projection only; 不进热路径）。
    engine_tool_clear_exec_keep_recent: int = 1
    # 写参 keep-recent（library projection only; 不进热路径）。
    engine_write_args_clear_keep_recent: int = 1
    # R1: when clearing a large read result, append a deterministic structural
    # digest (chars). 0 = pointer-only rollback (no summary). Must keep
    # pointer+summary strictly below engine_tool_clear_min_chars (idempotency).
    engine_tool_clear_file_read_summary_max_chars: int = 1200

    # Worker mid-run window compact (lossy summary of older ReAct rounds).
    # Orthogonal to conversation compaction (cross-user-turn chat). Journal / UI
    # stay full; projection only. Captain / solo loops ignore this. 64k last-prompt
    # is rot, not 1M overflow. Recency at the fold cut is a token budget: always
    # keep the newest round; keep two when both fit. Round count 2 is not a hard keep.
    engine_window_compact_enabled: bool = True
    engine_window_compact_prompt_tokens: int = 64_000
    engine_window_compact_recency_token_budget: int = 24_000
    engine_window_compact_min_fold_rounds: int = 4
    engine_window_compact_trigger_fold_rounds: int = 8
    engine_window_compact_max_fold_rounds: int = 12
    engine_window_compact_summary_char_budget: int = 4_000
    engine_window_compact_near_ratio: float = 0.8
    engine_window_compact_near_tokens: int = 200_000
    engine_window_compact_cooldown_rounds: int = 2

    # Worker 累计 token 硬顶 (loose backstop): window compact 挑大梁做
    # 上下文瘦身,这只是防失控的安全阀。每轮末比对 ``TokenUsage.fuse_tokens``
    # （新读入 + 新写出；缓存前言不计），到顶即收口。
    # 经 ``apply_worker_budgets`` 统一回填到各 worker；CEO 显式 ``token_ceiling`` 优先。
    # ≤0 关闭 (CEO/solo 路径不传此上限,保持 0)。
    engine_worker_token_ceiling: int = 8_000_000
    # 用户回合 turn 级累计硬顶（CEO + 全树 worker，含续派）：按
    # ``TokenUsage.fuse_tokens``（新读入 + 新写出；缓存前言不计）。触顶后禁新
    # delegate / debate / 新波派发，在飞跑完不 cancel。与 per-worker 顶正交。≤0 关闭。
    # 默认 30M 保险丝：全队 backstop（约数个工人保险丝之和），不按嵌套预占倒推。
    engine_turn_token_ceiling: int = 30_000_000
    # 预算收尾窗口：累计 fuse token ≥ ceiling − reserve 时强制进入落盘/handoff-only 轮，
    # 降低硬顶后 degraded_synth。收尾需要的空间是绝对量（写码一轮提示常见十几万），
    # 不该随 ceiling 缩放；过薄则硬切空交接。
    # ≤0 关闭软窗（只留硬顶）；reserve ≥ ceiling 视为病态配置，同样关闭软窗。
    engine_worker_token_wind_down_reserve: int = 200_000
    # 墙钟超时两段式（仅 CEO 显式 timeout_ms 武装后生效；无产品默认工人寿命墙钟）：
    # 先在 threshold × ratio 处警告 worker「限一轮内交接」，再到硬阈值才向 CEO
    # 发 TIMEOUT 通知（仍不自动取消）。须 ∈ (0, 1)；≤0 关闭预警。
    engine_worker_timeout_warn_ratio: float = 0.75

    # 流式停滞闸 (卡死根因): a per-chunk IDLE ceiling for one streamed LLM round — the
    # deadline resets on every chunk, so a healthy long generation (which keeps streaming
    # reasoning/content) is never cut, but a genuine STALL (no bytes for this many seconds)
    # fails FAST and OBSERVABLY instead of riding the provider's silent 120s×3 read-timeout
    # ladder (~6 min of a frozen turn). Sized BELOW the httpx per-read 120s so this fires
    # first (and logs ``llm.stream_stalled``), and ABOVE worst-case time-to-first-token on a
    # large prompt so a big post-debate finalization call is not false-killed. 0 disables it.
    engine_llm_stream_idle_timeout_seconds: float = 100.0
    # force_finalize 绝对墙钟（秒）：硬顶/收敛收尾 LLM 流的独立上限。与 idle 闸正交——idle
    # 按 chunk 重置，长流可无限拖；本墙钟不重置，超时走既有 salvage（保留 prior 交付）。
    # 默认 120：保留防挂死墙，给大上下文健康长收尾留余地（巡检 60s 误砍偏多）。≤0 关闭。
    # 不改 mark_llm_inflight 暂停语义。
    engine_force_finalize_wall_seconds: float = 120.0

    observability_span_export_enabled: bool = True
