"""Run/turn durability + telemetry models.

HandoffJob (本地→云交接), RunSessionRow (recoverable worker runs), PausedTurnRow
(结构化挂起 durable resume) + PausedTurnOutcomeRow (谁把那张卡结了 / 怎么结的),
TurnJournalRow (§8.3 唯一事实源), TurnMetricsRow (运营观测 telemetry), TurnLeaseRow
(durable RUNNING ownership for crash recover), TurnStreamStateRow (流式在飞通道快照 ·
流式回复持久化 §3.1).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid

# --- Handoff Jobs (本地→云交接: 云端在快照上跑团队, 双模式工作区 P2e / e2) ---
# A dispatched cloud run seeded from a local-mode conversation's snapshot. The
# user hands a task off from their local workspace; the server restores the
# uploaded snapshot into a fresh server-side workspace, runs the Agent team there
# (autonomously — no live client, so server-sandbox isolated and un-gated), then
# snapshots the result. The team's messages / cost / run journal persist under a
# dedicated hidden ``mode="handoff"`` conversation (filtered from the sidebar), so
# the run replays by opening it. e3 then diffs result vs base back to local files.
# Not a standing/workflows job twin: thin credentials, no pause / paused_turns.
#
# Cloud-replica reclaim (§7.6 按任务临时、结束可收): ``succeeded`` = 可合回 (Diff
# / apply still open); ``applied`` = 已合回; ``discarded`` = 已丢弃. Apply or
# discard soft-deletes the job host so retention can purge it — we do **not**
# pretend the replica is gone the instant the run finishes, and we never early-
# delete an open job (Diff must stay usable for ``workspace_retention_days``).


class HandoffJob(Base):
    __tablename__ = "handoff_jobs"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed', 'applied', 'discarded')",
            name="ck_handoff_jobs_status",
        ),
        # A source conversation's job list (newest first) is the only list query;
        # the composite index also serves prefix lookups by source_conversation_id.
        Index(
            "ix_handoff_jobs_source_created",
            "source_conversation_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # The local-mode conversation that dispatched this handoff: its workspace is
    # the source of truth the base snapshot was taken from. App-level FK.
    source_conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    # The hidden cloud conversation hosting the team run: its workspace is the
    # restored snapshot; its messages/cost/runs make the run replayable.
    job_conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    # Snapshot of the user's local files the cloud team runs on (the e3 diff base),
    # stored under the *source* conversation's storage key.
    base_snapshot_id: Mapped[str] = mapped_column(String(100))
    # Snapshot of the team's result, under the *job* conversation's storage key;
    # NULL until the run succeeds.
    result_snapshot_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Recoverable worker runs (留人 跨进程落盘) ---
# The in-memory roster (runtime/sessions.py) keeps a finished worker alive within a
# process so the CEO can 带现场续派 (delegate continue_from_run_id) it; this table is
# the durable backstop so "让刚才那个人接着干" still hits after a restart or memory
# eviction. A continuation loads the row on an in-memory miss, continues the run, and
# writes the extended transcript back. Lifecycle =「对话在，现场就在」: cascaded away
# with conversation delete (ConversationRepository), NOT time-pruned by default (the
# idle sweeper in runtime/session_retention.py only runs when retention_days > 0, as
# a post-scale storage backstop) — independent of the turn_journal graph-replay facts
# (different lifecycle, 见 docs/03-AI核心/多轮编排与同人续派.md).


class RunSessionRow(Base):
    __tablename__ = "run_sessions"
    __table_args__ = (
        # TTL sweep scans by last-touch: delete rows whose updated_at < cutoff.
        Index("ix_run_sessions_updated", "updated_at"),
    )

    # The worker's namespaced run id (e.g. ``del_<uuid>_1`` / ``<run>_rev2``) — a
    # plain string, NOT a UUID, so it is the PK directly (globally unique per turn).
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # The source RunSpec (role / model tier / allowed tools / contract) as JSON, so a
    # cross-process continuation runs as the same author under the same policy.
    spec: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # The worker's full, replayable message transcript (list of LLMMessage dicts).
    transcript: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # The latest answer, mirrored for quick display without rehydrating the spec.
    content: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # 改次闸 counter, persisted so the revise cap holds across processes.
    recall_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # Originating turn's log trace_id, set on first persist and NOT overwritten on a
    # later revise (a revise is a new turn) — links a recoverable worker back to the
    # interaction that spawned it. NULL when untraced. See core/log_context.py.
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


# --- Paused turns (结构化挂起 durable resume: turn 级落盘 + POST .../resume) ---
# A turn that suspended at a plan_review checkpoint, persisted so it SURVIVES a
# process restart / client disconnect — without it the whole turn (an in-memory
# asyncio task + its completed workers) is lost. One row per paused assistant
# turn, keyed by the pipeline's ``message_id``. The ``frame`` JSONB holds the full
# resumable snapshot (the delegate RunPlan with its minted ids, the completed
# workers' RunStates as seed_completed, the CEO context to rebuild the loop, and
# the journal-so-far for graph replay) — see runtime/suspension.py. The row is the
# AUTHORITATIVE pending state ONLY when no live in-process interaction exists (a
# live SSE turn settles via the interaction bridge instead); ``POST .../resume``
# claims-and-deletes it to continue on a fresh process. Deleted on resume / a
# live in-process resolve / timeout. Its journal-so-far is NOT stored here — it
# lives in the turn_journal fact stream (唯一事实源, §8.3): the pause mirrors it
# there and the resume re-hydrates from it, so the frame holds only resume control
# state (plan / seed_completed / CEO context / pending payload).


class PausedTurnRow(Base):
    __tablename__ = "paused_turns"
    __table_args__ = (
        # A conversation's pending paused turns (resume lookup on reopen).
        Index("ix_paused_turns_conversation", "conversation_id"),
        # TTL sweep scans by last-touch: delete rows whose updated_at < cutoff.
        Index("ix_paused_turns_updated", "updated_at"),
    )

    # The paused turn's assistant ``message_id`` (== the pipeline's minted id), so a
    # resume reuses the same id when it finally persists the assistant message.
    message_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    # No column-level index=True: the conversation lookup is served by the explicit
    # ix_paused_turns_conversation in __table_args__ above; a second auto-named index
    # (ix_paused_turns_conversation_id) would drift from the migration.
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # The resumable CONTROL snapshot (runtime/suspension.py TurnSuspension): plan +
    # seed_completed + CEO context + pending checkpoint payload. The journal-so-far is
    # NOT here — it rides turn_journal (唯一事实源, §8.3), re-hydrated on claim.
    frame: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # Originating turn's log trace_id, so the resumed continuation joins back to the
    # interaction that spawned it. NULL when untraced. See core/log_context.py.
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


# A paused frame's TERMINAL disposition. ``paused_turns`` says a card is still
# waiting; this says what ended it — and it is written by whoever atomically consumed
# the frame, in the same transaction that consumed it.
PAUSED_TURN_SETTLED = "settled"
PAUSED_TURN_EXPIRED = "expired"


class PausedTurnOutcomeRow(Base):
    """What ended a paused turn's card — written by the party that consumed the frame.

    ``claim`` is DELETE ... RETURNING, so exactly one caller wins a paused frame. The
    winner used to leave nothing behind but the hole, and every other caller had to
    infer「谁结的这张卡」from the last ``*_resolved`` in ``turn_journal`` — which, on
    the claim-race path, is usually the loser's OWN prewrite. This row is the winner's
    conclusion, stamped inside the winning transaction: the decision it applied, when,
    on which ``checkpoint_id``, and who settled it. A loser reads it instead of guessing.

    The TTL sweep stamps the other terminal disposition (``expired``) the same way, so
    「遗弃超期」 and 「回合已重新生成」 are told apart by this column rather than by
    whether an assistant row happens to still exist.

    Frame ⊕ outcome: a ``paused_turns`` row and its outcome never coexist. Saving /
    restoring a frame clears the outcome (the card is pending again); consuming a frame
    writes one. Absent row + absent frame ⇒ the turn was regenerated / deleted (its
    outcome went with the message — app-level cascade in ``MessageRepository``).
    """

    __tablename__ = "paused_turn_outcomes"
    __table_args__ = (
        # Only a settled card's ``checkpoint_id`` ever reaches a client (the
        # ``resume_settled`` frame keys the card on it, and an empty one would be
        # discarded whole). An ``expired`` row carries no id to the wire, so the
        # constraint stays off the TTL sweep's back — one malformed legacy frame must
        # not be able to wedge the sweep forever.
        CheckConstraint(
            f"outcome <> '{PAUSED_TURN_SETTLED}' OR checkpoint_id <> ''",
            name="ck_paused_turn_outcomes_settled_checkpoint",
        ),
        # Kept for conversation-scoped reads (IDOR-safe lookup pairs it with the PK).
        Index("ix_paused_turn_outcomes_conversation", "conversation_id"),
    )

    # The paused turn's assistant ``message_id`` — same key as the frame it replaces.
    message_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    # PAUSED_TURN_SETTLED (someone continued the turn) | PAUSED_TURN_EXPIRED (TTL swept).
    outcome: Mapped[str] = mapped_column(String(16))
    # The suspension kind the card was (ask_user / plan_review), taken
    # off the consumed frame — the wire ``resume_settled.kind``.
    card_kind: Mapped[str] = mapped_column(String(32), server_default=text("''"))
    # The consumed frame's interaction id. Never blank on a settled row (see the check).
    checkpoint_id: Mapped[str] = mapped_column(String(64), server_default=text("''"))
    # The decision the winner actually applied (continue / stop / adjust / …). Empty
    # for an expired card — nobody decided it.
    decision: Mapped[str] = mapped_column(String(32), server_default=text("''"))
    # 结算方: the origin device that settled it, or a server-side actor label for a
    # settlement no device drove (TTL sweep). Empty when the caller had no device.
    settled_by: Mapped[str] = mapped_column(String(64), server_default=text("''"))
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class TurnLeaseRow(Base):
    """Durable RUNNING lease for an in-flight turn (crash recover).

    Journal remains the唯一事实源; this row only records owner + heartbeat + phase so
    a dead process can be swept and ``recover_turn`` can redrive unfinished work.
    Cleared on terminal finish / durable pause / explicit stop.
    """

    __tablename__ = "turn_leases"
    __table_args__ = (
        Index("ix_turn_leases_conversation", "conversation_id"),
        Index("ix_turn_leases_heartbeat", "heartbeat_at"),
    )

    message_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    owner_id: Mapped[str] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String(40), server_default=text("'running'"))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


class TurnStreamStateRow(Base):
    """In-flight stream-channel snapshot (流式回复持久化 §3.1).

    One row per ``(turn_id, channel)`` holding the latest accumulated text for a live
    stream channel (``captain:content`` / ``captain:reasoning`` /
    ``run:{run_id}:output`` / ``run:{run_id}:reasoning``). UPSERT projection — not a
    second fact source; deleted after finalize / salvage / pause once the terminal
    snapshot is written. Same-generation text is monotonic; a higher ``generation``
    (content_reset / run_output_reset) may clear and restart.
    """

    __tablename__ = "turn_stream_state"
    __table_args__ = (
        # TTL sweep (mirror paused_turns 7d) scans by last-touch —
        # ``stream_state_retention_loop``; 0 days disables.
        Index("ix_turn_stream_state_updated", "updated_at"),
    )

    turn_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    # Channel key — see module docstring. Not a UUID; length covers run-scoped ids.
    channel: Mapped[str] = mapped_column(String(128), primary_key=True)
    # ``text`` shadows sqlalchemy.text once assigned — keep server_default=text(...) above it.
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
    text: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))


# Occupancy bands for ``turn_journal`` PK ``(turn_id, band, seq)``. Live is the
# rewritable dense prefix; overflow is the post-seal second channel. db filters
# by these literals — never by a runtime seq-split constant.
JOURNAL_BAND_LIVE = "live"
JOURNAL_BAND_OVERFLOW = "overflow"


class TurnJournalRow(Base):
    """One fact of a turn's append-only execution journal (§8.3 Turn Journal · 唯一事实源).

    A turn's ordered execution facts (run/tool/interaction events for a multi-agent
    turn, or reasoning/tool 步 for a single-agent turn, plus a closing ``turn_end``)
    are stored here — one row per fact, keyed by ``(turn_id, band, seq)`` where
    ``turn_id`` == the assistant ``message_id`` and ``band`` is ``live`` (rewritable
    prefix occupancy) or ``overflow`` (post-seal terminals). This REPLACES the old
    ``messages.runs`` JSON blob: the journal is the single durable source of truth,
    and the assistant message's replay payload (``MessageDetail.runs``) is PROJECTED
    from these rows on read (see ``agentcore.runtime.journal``). A plain single-agent
    chat (nothing to replay) writes no rows. ``Journal.record`` replaces the live-band
    prefix occupancy; overflow-band rows stay. Read order is emission order
    (``created_at``, then band-local ``seq``), not live-then-overflow by seq.

    **Lifecycle** (no DB FK — app-level cascade, per repo convention): cleaned with
    its owning message/conversation on hard-delete (``MessageRepository.delete_by_id``
    / ``delete_after``, ``ConversationRepository.hard_delete``) and by the paused-turn
    TTL sweep for an abandoned pause (``PausedTurnRepository.delete_stale``). A paused
    turn writes rows before any message exists (hence no FK to ``messages``).
    """

    __tablename__ = "turn_journal"
    __table_args__ = (
        CheckConstraint(
            "band in ('live', 'overflow')",
            name="ck_turn_journal_band",
        ),
        # A conversation's facts, e.g. for future cross-turn projections / sweeps.
        Index("ix_turn_journal_conversation", "conversation_id"),
    )

    # The owning turn == the assistant message id (the pipeline's minted id), so the
    # projected replay rejoins its message without a separate key.
    turn_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    # Occupancy band: live prefix vs post-seal overflow. Composite PK with turn_id+seq.
    band: Mapped[str] = mapped_column(
        String(8), primary_key=True, server_default=text("'live'")
    )
    # Monotonic position within the band (occupancy); not a cross-band emission index.
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The fact kind: an SSE event type (run_plan / tool_use_start / checkpoint_* …),
    # a single-agent process step (process_reasoning / process_tool), or turn_end.
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # The fact's own emission timestamp (the SSE event's), preserved so the projected
    # replay keeps the original ordering metadata. NULL for derived rows (process / end).
    ts: Mapped[str | None] = mapped_column(String(40), nullable=True)
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    # Originating turn's log trace_id (DB↔logs join), stamped on every fact. See
    # core/log_context.py. NULL when untraced.
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


# --- Turn metrics (运营观测: per-turn telemetry for the admin 观测看板) ---
# One row per COMPLETED assistant turn — the operator-facing counterpart of the
# dev firehose (logs/dev.jsonl). It persists the same outcome/quality fields the
# turn already logs at chat.turn_complete / chat.resume_complete (status /
# finish_reason / rounds / duration / delegated / workers / tokens), so the admin
# 观测 dashboard aggregates them with indexed SQL instead of scanning the JSONL
# file — which prod's stdout-only logging posture (settings.log_file default "")
# may never even write. Written best-effort at the turn's persistence tail: a
# telemetry write must NEVER break the user's turn (同 cost ledger / 工作区快照 铁律).
#
# Deliberately compact + non-duplicative: money lives in cost_events and the
# message body in messages — both join here by trace_id (the one-per-interaction
# key), so a 会话复盘 stitches the three by trace_id/conversation_id without this
# row copying spend or text. Distinct from turn_journal (the replay event stream,
# keyed by message_id): that is for client replay, this is the aggregatable
# outcome row for ops. Tool/LLM span-level detail is intentionally NOT here (it
# stays a dev concern in the log file); add a span_metrics table only if ops需要.


class TurnMetricsRow(Base):
    """Per-turn 运营观测 telemetry (one row per completed assistant turn).

    Powers the admin 观测看板 (全站健康: error rate / latency / rounds / 委派率 +
    7-day trend) and, with messages + cost_events joined by trace_id, the 会话复盘
    timeline. See the module note above for why it is a purpose-built DB sink
    rather than the log file.
    """

    __tablename__ = "turn_metrics"
    __table_args__ = (
        CheckConstraint(
            "status in ('ok', 'partial', 'paused', 'error')",
            name="ck_turn_metrics_status",
        ),
        CheckConstraint("kind in ('turn', 'resume')", name="ck_turn_metrics_kind"),
        CheckConstraint("mode in ('cloud', 'local')", name="ck_turn_metrics_mode"),
        # 全站健康看板: window aggregates + the daily trend filter/group on created_at.
        Index("ix_turn_metrics_created", "created_at"),
        # 会话复盘 (P2): every turn of one conversation, newest-first.
        Index("ix_turn_metrics_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    # mode=cloud: ``attempt_id`` minted at ``chat.turn_start`` (core.types.new_id);
    # log correlation handle (log_context.attempt_id). Resume mints a fresh one.
    # Not the assistant message_id (that is the journal's key).
    # mode=local: sidecar write-back has no attempt_id (not on RecordTurnRequest).
    # Stores assistant message_id — the only UUID local finalize holds. Do not
    # invent one. Consumers must branch on ``mode``: join local rows to
    # messages/journal by this value (≡ message_id) or by ``trace_id``; do not
    # join local ``turn_id`` to JSONL ``attempt_id``.
    turn_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    conversation_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # The turn's top agent (the CEO/captain); delegated members are counted in `workers`.
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Join key to the runtime logs + cost_events + messages (one per interaction).
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # turn = fresh send / regenerate; resume = 结构化挂起 continuation.
    kind: Mapped[str] = mapped_column(String(16), default="turn", server_default=text("'turn'"))
    # Engine location — same fork as ``CloudStore.finalize(mode="cloud"|"local")``.
    # Not ``chat.turn_start.location`` (workspace on user disk vs server) and not
    # ``via`` (``cloud`` / ``sidecar``); the three are orthogonal. Old rows are
    # cloud: local turns did not write this table until after this column landed.
    mode: Mapped[str] = mapped_column(
        String(16), default="cloud", server_default=text("'cloud'")
    )
    # ok | partial | paused | error. ``paused`` is written by the CEO-continue pause
    # (cloud) and by the local settle; it is no longer a reserved value.
    status: Mapped[str] = mapped_column(String(8), default="ok", server_default=text("'ok'"))
    # The turn's terminal finish_reason (FinishReason value), e.g. stop / length / error.
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # A soft error surfaced in the turn result (truncated); NULL on success.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    rounds: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # Both modes: ``turn_worker_stats`` (completed member workers = cost_runs
    # role=member ∪ journal message_final phase=completed). Not team_batch
    # kickoff roster. Local write-back has no cost_runs — journal half only.
    delegated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    workers: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # 协作质量 (学·度量, docs/05-平台与运维/管理员后台.md §四): per-turn orchestration signals,
    # the operator面 counterpart of the offline log_stats 方向盘. ``boundary_yields`` = 受监督边界
    # 让出次数 (首计划存活率 = delegated turns whose boundary_yields==0); ``scope_signals`` =
    # escalate kind=scope count (漂移率); ``revises`` = 定向唤回 次数 (返工率 的一半; contract
    # 重试 stays a dev-log signal); ``escalations`` = total worker→captain escalations. 空转·早收
    # reads off the existing ``finish_reason`` (no new column). All default 0 — a plain
    # single-agent turn writes zeros, unchanged.
    boundary_yields: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    scope_signals: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    revises: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    escalations: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    audit_drops: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
