"""Durable coordination snapshot, restore, and terminal-settlement bookkeeping.

Split from ``session.py`` — pure move. Queue drain used while snapshotting
stays on ``SessionQueueMixin._drain_queue_copy``.
"""

# mypy: disable-error-code="misc"

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.coordination.session_types import (
    _INTERJECTION_SNAPSHOT_KEYS,
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSnapshot,
    _durable_terminal_run_ids,
)

if TYPE_CHECKING:
    from agentcore.runtime.coordination.session import CoordinationSession

logger = get_logger("agentcore.runtime.coordination.session")


class SessionSnapshotMixin:
    """to/from ``CoordinationSnapshot`` and terminal ``settled_via`` accounting."""

    settled_via: str | None

    def mark_settled(self: CoordinationSession, via: str) -> None:
        """Record which path consumed the terminal (attached inject / detached / stop)."""
        label = (via or "").strip()
        if not label:
            return
        if self.settled_via and self.settled_via != label:
            logger.info(
                "coordination.settled_via_replaced",
                execution_id=self.execution_id,
                prior=self.settled_via,
                via=label,
            )
        self.settled_via = label

    def note_attached_inject_visible_close(
        self: CoordinationSession, delta: str
    ) -> None:
        """Record that the captain bubble currently has post-inject visible prose.

        Structural only: non-empty ``content_delta``, ``settled_via=attached_inject``.
        Does not inspect prose. ``content_reset`` must clear this — it is the
        live bubble, not a one-shot latch.
        """
        if not (delta or "").strip():
            return
        if not self.all_completed_injected:
            return
        if self.settled_via != "attached_inject":
            return
        self.attached_inject_visible_close = True

    def clear_attached_inject_visible_close(self: CoordinationSession) -> None:
        """``content_reset`` emptied the captain bubble; visible-close skip is invalid
        until it fills again."""
        self.attached_inject_visible_close = False

    def check_terminal_settlement(
        self: CoordinationSession,
        journal_entries: list[dict[str, Any]] | None = None,
    ) -> None:
        """终态对账：terminal 必须收敛到附着注入或 detached 结算（user_stop 豁免）。

        When ``journal_entries`` is the host journal after detach, missing durable
        run terminals fire even if inject/settle already stamped ``settled_via`` —
        that stamp cannot see frames lost after the arming-turn snapshot.
        """
        if journal_entries is not None and self.completed_run_ids:
            have = _durable_terminal_run_ids(journal_entries)
            missing = sorted(rid for rid in self.completed_run_ids if rid not in have)
            if missing:
                logger.error(
                    "coordination.terminal_unsettled",
                    execution_id=self.execution_id,
                    conversation_id=self.conversation_id or "",
                    completed=len(self.completed_run_ids),
                    total=self.total_workers,
                    turn_attached=self.turn_attached,
                    harvest_scheduled=self.harvest_scheduled,
                    all_completed_injected=self.all_completed_injected,
                    missing_run_ids=missing,
                    detail=(
                        "终态对账失败：execution 已有完成 run，但宿主 journal 缺少对应终态帧。"
                    ),
                )
                return
        if self.settled_via:
            return
        if self.user_stopped:
            self.settled_via = "user_stop"
            return
        if self.all_completed_injected:
            self.settled_via = "attached_inject"
            return
        if self.harvest_scheduled:
            self.settled_via = "detached"
            return
        if not self.terminal_posted:
            return
        logger.error(
            "coordination.terminal_unsettled",
            execution_id=self.execution_id,
            conversation_id=self.conversation_id or "",
            completed=len(self.completed_run_ids),
            total=self.total_workers,
            turn_attached=self.turn_attached,
            harvest_scheduled=self.harvest_scheduled,
            all_completed_injected=self.all_completed_injected,
            detail=("终态对账失败：execution 已投递终态，但未收敛到附着回合注入或 detached 结算。"),
        )

    def snapshot(self: CoordinationSession) -> CoordinationSnapshot:
        pending = [
            {"kind": e.kind.value, "payload": dict(e.payload)}
            for e in (
                *self._deferred_progress,
                *self._pending,
                *self._drain_queue_copy(),
            )
        ]
        live_plan_json: dict[str, Any] | None = None
        if self.live_plan is not None:
            try:
                from agentcore.runtime.runs.serialize import plan_to_json

                live_plan_json = plan_to_json(self.live_plan)
            except Exception:  # noqa: BLE001 — snapshot must never raise
                logger.warning(
                    "coordination.live_plan_snapshot_failed",
                    execution_id=self.execution_id,
                )
                live_plan_json = None
        interjections = [
            {
                "interjection_id": iid,
                **{k: v for k, v in payload.items() if k in _INTERJECTION_SNAPSHOT_KEYS},
            }
            for iid, payload in self.pending_interjections.items()
        ]
        ownership_dict: dict[str, Any] = {}
        if self.file_ownership is not None:
            try:
                ownership_dict = dict(self.file_ownership.to_dict())
            except Exception:  # noqa: BLE001 — snapshot must never raise
                logger.warning(
                    "coordination.file_ownership_snapshot_failed",
                    execution_id=self.execution_id,
                )
                ownership_dict = {}
        return CoordinationSnapshot(
            execution_id=self.execution_id,
            draft=self.draft,
            conversation_id=self.conversation_id,
            completed_run_ids=sorted(self.completed_run_ids),
            progress_budget_remaining=self.progress_budget_remaining,
            decision_budget_remaining=self.decision_budget_remaining,
            total_workers=self.total_workers,
            active=self.active,
            cancel_run_ids=sorted(self.cancel_ids),
            ceo_cancel_worker_ids=sorted(self.ceo_cancel_worker_ids),
            ceo_cancel_started_ids=sorted(self.ceo_cancel_started_ids),
            pending_events=pending,
            pending_arbitrations=[dict(v) for v in self.pending_arbitrations.values()],
            resolved_arbitrations=[dict(v) for v in self.resolved_arbitrations.values()],
            live_plan=live_plan_json,
            pending_interjections=interjections,
            all_completed_injected=self.all_completed_injected,
            harvest_scheduled=self.harvest_scheduled,
            terminal_posted=self.terminal_posted,
            drive_cancelled=self.drive_cancelled,
            settled_via=self.settled_via,
            turn_attached=self.turn_attached,
            user_stopped=self.user_stopped,
            saw_first_completion=self._saw_first_completion,
            file_ownership=ownership_dict,
        )

    @classmethod
    def from_snapshot(
        cls: type[CoordinationSession], snap: CoordinationSnapshot
    ) -> CoordinationSession:
        session = cls(
            execution_id=snap.execution_id,
            total_workers=snap.total_workers,
            progress_budget_remaining=snap.progress_budget_remaining,
            decision_budget_remaining=snap.decision_budget_remaining,
            draft=snap.draft,
            conversation_id=snap.conversation_id,
            completed_run_ids=set(snap.completed_run_ids),
            # Treat restored completions as already reported — avoid re-listing the
            # whole roster as「本轮新完成」on the first post-resume inject.
            progress_reported_completed=set(snap.completed_run_ids),
            cancel_ids=set(snap.cancel_run_ids),
            ceo_cancel_worker_ids=set(snap.ceo_cancel_worker_ids),
            ceo_cancel_started_ids=set(snap.ceo_cancel_started_ids),
            active=snap.active,
            all_completed_injected=snap.all_completed_injected,
            harvest_scheduled=snap.harvest_scheduled,
            terminal_posted=snap.terminal_posted,
            drive_cancelled=snap.drive_cancelled,
            settled_via=snap.settled_via,
            turn_attached=snap.turn_attached,
            user_stopped=snap.user_stopped,
        )
        if snap.live_plan:
            try:
                from agentcore.runtime.runs.serialize import plan_from_json

                session.live_plan = plan_from_json(snap.live_plan)
            except Exception:  # noqa: BLE001 — tolerate corrupt plan payload
                logger.warning(
                    "coordination.live_plan_restore_failed",
                    execution_id=snap.execution_id,
                )
                session.live_plan = None
        for raw in snap.pending_events:
            kind_raw = str(raw.get("kind") or "")
            try:
                kind = CoordinationEventKind(kind_raw)
            except ValueError:
                continue
            session._pending.append(
                CoordinationEvent(kind=kind, payload=dict(raw.get("payload") or {}))
            )
        for raw in snap.pending_arbitrations:
            rid = str(raw.get("run_id") or "").strip()
            if rid:
                session.pending_arbitrations[rid] = dict(raw)
        for raw in snap.resolved_arbitrations:
            rid = str(raw.get("run_id") or "").strip()
            if rid:
                session.resolved_arbitrations[rid] = dict(raw)
        for raw in snap.pending_interjections:
            iid = str(raw.get("interjection_id") or "").strip()
            if not iid:
                continue
            payload = {k: v for k, v in raw.items() if k in _INTERJECTION_SNAPSHOT_KEYS}
            session.pending_interjections[iid] = payload
        if snap.saw_first_completion or snap.completed_run_ids:
            session._saw_first_completion = True
        raw_own = snap.file_ownership
        if raw_own and (
            raw_own.get("_v") in (2, 3) or isinstance(raw_own.get("owners"), dict)
        ):
            from agentcore.workspace.write_claims import WriteCoordinator

            run_desks: dict[str, str | None] = {}
            birth: str | None = None
            live = session.live_plan
            if live is not None:
                for n in getattr(live, "nodes", ()) or ():
                    rid = (getattr(n, "run_id", None) or "").strip()
                    if rid:
                        tf = getattr(n, "target_folder_id", None)
                        run_desks[rid] = (
                            str(tf).strip() if tf is not None and str(tf).strip() else None
                        )
            session.file_ownership = WriteCoordinator.from_dict(
                raw_own,
                birth_desk_id=birth,
                run_target_folder_ids=run_desks or None,
            )
        return session
