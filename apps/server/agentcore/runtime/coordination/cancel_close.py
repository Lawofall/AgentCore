"""Cancel/interrupt close wording — structural facts only.

Shared by coordination inject and ``cancel_worker`` success stamps.

Does not scan user prose. Does not change schedule / cancel / approval.
"""

from __future__ import annotations

from typing import Any, Literal

from agentcore.runtime.coordination.session_types import (
    CoordinationEvent,
    CoordinationEventKind,
)

CancelCloseKind = Literal[
    "user_stopped",
    "drive_cancelled",
    "ceo_cancel_started",
    "not_started",
    "cancelled",
]

# Event-line / harvest marks — tests distinguish the four named kinds by these.
USER_STOPPED_MARK = "用户已停止"
DRIVE_INTERRUPTED_MARK = "协调被打断"
CEO_CANCEL_STARTED_MARK = "主 Agent 已终止已开工队员"
NOT_STARTED_MARK = "尚未开工"
NOT_STARTED_ALLOWED = "尚未启动"
FORBID_NEVER_STARTED = "已开工队员禁止说成「没启动 / 没跑起来 / 一直未被启动」。"
ALLOW_NOT_STARTED = "仅尚未开工被取消的队员可说明「尚未启动」。"

_STARTED_EVENT_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "running", "retrying"}
)

_DONT_PASTE = "勿粘贴协调事件 / 队员终态名册 / escalation 原文 / 中间合成草稿"


def note_cancel_worker_success(
    session: Any,
    run_id: str,
    *,
    started: bool,
) -> None:
    """Stamp a successful ``cancel_worker`` (running stop or queued withdraw).

    Unsettled leftover-clear and timeout force-cancel must not call this.
    """
    rid = (run_id or "").strip()
    if not rid:
        return
    ids = getattr(session, "ceo_cancel_worker_ids", None)
    if not isinstance(ids, set):
        session.ceo_cancel_worker_ids = set()
        ids = session.ceo_cancel_worker_ids
    ids.add(rid)
    if not started:
        return
    started_ids = getattr(session, "ceo_cancel_started_ids", None)
    if not isinstance(started_ids, set):
        session.ceo_cancel_started_ids = set()
        started_ids = session.ceo_cancel_started_ids
    started_ids.add(rid)


def worker_was_started(session: Any, run_id: str) -> bool:
    """Whether this member had been dispatched (start clock / running / non-queue)."""
    rid = (run_id or "").strip()
    if not rid:
        return False
    started_at = getattr(session, "_worker_started_at", None) or {}
    if rid in started_at:
        return True
    running = getattr(session, "_running_workers", None) or {}
    if rid in running:
        return True
    started_stamps = getattr(session, "ceo_cancel_started_ids", None) or ()
    if rid in started_stamps:
        return True
    failed = getattr(session, "failed_run_ids", None) or ()
    if rid in failed:
        return True
    completed = getattr(session, "completed_run_ids", None) or set()
    cancel_ids = getattr(session, "cancel_ids", None) or set()
    vacated = getattr(session, "vacated_run_ids", None) or set()
    # Successful finisher (not a cancel/skip leftover).
    return rid in completed and rid not in cancel_ids and rid not in vacated


def member_had_started(
    session: Any,
    events: list[CoordinationEvent] | None = None,
) -> bool:
    """True when any member was dispatched / left a non-queue terminal."""
    started_stamps = getattr(session, "ceo_cancel_started_ids", None) or ()
    if started_stamps:
        return True
    started_at = getattr(session, "_worker_started_at", None) or {}
    if started_at:
        return True
    running = getattr(session, "_running_workers", None) or {}
    if running:
        return True
    failed = getattr(session, "failed_run_ids", None) or ()
    if failed:
        return True
    completed = set(getattr(session, "completed_run_ids", None) or ())
    cancel_ids = set(getattr(session, "cancel_ids", None) or ())
    vacated = set(getattr(session, "vacated_run_ids", None) or ())
    if completed - cancel_ids - vacated:
        return True
    for ev in _iter_close_events(session, events):
        if ev.kind is not CoordinationEventKind.WORKER_COMPLETED:
            continue
        status = str((ev.payload or {}).get("status") or "").strip().lower()
        if status in _STARTED_EVENT_STATUSES:
            return True
    facts = getattr(session, "harvest_user_facts", None)
    if isinstance(facts, dict):
        for raw in facts.get("nodes") or []:
            if not isinstance(raw, dict):
                continue
            status = str(raw.get("status") or "").strip().lower()
            if status in _STARTED_EVENT_STATUSES - {"cancelled"}:
                return True
    return False


def classify_cancel_close(
    session: Any,
    events: list[CoordinationEvent] | None = None,
) -> CancelCloseKind | None:
    """Wording key for a cancel/interrupt close. ``None`` if not a cancel close.

    Priority: user_stopped > DRIVE_CANCELLED > CEO cancel_worker of started
    members > queue-only withdraw. Soft-stop has its own copy (not this).
    """
    if getattr(session, "soft_stop", False):
        return None
    evs = _iter_close_events(session, events)
    if getattr(session, "user_stopped", False):
        return "user_stopped"
    if getattr(session, "drive_cancelled", False) or any(
        ev.kind is CoordinationEventKind.DRIVE_CANCELLED for ev in evs
    ):
        return "drive_cancelled"
    stamps = set(getattr(session, "ceo_cancel_worker_ids", None) or ())
    started = bool(getattr(session, "ceo_cancel_started_ids", None) or ()) or (
        member_had_started(session, evs)
    )
    if stamps:
        return "ceo_cancel_started" if started else "not_started"
    if _batch_is_cancelled(session, evs):
        return "cancelled"
    return None


def cancel_event_headline(
    kind: CancelCloseKind,
    *,
    prefix: str,
    done: object,
    total: object,
) -> str:
    """One ``all_completed`` / ``drive_cancelled`` fact line."""
    count = f"（{done}/{total}）"
    if kind == "user_stopped":
        return f"- {prefix}：{USER_STOPPED_MARK}，基于已完成部分收口{count}。"
    if kind == "drive_cancelled":
        return f"- {prefix}：{DRIVE_INTERRUPTED_MARK}，基于已完成部分收口{count}。"
    if kind == "ceo_cancel_started":
        return (
            f"- {prefix}：{CEO_CANCEL_STARTED_MARK}，基于已完成部分收口{count}。"
            "禁止对用户说「没启动 / 没跑起来 / 一直未被启动」。"
        )
    if kind == "not_started":
        return (
            f"- {prefix}：{NOT_STARTED_MARK}的队员已从队列撤出，基于已完成部分收口{count}。"
            f"此情形可说明「{NOT_STARTED_ALLOWED}」。"
        )
    return f"- {prefix}：任务已取消，基于已完成部分收口{count}。"


def cancel_close_line(kind: CancelCloseKind) -> str:
    if kind == "user_stopped":
        return (
            "按终稿纪律基于已完成部分向用户交代（走 content_delta）；"
            f"说明{USER_STOPPED_MARK}，不要接着派活。"
        )
    if kind == "drive_cancelled":
        return (
            "按终稿纪律基于已完成部分向用户交代（走 content_delta）；"
            f"说明{DRIVE_INTERRUPTED_MARK}，不要接着派活。"
        )
    if kind == "ceo_cancel_started":
        return (
            "按终稿纪律基于已完成部分向用户交代（走 content_delta）；"
            "说明已开工队员被终止，不要接着派活。"
        )
    if kind == "not_started":
        return (
            "按终稿纪律基于已完成部分向用户交代（走 content_delta）；"
            f"说明{NOT_STARTED_MARK}的队员已取消，不要接着派活。"
        )
    return (
        "按终稿纪律基于已完成部分向用户交代（走 content_delta）；"
        "说明已取消，调度已停，不要接着派活。"
    )


def cancel_discipline_sentence(kind: CancelCloseKind | None, session: Any) -> str:
    """Extra 【终稿纪律】 sentence. Empty on success / failure / unproven leftover."""
    if kind == "not_started":
        return ALLOW_NOT_STARTED
    if kind in ("user_stopped", "drive_cancelled", "ceo_cancel_started"):
        return FORBID_NEVER_STARTED
    if kind == "cancelled" and member_had_started(session):
        return FORBID_NEVER_STARTED
    return ""


def _iter_close_events(
    session: Any,
    events: list[CoordinationEvent] | None,
) -> list[CoordinationEvent]:
    if events is not None:
        return list(events)
    return list(getattr(session, "_pending", None) or [])


def _batch_is_cancelled(session: Any, events: list[CoordinationEvent]) -> bool:
    for ev in events:
        if ev.kind is CoordinationEventKind.ALL_COMPLETED:
            payload = ev.payload or {}
            if payload.get("cancelled") or payload.get("error"):
                return True
        if ev.kind is CoordinationEventKind.DRIVE_CANCELLED:
            return True
    cancel_ids = set(getattr(session, "cancel_ids", None) or ())
    completed = set(getattr(session, "completed_run_ids", None) or ())
    failed = set(getattr(session, "failed_run_ids", None) or ())
    return bool((cancel_ids & completed) - failed)
