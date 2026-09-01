"""Coordination session: event queue, budget, timeouts, and durable snapshot.

Root-CEO only (Phase 2+). Lead nesting stays on the blocking path.

Types live in ``session_types``; worker registry / cancel / verify in
``session_workers``; timeouts in ``session_timeout``; queue wait/close in
``session_queue``; telemetry budget in ``session_budget``; durable snapshot
and terminal settlement in ``session_snapshot``. Public import path stays
this module.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.coordination.session_budget import SessionBudgetMixin
from agentcore.runtime.coordination.session_queue import SessionQueueMixin
from agentcore.runtime.coordination.session_snapshot import SessionSnapshotMixin
from agentcore.runtime.coordination.session_timeout import SessionTimeoutMixin
from agentcore.runtime.coordination.session_types import (
    _INTERJECTION_SNAPSHOT_KEYS,  # noqa: F401 — public re-export
    _MIN_DECISION_BUDGET,  # noqa: F401 — public re-export
    DEFAULT_COORDINATION_BUDGET,  # noqa: F401 — public re-export
    DEFAULT_DECISION_BUDGET,
    DEFAULT_PROGRESS_BUDGET,
    MAX_COORDINATION_BUDGET,  # noqa: F401 — public re-export
    MAX_DECISION_BUDGET,  # noqa: F401 — public re-export
    MAX_PROGRESS_BUDGET,  # noqa: F401 — public re-export
    CancelResolution,  # noqa: F401 — public re-export
    CoordinationEvent,
    CoordinationEventKind,  # noqa: F401 — public re-export
    CoordinationSnapshot,  # noqa: F401 — public re-export
    _budget_pools_from_dict,  # noqa: F401 — public re-export
    _durable_terminal_run_ids,  # noqa: F401 — public re-export
    _WorkerSpend,
    coordination_budget_for_batch,  # noqa: F401 — public re-export
    should_enter_coordination,  # noqa: F401 — public re-export
    split_coordination_budget,  # noqa: F401 — public re-export
)
from agentcore.runtime.coordination.session_workers import SessionWorkersMixin

logger = get_logger(__name__)


# Registry keyed by root-turn ``execution_id`` (captain + all workers share one id).
# Module-level dict — not a ContextVar holding the session — because ``execute_tools``
# runs each tool under ``asyncio.gather``, which copies the context: a session set
# inside ``delegate`` would be invisible to the parent CEO ``react_loop``. The dict
# is a shared object visible across gather; the key isolates concurrent turns.
# ``current_execution_id`` is set at turn entry (before gather) so the captain wait
# path can resolve without threading execution_id through the whole loop.
_sessions: dict[str, CoordinationSession] = {}
# Reverse index: conversation_id → execution_id for mid-flight message routing
# (POST …/messages while a coordination turn is live).
_by_conversation: dict[str, str] = {}
current_execution_id: ContextVar[str | None] = ContextVar("current_execution_id", default=None)


@dataclass
class CoordinationSession(
    SessionWorkersMixin,
    SessionTimeoutMixin,
    SessionQueueMixin,
    SessionBudgetMixin,
    SessionSnapshotMixin,
):
    """In-process coordination state for one non-blocking delegate batch."""

    execution_id: str
    total_workers: int
    # 两池遥测计数（同一总额切成两本账，不扩容、不闸唤醒）：
    # - 进度池：例行进展（worker 完成）唤醒计数。
    # - 决策池：必要决策（终局 / 升级 / 冲突 / 插话 / 单员超时 / 边界 / 派批）唤醒计数。
    progress_budget_remaining: int = DEFAULT_PROGRESS_BUDGET
    decision_budget_remaining: int = DEFAULT_DECISION_BUDGET
    draft: str = ""
    conversation_id: str = ""
    # C3: session birth desk (conversation folder_id). Ownership keys use
    # ``target_folder_id or birth_desk_id``; process-local (re-armed from tool).
    birth_desk_id: str | None = None
    # Sidecar startTurn stamps local folder bind so settle can rebuild the
    # workspace without ``resolve_local_binding(db)``. Process-local; not snapshotted.
    folder_binding_injected: bool = False
    folder_local_root_id: str | None = None
    folder_local_subpath: str = ""
    completed_run_ids: set[str] = field(default_factory=set)
    # Terminal FAILED run_ids (subset of completed) — pipeline health / idle brief.
    failed_run_ids: set[str] = field(default_factory=set)
    # FAILED / CANCELLED / SKIPPED — seats vacated for auto replaces_run_id fill.
    # Process-local (like failed_run_ids); seed path rehydrates from RunPhase.
    vacated_run_ids: set[str] = field(default_factory=set)
    # CEO progress-inject cursor: completed ids already named in a prior progress
    # block. Not snapshotted — restore seeds it to current completed (no re-dump).
    progress_reported_completed: set[str] = field(default_factory=set)
    cancel_ids: set[str] = field(default_factory=set)
    # CEO cancel_worker successes (close wording). Unsettled leftover-clear and
    # timeout force-cancel do not stamp. Snapshotted; old snapshots missing the
    # key restore as empty.
    ceo_cancel_worker_ids: set[str] = field(default_factory=set)
    ceo_cancel_started_ids: set[str] = field(default_factory=set)
    active: bool = True
    # True after ALL_COMPLETED was injected into a live CEO wait.
    all_completed_injected: bool = False
    # Process-local: captain bubble *currently* has non-empty prose after
    # attached ALL_COMPLETED inject. ``content_delta`` sets it; ``content_reset``
    # clears it. Used to skip an away-user push when the user already saw a close.
    # Not snapshotted.
    attached_inject_visible_close: bool = False
    # Structured user-audience facts (nodes / files / outstanding tool failures)
    # stamped at drive close — inject names accepted paths from this, not a
    # second CEO turn.
    harvest_user_facts: dict[str, Any] | None = None
    # Strong refs for fire-and-forget settle tasks (bare create_task is a weak
    # ref; the loop may destroy a pending task before it runs).
    _settle_tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    # Background WaveScheduler task (owned by drive); None until started.
    drive_task: asyncio.Task[Any] | None = None
    # ask_user soft-stop sets this before cancelling drive_task so the cancel
    # handler skips wake events (no ALL_COMPLETED / DRIVE_CANCELLED in the
    # hang-frame snapshot — resume re-drives unfinished workers from journal).
    soft_stop: bool = False
    # Turn ownership: True while the arming chat turn is still open. Teardown sets
    # False when preserving a live background drive so the drive's finally can
    # close+unregister after workers finish (lifecycle ≠ chat turn).
    turn_attached: bool = True
    # Host turn journal writer bound at arm time — DURABLE display facts keep
    # appending here after the arming turn's ContextVar is reset (pillar A).
    host_journal_writer: Any | None = field(default=None, repr=False)
    # Same-turn fact log as the writer. Post-detach sink persist must keep this
    # in sync so the sidecar finalize snapshot (taken after the drive settles)
    # still contains run terminals that arrived after ContextVar teardown.
    host_fact_log: Any | None = field(default=None, repr=False)
    host_turn_id: str = ""
    # Set by crash redrive (``recover_turn``) to the ORIGINAL turn's message_id.
    # Deliberately NOT snapshotted: a soft-stop/resume hands the turn back to the
    # resume pipeline, which already reuses the original id on its own.
    recovered_turn_id: str = ""
    # Process-local: CEO rate-limit continue pause. Not snapshotted.
    host_turn_paused: bool = False
    # Set when post-drive settlement has been armed (idempotent).
    harvest_scheduled: bool = False
    # Drive posted ALL_COMPLETED / DRIVE_CANCELLED (终态对账前置条件).
    terminal_posted: bool = False
    # True after ``DRIVE_CANCELLED`` was posted (survives inject consuming the event).
    drive_cancelled: bool = False
    # Why ``drive_task`` was cancelled (in-process; not snapshotted).
    drive_cancel_reason: str | None = None
    # 终态收敛路径：attached_inject | detached | user_stop；清空前未收敛 → error 告警.
    settled_via: str | None = None
    # Live RunPlan owned by the active drive — mid-coordination secondary
    # ``delegate`` appends workers here (same graph / same session).
    live_plan: Any | None = field(default=None, repr=False)
    _queue: asyncio.Queue[CoordinationEvent] = field(default_factory=asyncio.Queue, repr=False)
    # Events already drained but not yet consumed by an LLM round (merge buffer).
    _pending: list[CoordinationEvent] = field(default_factory=list, repr=False)
    # Wakes ``wait_events`` when snapshot/drain moves queue items into ``_pending``
    # (otherwise a blocked ``queue.get`` would miss them until timeout).
    _wake: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    # Snapshot compat: first worker_completed seen (no longer a wake trigger).
    _saw_first_completion: bool = False
    # Success / skip / cancel completions held until a necessary wake.
    _deferred_progress: list[CoordinationEvent] = field(default_factory=list, repr=False)
    # Per-worker wall-clock timers (notify-only; never auto-cancel).
    _worker_started_at: dict[str, float] = field(default_factory=dict, repr=False)
    _timeout_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, repr=False)
    # In-flight worker registry (full run_id → role) for cancel_worker short→full
    # resolution. Populated on ``arm_worker_timeout`` (every dispatch), cleared on
    # disarm / completion. NOT snapshotted: resume re-dispatches unfinished workers,
    # which re-arm and re-register — so a completed worker is never resolvable.
    _running_workers: dict[str, str] = field(default_factory=dict, repr=False)
    # Worker busy stamps (run_id → "llm" | "tool" | "verify" | "arbitrate"):
    # - llm/tool: short in-flight work; idle-patrol defers (勿误唤醒).
    # - verify: minute-level bounded verify (test_run); still shown in progress
    #   summary, but does NOT count as has_inflight_work — CEO may patrol /
    #   cancel_worker instead of parking behind wall+0. 不快照。
    # - arbitrate: blocking escalate awaiting CEO; same as verify for inflight
    #   (wait 不得空等该队员). 不快照。
    _busy_workers: dict[str, str] = field(default_factory=dict, repr=False)
    # Live used/limit/tokens last stamped via ``note_coord_worker_busy``.
    # Survives clear_busy (轮间 / verify still need the last known spend);
    # dropped with the running-worker registry. 不快照。
    _worker_spend: dict[str, _WorkerSpend] = field(default_factory=dict, repr=False)
    # Sibling verify coalesce (same execution): fingerprint → inflight Future /
    # completed ToolResult snapshot. Process-local; not snapshotted (resume
    # re-runs unfinished workers). Generation bumps on successful land so a
    # still-running verify cannot re-poison the cache after disk changed. 不快照。
    _verify_inflight: dict[str, asyncio.Future[Any]] = field(
        default_factory=dict, repr=False
    )
    _verify_cache: dict[str, Any] = field(default_factory=dict, repr=False)
    _verify_generation: int = field(default=0, repr=False)
    # Explicit user /stop cascaded cancel — release_turn_coordination must clear
    # (not detach) so the background drive does not outlive the stopped turn.
    user_stopped: bool = False
    # Presence-disconnect stamp (desktop gone). Cleared when the fulfiller returns.
    workspace_channel_dead: bool = False
    # One-shot host content_delta for CHANNEL_DEAD_USER_VISIBLE already emitted.
    channel_dead_user_notice_emitted: bool = False
    # Sticky: run family retired on exec-env hangs / probe fail.
    exec_env_dead: bool = False
    # Classified probe reason (``exec_env_no_interpreter`` / ``…_probe_timeout`` /
    # ``…_spawn_denied``), so CEO inject repeats the same honest cause
    # the live notice gave. None = unclassified (idle hang / unreadable probe).
    exec_env_dead_reason: str | None = None
    # One-shot host content_delta for EXEC_ENV_DEAD_USER_VISIBLE already emitted.
    exec_env_dead_user_notice_emitted: bool = False
    _timeout_notified: set[str] = field(default_factory=set, repr=False)
    # B·超时预警：先于 CEO TIMEOUT 通知，供 worker react_loop 消费进入收尾窗口。
    _timeout_warned: set[str] = field(default_factory=set, repr=False)
    _timeout_wind_down_pending: set[str] = field(default_factory=set, repr=False)
    # 「真正进入过 timeout wind-down」的痕迹：仅在 consume_timeout_wind_down 被引擎消费
    # （工具面实际被收窄）时记入，pending 未消费不算。与 _timeout_notified 同生命周期
    # （arm 重置、disarm 不清），供 coordinated_executor 判超时是否真造成交付缩水。不快照。
    _timeout_wind_down_entered: set[str] = field(default_factory=set, repr=False)
    # Hard-timeout force-cancel via cancel_ids (宽限耗尽 / 宽限墙钟)。不快照。
    _timeout_force_cancelled: set[str] = field(default_factory=set, repr=False)
    # Dedupe escalation injections (live escalate + completion inject + SCOPE boundary).
    _escalation_keys: set[str] = field(default_factory=set, repr=False)
    # D1: blocking escalate → CEO arbitration. run_id → live bridge metadata.
    pending_arbitrations: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Answers stashed when the live Future is gone (ask_user soft-stop cancelled the worker);
    # re-armed workers pick these up on the next escalate(blocking=true).
    resolved_arbitrations: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Mid-flight user interjections awaiting CEO disposition. Credentials are
    # process-local only — journal snapshots strip ``llm_credentials``.
    pending_interjections: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    # Ids injected into a CEO wake and not yet addressed/queued/failed (process-local).
    # ``injected`` SSE is emitted when ids enter this set.
    awaiting_disposition: set[str] = field(default_factory=set, repr=False)
    # Terminal disposition already emitted (idempotent queue_user_message after close).
    dispositioned_interjections: set[str] = field(default_factory=set, repr=False)
    # Live SSE sink for coordination UX (``coordination_wait``). Set by host when
    # arming; not snapshotted — resume re-attaches from the live tool sink.
    event_sink: Any | None = field(default=None, repr=False)
    # 唤醒节流基线（monotonic 秒）：每次真正唤醒 CEO 出手后 stamp。进展事件攒批以此为
    # 参照——「距上次唤醒 <合并窗口 且 未攒够阈值」时 hold 合并，避免每个 worker 完成都醒。
    # None = 尚未唤醒过（首个完成事件本就是必要决策点，立即唤醒、不攒批）。不快照。
    last_wake_monotonic: float | None = field(default=None, repr=False)
    # 空转巡查连续超时次数：每次「无任何新事件」的 idle 超时 +1，有真实事件即清零。
    # idle 等待超时按 2**streak 退避降频，避免无事件时反复烧全量 LLM 轮。不快照。
    idle_streak: int = 0
    # 疑似缺依赖提示（builder.suspect_missing_dep 搭车）：建图时收集，首次注入随团队
    # 事件简报一并呈现给 CEO 后清空——搭车既有注入通道，不新增独立唤醒。不快照。
    dep_advisories: list[str] = field(default_factory=list, repr=False)
    # C3 文件归属账本（execution 生命周期；与批次 WriteCoordinator 统一为一本账）。
    # 进快照可恢复；嵌套子队经 resolve_write_coordinator 共享本表。
    file_ownership: Any | None = field(default=None, repr=False)


def active_coordination(execution_id: str | None = None) -> CoordinationSession | None:
    """Look up the coordination session for a turn.

    Prefer an explicit ``execution_id`` (tool contexts). When omitted, resolve via
    the turn-entry :data:`current_execution_id` ContextVar (captain wait path).
    ContextVar is a single-task-tree cache only — cross-task callers must pass
    ``execution_id`` or use :func:`active_coordination_for_conversation`.
    """
    eid = (execution_id or "").strip() or (current_execution_id.get() or "").strip()
    if not eid:
        return None
    return _sessions.get(eid)


def resolve_coordination_session(
    execution_id: str | None = None,
) -> CoordinationSession | None:
    """Coordination session with the same parent fallback as write-ledger resolve.

    Nested sub-team ``execution_id`` often has no session of its own; fall back to
    :data:`current_execution_id` so ownership lookup / escalate routing share the
    parent book (mirrors :func:`~agentcore.workspace.write_claims.resolve_write_coordinator`).
    """
    eid = (execution_id or "").strip()
    session = active_coordination(eid) if eid else None
    if session is None:
        parent_eid = (current_execution_id.get() or "").strip()
        if parent_eid and parent_eid != eid:
            session = active_coordination(parent_eid)
    if session is None and not eid:
        session = active_coordination()
    return session


def registered_coordination_for_conversation(
    conversation_id: str,
) -> CoordinationSession | None:
    """Registry session for ``conversation_id`` (active or still settling).

    Mid-flight user routing still uses :func:`active_coordination_for_conversation`
    (active only).
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    eid = _by_conversation.get(cid)
    if not eid:
        return None
    return _sessions.get(eid)


def adopt_active_execution(
    conversation_id: str,
    *,
    event_sink: Any | None = None,
) -> CoordinationSession | None:
    """Re-attach the conversation's live execution to the calling turn (pillar B).

    Binds :data:`current_execution_id` to the registry session so the CEO wait path
    finds it even when this turn minted a different id. Sets ``turn_attached=True``
    and optionally refreshes ``event_sink``. Closed sessions are not reopened —
    there is no system closing turn. Returns the adopted session or ``None``.
    """
    session = registered_coordination_for_conversation(conversation_id)
    if session is None or session.user_stopped or session.soft_stop:
        return None
    if not session.active:
        return None
    current_execution_id.set(session.execution_id)
    session.turn_attached = True
    if event_sink is not None:
        session.event_sink = event_sink
    logger.info(
        "coordination.execution_adopted",
        conversation_id=conversation_id,
        execution_id=session.execution_id,
        completed=len(session.completed_run_ids),
        total=session.total_workers,
    )
    return session


def bind_host_journal(
    session: CoordinationSession,
    *,
    writer: Any | None,
    turn_id: str | None = None,
    fact_log: Any | None = None,
) -> None:
    """Remember the arming turn's journal writer + fact log for post-detach DURABLE persistence."""
    if writer is not None:
        session.host_journal_writer = writer
        tid = (turn_id or getattr(writer, "turn_id", "") or "").strip()
        if tid:
            session.host_turn_id = tid
    if fact_log is None:
        from agentcore.runtime.facts import current_fact_log

        fact_log = current_fact_log.get()
    if fact_log is not None:
        session.host_fact_log = fact_log


def rebind_host_journal_writer(old: Any, new: Any) -> None:
    """Point live sessions at ``new`` when they still hold sealed ``old``.

    Pause ``seal()`` freezes the arming-turn writer; background execution
    terminals must keep appending to the same ``turn_id`` via an unsealed
    overflow writer. Sessions that still point at ``old`` would otherwise
    skip the write (``sealed`` guards) or silent-drop in ``_enqueue``.
    """
    if old is None or new is None or old is new:
        return
    for session in _sessions.values():
        if session.host_journal_writer is old:
            session.host_journal_writer = new


def emit_execution_detached(
    session: CoordinationSession,
    *,
    reason: str = "turn_released",
) -> None:
    """Persist + best-effort SSE ``execution_detached`` (pillar D)."""
    from agentcore.runtime.events import execution_detached

    event = execution_detached(
        execution_id=session.execution_id,
        conversation_id=session.conversation_id or "",
        completed=len(session.completed_run_ids),
        total=session.total_workers,
        reason=reason,
        host_turn_id=session.host_turn_id or None,
    )
    sink = session.event_sink
    if sink is not None:
        with contextlib.suppress(Exception):
            sink.emit(event)
    else:
        writer = session.host_journal_writer
        if writer is not None:
            writable = getattr(writer, "writable", None)
            if callable(writable):
                writer = writable()
            with contextlib.suppress(Exception):
                writer.schedule_append(
                    {
                        "kind": event.type.value,
                        "payload": event.payload,
                        "ts": event.timestamp,
                    }
                )
    logger.info(
        "coordination.execution_detached_emitted",
        execution_id=session.execution_id,
        reason=reason,
        completed=len(session.completed_run_ids),
        total=session.total_workers,
    )


def release_turn_coordination(
    execution_id: str | None,
    *,
    conversation_id: str | None = None,
    _followed_conversation_host: bool = False,
) -> None:
    """Chat-turn teardown: drop idle sessions; preserve live background coordination.

    Coordination lifecycle is decoupled from the chat turn — when a background
    drive is still running (typical cross-turn append / SSE disconnect), detach
    turn ownership and leave the registry entry for the drive's finally to
    settle+clear. Idle / already-closed sessions are cleared here as before.

    Explicit user /stop is different: :func:`cancel_coordination_on_user_stop`
    marks ``user_stopped`` and cancels the drive; this path then clears instead
    of detaching so workers do not keep running after the turn is closed.

    Cross-turn append may leave ContextVar on the mint eid while
    ``_by_conversation`` points at the host execution — also release that host
    so ``turn_attached`` is not stuck True on the live drive.

    When the mint eid was never registered (gather child wrote host into its
    ContextVar copy; parent still holds mint), pass ``conversation_id`` so we
    can still resolve the host via ``_by_conversation`` without a mint session.
    """
    eid = (execution_id or "").strip()
    cid_hint = (conversation_id or "").strip()
    if not eid and not cid_hint:
        return
    session = _sessions.get(eid) if eid else None
    # Conversation-active host may differ from the ContextVar mint id.
    extra_eid: str | None = None
    if not _followed_conversation_host:
        cid = ""
        if session is not None:
            cid = (session.conversation_id or "").strip()
        if not cid:
            cid = cid_hint
        if cid:
            mapped = _by_conversation.get(cid)
            if mapped and mapped != eid:
                extra_eid = mapped
            elif not eid and mapped:
                eid = mapped
                session = _sessions.get(eid)
    if eid:
        _release_turn_one(eid, session)
    if extra_eid:
        release_turn_coordination(extra_eid, _followed_conversation_host=True)


def _release_turn_one(eid: str, session: CoordinationSession | None) -> None:
    if session is None:
        return
    if session.user_stopped:
        # Hard stop already signalled — do not detach for background completion.
        if _drive_live(session):
            # Idempotent: ensure cancel fired even if stop raced ahead of this.
            cancel_coordination_on_user_stop(execution_id=eid)
        if session.active:
            session.close()
        clear_active_coordination(eid)
        logger.info(
            "coordination.user_stop_released",
            execution_id=eid,
            completed=len(session.completed_run_ids),
            total=session.total_workers,
        )
        return
    if _drive_live(session):
        if session.turn_attached:
            emit_execution_detached(session, reason="turn_released")
        session.turn_attached = False
        logger.info(
            "coordination.turn_detached",
            execution_id=eid,
            active=session.active,
            completed=len(session.completed_run_ids),
            total=session.total_workers,
        )
        return
    # Drive finished (or never armed). Always detach so attach-grace can proceed.
    # Already-armed settle only hands back attach; do not bare-clear.
    session.turn_attached = False
    if session.harvest_scheduled:
        return
    if (
        session.terminal_posted
        and not session.user_stopped
        and session.settled_via != "detached"
        and (session.conversation_id or "").strip()
    ):
        logger.info(
            "coordination.release_prefers_settle",
            execution_id=eid,
            completed=len(session.completed_run_ids),
            total=session.total_workers,
        )
        finish_detached_coordination(session)
        return
    if session.active:
        session.close()
    clear_active_coordination(eid)


# After drive finally: wait this long for release_turn detach before
# force-settling an *empty* slot. Covers fire-and-forget and the
# cross-turn append ContextVar miss (gather child wrote host eid; parent
# teardown released the mint id → turn_attached stuck True). A still-live
# occupant is not stale — keep waiting for it; do not stretch this grace
# to "cover one LLM round". Inject does not cancel this wait.
_SETTLE_ATTACH_GRACE_S = 5.0
_SETTLE_ATTACH_POLL_S = 0.05
# After terminal_posted: bound ``await_live_detached_drive`` so sidecar/cloud
# owners can finalize outbox even if the drive task never unwinds. Same order of
# magnitude as attach grace; does not cancel the drive.
_AWAIT_DETACHED_DRIVE_GRACE_S = 5.0


def finish_detached_coordination(session: CoordinationSession) -> None:
    """Background drive finally: emit ``execution_completed`` and close (no new turn).

    ``turn_attached=True`` must **not** silently no-op. The arming turn may have
    fire-and-forget ``end_turn``'d without waiting, or turn teardown may have
    released a different ContextVar eid after cross-turn append — leaving this
    session flagged attached forever. Defer briefly so a still-live CEO turn can
    finish writing the current bubble; then settle. Grace expiry force-detaches
    only when the conversation slot has no live occupant.
    """
    if session.user_stopped:
        if session.active:
            session.close()
        current = _sessions.get(session.execution_id)
        if current is session:
            clear_active_coordination(session.execution_id)
        return
    # ask_user soft-stop cancels the drive on purpose; resume re-drives from the
    # journal. Arming settle here races mid-pause (attached grace + Task destroyed
    # pending under xdist teardown).
    if session.soft_stop:
        return
    if _schedule_settle_hold_if_hot_pending(session):
        return
    if session.harvest_scheduled:
        return
    # Empty conversation_id: orphan / unit sessions sync-clear only when already
    # detached. turn_attached=True must keep attach-grace semantics (same as with
    # a cid) so same-turn CEO wait can inject — never sync-clear into a false
    # terminal_unsettled while the arming turn is still attached.
    if not (session.conversation_id or "").strip() and not session.turn_attached:
        _close_detached_session(session)
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _close_detached_session(session)
        return

    session.harvest_scheduled = True
    if session.turn_attached:
        logger.info(
            "coordination.settle_armed_while_attached",
            execution_id=session.execution_id,
            conversation_id=session.conversation_id or "",
            terminal_posted=session.terminal_posted,
            completed=len(session.completed_run_ids),
            total=session.total_workers,
        )
        task = loop.create_task(
            _run_settle_after_attach_grace(session),
            name=f"coord-settle-grace-{session.execution_id[:8]}",
        )
        _retain_settle_task(session, task)
        return
    _arm_settle_now(session)


_SETTLE_CANCEL_LOGGED = "_settle_cancel_logged"


def _log_settle_cancelled(session: CoordinationSession, task: asyncio.Task[Any]) -> None:
    """Emit at most one cancel event (3.13 may never enter the coroutine)."""
    if getattr(task, _SETTLE_CANCEL_LOGGED, False):
        return
    setattr(task, _SETTLE_CANCEL_LOGGED, True)
    logger.warning(
        "coordination.settle_cancelled",
        execution_id=session.execution_id,
        conversation_id=session.conversation_id or "",
    )


def _retain_settle_task(
    session: CoordinationSession, task: asyncio.Task[Any]
) -> None:
    """Keep a strong ref until the task finishes (loop only holds weak refs)."""
    session._settle_tasks.add(task)

    def _on_done(done: asyncio.Task[Any]) -> None:
        session._settle_tasks.discard(done)
        if done.cancelled():
            _log_settle_cancelled(session, done)

    task.add_done_callback(_on_done)


def attached_inject_closed_visibly(session: CoordinationSession) -> bool:
    """True when the captain bubble currently holds post-inject visible prose."""
    return (
        session.settled_via == "attached_inject"
        and session.all_completed_injected
        and session.attached_inject_visible_close
    )


_HOT_PENDING_HOLD_ARMED = "_hot_pending_hold_armed"


def _schedule_settle_hold_if_hot_pending(session: CoordinationSession) -> bool:
    """Defer settle while a user-side hot card is up. Session stays recoverable."""
    from agentcore.runtime.interaction_orphan import holds_for_hot_user

    if not holds_for_hot_user(session):
        return False
    if getattr(session, _HOT_PENDING_HOLD_ARMED, False):
        session.harvest_scheduled = True
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.info(
            "coordination.settle_held_hot_pending",
            execution_id=session.execution_id,
            conversation_id=session.conversation_id or "",
            stage="finish_detached_no_loop",
        )
        return True
    setattr(session, _HOT_PENDING_HOLD_ARMED, True)
    session.harvest_scheduled = True
    logger.info(
        "coordination.settle_held_hot_pending",
        execution_id=session.execution_id,
        conversation_id=session.conversation_id or "",
        stage="finish_detached",
    )
    task = loop.create_task(
        _hold_settle_until_hot_pending_clears(session),
        name=f"coord-settle-hold-{session.execution_id[:8]}",
    )
    _retain_settle_task(session, task)
    return True


async def _hold_settle_until_hot_pending_clears(session: CoordinationSession) -> None:
    """Resume settle after the user card clears, or user_stop close."""
    from agentcore.runtime.interaction_orphan import (
        holds_for_hot_user,
        wait_hot_user_pending_change,
    )

    cid = session.conversation_id or ""
    try:
        while holds_for_hot_user(session) and not session.user_stopped:
            await wait_hot_user_pending_change(cid, timeout=1.0)
        if session.user_stopped:
            setattr(session, _HOT_PENDING_HOLD_ARMED, False)
            session.harvest_scheduled = False
            if session.active:
                session.close()
            current = _sessions.get(session.execution_id)
            if current is session:
                clear_active_coordination(session.execution_id)
            return
        if session.soft_stop:
            setattr(session, _HOT_PENDING_HOLD_ARMED, False)
            session.harvest_scheduled = False
            return
        if _drive_live(session):
            setattr(session, _HOT_PENDING_HOLD_ARMED, False)
            session.harvest_scheduled = False
            return
        # Pending cleared but members still in flight (just allowed): wait, do
        # not settle/cancel the newly unblocked workers.
        while session.running_workers() and not session.user_stopped:
            await asyncio.sleep(0.2)
            if _drive_live(session):
                setattr(session, _HOT_PENDING_HOLD_ARMED, False)
                session.harvest_scheduled = False
                return
        setattr(session, _HOT_PENDING_HOLD_ARMED, False)
        session.harvest_scheduled = False
        if session.user_stopped:
            if session.active:
                session.close()
            current = _sessions.get(session.execution_id)
            if current is session:
                clear_active_coordination(session.execution_id)
            return
        if session.soft_stop:
            return
        if _drive_live(session):
            return
        finish_detached_coordination(session)
    except asyncio.CancelledError:
        setattr(session, _HOT_PENDING_HOLD_ARMED, False)
        session.harvest_scheduled = False
        raise


def _arm_settle_now(session: CoordinationSession) -> None:
    """Mark settled, emit execution_completed, schedule async notify+close."""
    if _schedule_settle_hold_if_hot_pending(session):
        return
    if attached_inject_closed_visibly(session):
        logger.info(
            "coordination.settle_skipped_visible_close",
            execution_id=session.execution_id,
            conversation_id=session.conversation_id or "",
            completed=len(session.completed_run_ids),
            total=session.total_workers,
        )
        session.harvest_scheduled = False
        from agentcore.runtime.coordination.harvest import emit_execution_completed

        emit_execution_completed(session)
        _close_detached_session(session)
        return
    if session.settled_via != "attached_inject":
        session.mark_settled("detached")
    # Emit *before* scheduling the async settle so owners that
    # ``await_live_detached_drive`` still have the turn sink open and can push
    # ``execution_completed`` live (and into outbox before READY).
    from agentcore.runtime.coordination.harvest import emit_execution_completed

    emit_execution_completed(session)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _close_detached_session(session)
        return
    task = loop.create_task(
        _run_settle(session),
        name=f"coord-settle-{session.execution_id[:8]}",
    )
    _retain_settle_task(session, task)


def _conversation_slot_has_live_occupant(conversation_id: str) -> bool:
    """True when ``turn_runs`` or sidecar still holds a live turn for this conversation.

    Stale attach is ``turn_attached`` with an empty slot. Occupancy is
    ``turn_runs`` / sidecar live task — do not inspect host prose.
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return False
    from agentcore.runtime.turn.runs import turn_runs

    existing = turn_runs.get(cid)
    if existing is not None and not existing.task.done():
        return True
    from agentcore.sidecar.server_pkg.core import get_active_sidecar

    sidecar = get_active_sidecar()
    if sidecar is None:
        return False
    live = sidecar.live_turn_task(cid)
    return live is not None and not live.done()


async def _run_settle_after_attach_grace(session: CoordinationSession) -> None:
    """Wait for detach; force-settle only empty-slot stale attach.

    The live turn may still be the waiting/writing bubble. After detach (or
    stale-attach force), ``_arm_settle_now`` emits ``execution_completed``.
    Grace expiry with a live occupant keeps waiting — the occupant is still
    the attached turn, not a stuck flag.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SETTLE_ATTACH_GRACE_S
    logged_live_wait = False
    while True:
        if session.user_stopped:
            session.harvest_scheduled = False
            if session.active:
                session.close()
            current = _sessions.get(session.execution_id)
            if current is session:
                clear_active_coordination(session.execution_id)
            return
        if session.soft_stop:
            session.harvest_scheduled = False
            return
        if not session.turn_attached:
            logger.info(
                "coordination.settle_attach_cleared",
                execution_id=session.execution_id,
            )
            break
        if loop.time() >= deadline:
            if _conversation_slot_has_live_occupant(session.conversation_id or ""):
                if not logged_live_wait:
                    logged_live_wait = True
                    logger.info(
                        "coordination.settle_attach_waiting_live_occupant",
                        execution_id=session.execution_id,
                        conversation_id=session.conversation_id or "",
                        grace_s=_SETTLE_ATTACH_GRACE_S,
                        terminal_posted=session.terminal_posted,
                    )
            else:
                logger.warning(
                    "coordination.settle_stale_attach_forcing",
                    execution_id=session.execution_id,
                    conversation_id=session.conversation_id or "",
                    grace_s=_SETTLE_ATTACH_GRACE_S,
                    terminal_posted=session.terminal_posted,
                )
                session.turn_attached = False
                break
        await asyncio.sleep(_SETTLE_ATTACH_POLL_S)

    if session.user_stopped or session.soft_stop:
        session.harvest_scheduled = False
        return
    _arm_settle_now(session)


def _close_detached_session(session: CoordinationSession) -> None:
    if session.active:
        session.close()
    current = _sessions.get(session.execution_id)
    if current is session:
        clear_active_coordination(session.execution_id)
        logger.info(
            "coordination.detached_drive_finished",
            execution_id=session.execution_id,
            completed=len(session.completed_run_ids),
            total=session.total_workers,
            settled_via=session.settled_via,
        )


async def _run_settle(session: CoordinationSession) -> None:
    try:
        from agentcore.runtime.coordination.harvest import settle_detached_execution

        await settle_detached_execution(session)
    except asyncio.CancelledError:
        # BaseException since 3.9: ``except Exception`` does not catch this.
        # 3.13 may also cancel a never-started task without entering this body;
        # the retain done-callback logs that case.
        task = asyncio.current_task()
        if task is not None:
            _log_settle_cancelled(session, task)
        raise
    except Exception:  # noqa: BLE001 — settle must never leak into drive task
        logger.exception(
            "coordination.settle_failed",
            execution_id=session.execution_id,
        )


def active_coordination_for_conversation(
    conversation_id: str,
) -> CoordinationSession | None:
    """Live coordination session for ``conversation_id``, or ``None``.

    Used by ``POST …/messages`` to route mid-flight user text into the CEO window
    instead of the conversation-level turn queue.
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    eid = _by_conversation.get(cid)
    if not eid:
        return None
    session = _sessions.get(eid)
    if session is None or not session.active:
        return None
    return session


def set_active_coordination(session: CoordinationSession | None) -> None:
    """Register ``session`` under its ``execution_id``, or clear when ``None``.

    Also binds :data:`current_execution_id` in the *current* context (tests / settle
    in the captain task). When ``set_active`` runs inside an ``asyncio.gather`` child
    (delegate tool), that ContextVar write stays in the child copy — the parent CEO
    loop relies on the turn-entry binding set before gather.

    Mid-coordination secondary ``delegate`` must **merge** into the existing session
    (see :func:`agentcore.runtime.coordination.host.try_start_coordination`) — never
    call this to silently replace an active session for the same ``execution_id``.
    """
    if session is None:
        clear_active_coordination()
        return
    eid = (session.execution_id or "").strip()
    if not eid:
        logger.warning("coordination.set_active_missing_execution_id")
        return
    prior = _sessions.get(eid)
    if (
        prior is not None
        and prior is not session
        and prior.active
        and prior.drive_task is not None
        and not prior.drive_task.done()
    ):
        # Overwrite while a background drive still owns the old session = event
        # crosstalk / lost cancel+arbitration. Callers must merge instead.
        logger.error(
            "coordination.set_active_overwrite_while_live",
            execution_id=eid,
            prior_workers=prior.total_workers,
            new_workers=session.total_workers,
        )
    _sessions[eid] = session
    cid = (session.conversation_id or "").strip()
    if cid:
        _by_conversation[cid] = eid
    current_execution_id.set(eid)


def clear_active_coordination(
    execution_id: str | None = None,
    _token: object | None = None,
) -> None:
    """Drop one session by ``execution_id``, or the whole registry when omitted.

    ``_token`` kept for call-site compat (ignored). Omitting ``execution_id`` clears
    every entry — used by test teardown. Pass an id to isolate concurrent turns.
    """
    eid = (execution_id or "").strip()
    if eid:
        dropped = _sessions.pop(eid, None)
        if dropped is not None:
            dropped.check_terminal_settlement()
            cid = (dropped.conversation_id or "").strip()
            if cid and _by_conversation.get(cid) == eid:
                _by_conversation.pop(cid, None)
        else:
            # Session already gone — still scrub stale conversation index entries.
            stale = [c for c, e in _by_conversation.items() if e == eid]
            for c in stale:
                _by_conversation.pop(c, None)
        return
    for sess in list(_sessions.values()):
        sess.check_terminal_settlement()
    _sessions.clear()
    _by_conversation.clear()


def _drive_live(session: CoordinationSession) -> bool:
    task = session.drive_task
    return task is not None and not task.done()


async def await_live_detached_drive(conversation_id: str) -> bool:
    """Await a live *detached* coordination drive before the sink owner closes.

    Pillar D1 (narrow): after the chat pipeline returns, keep the turn sink open
    until the background drive settles so ``run_completed`` / ``execution_completed``
    still reach the live UI, and so sidecar outbox READY is not sealed while
    post-detach DURABLE journal appends are still in flight.

    No-op (returns False) when there is no live detached drive — user_stop /
    still-attached / idle all close immediately. ``soft_stop`` still awaits a
    live drive so finally-block terminal frames are not dropped by an early
    sink close. Drive-task cancel/failure does not raise; caller cancellation does.
    """
    # Registered (not only ``active``): settle may have closed the session
    # after the drive finished, but post-detach journal 对账 still needs the
    # host_fact_log that remains on the registry object.
    session = registered_coordination_for_conversation(conversation_id)
    if session is None:
        return False
    if session.user_stopped or session.turn_attached:
        return False
    task = session.drive_task
    if task is None:
        return False
    # ``asyncio.wait`` completes when the drive finishes (ok / error / cancel)
    # without re-raising the drive's CancelledError; our own cancellation still
    # propagates so turn cancel paths close the sink immediately.
    # Already-done drives still flush + journal-terminal 对账: CEO persist can
    # race the last workers, so this is the first place that sees post-detach
    # host_fact_log (``settled_via`` stamp cannot).
    if not task.done():
        if session.terminal_posted:
            done, pending = await asyncio.wait(
                {task}, timeout=_AWAIT_DETACHED_DRIVE_GRACE_S
            )
            if pending:
                logger.error(
                    "coordination.await_detached_drive_grace_expired",
                    conversation_id=conversation_id,
                    execution_id=session.execution_id,
                    grace_s=_AWAIT_DETACHED_DRIVE_GRACE_S,
                )
        else:
            await asyncio.wait({task})
    writer = session.host_journal_writer
    if writer is not None:
        flush = getattr(writer, "flush", None)
        if flush is not None:
            with contextlib.suppress(Exception):
                await flush()
    log = session.host_fact_log
    entries = log.entries() if log is not None and hasattr(log, "entries") else None
    session.check_terminal_settlement(journal_entries=entries)
    return True


def note_coord_worker_busy(
    run_id: str,
    kind: str,
    *,
    rounds_used: int | None = None,
    rounds_limit: int | None = None,
    tokens_spent: int | None = None,
) -> None:
    """Best-effort stamp: worker ``run_id`` is inside LLM / tool / verify / arbitrate.

    Optional spend kwargs are the same channel — not a second reporter.
    """
    session = active_coordination()
    if session is None or not session.active:
        return
    session.mark_worker_busy(
        run_id,
        kind,
        rounds_used=rounds_used,
        rounds_limit=rounds_limit,
        tokens_spent=tokens_spent,
    )


def clear_coord_worker_busy(run_id: str) -> None:
    """Clear the busy stamp for ``run_id`` (end of LLM/tool)."""
    session = active_coordination()
    if session is None:
        return
    session.clear_worker_busy(run_id)


def cancel_coordination_on_user_stop(
    conversation_id: str | None = None,
    *,
    execution_id: str | None = None,
) -> bool:
    """Cascade-cancel live coordination for an explicit user /stop.

    Cancels every in-flight worker (via ``cancel_ids``) and the background
    ``drive_task``. Marks ``user_stopped`` so :func:`release_turn_coordination`
    clears the session instead of detaching. SSE disconnect must NOT call this
    (detach-and-continue semantics stay intact).

    Returns ``True`` when a live session was found and cancelled.
    """
    session: CoordinationSession | None = None
    eid = (execution_id or "").strip()
    if eid:
        session = _sessions.get(eid)
    if session is None:
        cid = (conversation_id or "").strip()
        if cid:
            session = active_coordination_for_conversation(cid)
    if session is None or not session.active:
        return False
    session.user_stopped = True
    running = list(session.running_workers())
    for run_id, _role in running:
        session.request_cancel(run_id)
    from agentcore.runtime.coordination.drive_cancel import cancel_drive_task

    cancel_drive_task(session, "user_stop")
    logger.info(
        "coordination.user_stop_cancelled",
        execution_id=session.execution_id,
        conversation_id=session.conversation_id or conversation_id or "",
        cancelled_workers=len(running),
        completed=len(session.completed_run_ids),
        total=session.total_workers,
        reason="user_stop",
    )
    return True
