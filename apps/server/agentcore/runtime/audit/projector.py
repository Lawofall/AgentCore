"""Map journal facts / runtime payloads to audit drafts."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any

from agentcore.core.types import ToolApproval
from agentcore.runtime.audit.recorder import AuditDraft, AuditRecorder

_TASK_PREVIEW_CHARS = 200


@lru_cache(maxsize=1)
def _grantable_tool_names() -> frozenset[str]:
    """All GRANTABLE tools on the declaration roster (builtin + worker-only Host/L3…).

    Builtin-only scan would miss runtime-elevated ``host`` / terminal / browser — those
    still go through ApprovalGate and must land on ``agent_audit_events`` like file tools.
    """
    from agentcore.tools.registration import (
        ToolSurface,
        declared_tools,
        instantiate_declared,
        tool_registration,
    )

    names: set[str] = set()
    for cls in declared_tools():
        reg = tool_registration(cls)
        if reg.surface is ToolSurface.CEO_ORCHESTRATION:
            continue
        schema = instantiate_declared(cls, location=None).schema
        if schema.approval is ToolApproval.GRANTABLE:
            names.add(schema.name)
    # Keep ``git`` / ``host`` even though schema is NEVER (runtime-elevated approval).
    return frozenset(names | {"git", "host"})


def task_preview_and_hash(task: str) -> tuple[str, str]:
    text = task or ""
    preview = text[:_TASK_PREVIEW_CHARS]
    return preview, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _context_inject_source_run_ids(payload: dict[str, Any]) -> list[str]:
    """Upstream run ids from ``run_context`` dependency blocks."""
    ids: list[str] = []
    blocks = payload.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict) or block.get("channel") != "dependency":
                continue
            source = block.get("source_run_id")
            if source:
                ids.append(str(source))
    seen: set[str] = set()
    out: list[str] = []
    for run_id in ids:
        if run_id in seen:
            continue
        seen.add(run_id)
        out.append(run_id)
    return out


def _workspace_rel_path(path: str | None) -> str | None:
    if not path:
        return None
    rel = str(path).replace("\\", "/").strip().lstrip("/")
    return rel or None


def _file_target_from_arguments(tool_name: str, arguments: dict[str, Any]) -> str | None:
    if tool_name in {"file_write", "file_read", "file_delete", "file_move", "str_replace"}:
        return _workspace_rel_path(str(arguments.get("path") or arguments.get("file_path") or ""))
    if tool_name == "git":
        return _workspace_rel_path(str(arguments.get("path") or "."))
    return None


def _actor_kind(recorder: AuditRecorder, run_id: str | None) -> str:
    if not run_id:
        return "system"
    if recorder.captain_run_id and run_id == recorder.captain_run_id:
        return "captain"
    return "member"


def project_journal_entry(recorder: AuditRecorder, entry: dict[str, Any]) -> AuditDraft | None:
    kind = entry.get("kind")
    payload = entry.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    if kind == "tool_use_start":
        tool_call_id = str(payload.get("tool_call_id") or "")
        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        recorder.remember_tool_args(tool_call_id, arguments)
        return None

    if kind == "tool_use_end":
        tool_name = str(payload.get("tool_name") or "")
        if tool_name not in _grantable_tool_names():
            return None
        run_id = str(payload.get("run_id") or "") or None
        success = payload.get("status") != "error"
        tool_call_id = str(payload.get("tool_call_id") or "")
        arguments = recorder.pop_tool_args(tool_call_id)
        display = payload.get("display") if isinstance(payload.get("display"), dict) else {}
        target_ref = _file_target_from_arguments(
            tool_name, arguments
        ) or _file_target_from_arguments(tool_name, display)
        return AuditDraft(
            category="tool",
            action=f"tool.{tool_name}",
            actor_kind=_actor_kind(recorder, run_id),
            outcome="ok" if success else "failed",
            run_id=run_id,
            target_type="file" if target_ref else "tool",
            target_ref=target_ref or str(payload.get("tool_call_id") or tool_name),
            detail={
                "tool_call_id": payload.get("tool_call_id"),
                "success": success,
            },
        )

    if kind == "run_started":
        run_id = str(payload.get("run_id") or "") or None
        return AuditDraft(
            category="state",
            action="run.started",
            actor_kind=_actor_kind(recorder, run_id),
            outcome="ok",
            run_id=run_id,
            parent_run_id=str(payload.get("parent_run_id") or "") or None,
            detail={"agent_id": payload.get("agent_id"), "kind": payload.get("kind")},
        )

    if kind == "run_completed":
        run_id = str(payload.get("run_id") or "") or None
        return AuditDraft(
            category="state",
            action="run.completed",
            actor_kind=_actor_kind(recorder, run_id),
            outcome="ok",
            run_id=run_id,
            detail={
                "finish_reason": payload.get("finish_reason"),
                "files_touched": payload.get("files_touched") or [],
            },
        )

    if kind == "run_failed":
        run_id = str(payload.get("run_id") or "") or None
        return AuditDraft(
            category="failure",
            action="run.failed",
            actor_kind=_actor_kind(recorder, run_id),
            outcome="failed",
            run_id=run_id,
            detail={"error": str(payload.get("error") or "")[:500]},
        )

    if kind == "run_context":
        execution_id = str(payload.get("execution_id") or "") or None
        source_run_ids = _context_inject_source_run_ids(payload)
        return AuditDraft(
            category="comm",
            action="context.inject",
            actor_kind="member",
            outcome="ok",
            execution_id=execution_id,
            run_id=str(payload.get("run_id") or "") or None,
            detail={
                "source_run_ids": source_run_ids,
                "handling": payload.get("handling"),
                "size_bytes": payload.get("size_bytes"),
                "truncated": payload.get("truncated"),
                "file_pointers": payload.get("file_pointers") or [],
            },
        )

    if kind == "plan_revised":
        return AuditDraft(
            category="orchestration",
            action="plan.revised",
            actor_kind="captain",
            outcome="ok",
            execution_id=str(payload.get("execution_id") or "") or None,
            detail={"revisions": payload.get("revisions") or []},
        )

    if kind == "escalation_required":
        run_id = str(payload.get("run_id") or "") or None
        return AuditDraft(
            category="comm",
            action="escalate.raised",
            actor_kind=_actor_kind(recorder, run_id),
            outcome="ok",
            run_id=run_id,
            target_type="interaction",
            target_ref=str(payload.get("escalation_id") or "") or None,
            detail={
                "question": str(payload.get("question") or "")[:200],
                "assumption": str(payload.get("assumption") or "")[:200],
            },
        )

    if kind == "escalation_resolved":
        run_id = str(payload.get("run_id") or "") or None
        status = str(payload.get("status") or "resolved")
        outcome = (
            "ok"
            if status == "resolved"
            else "denied"
            if status in ("timed_out", "orphaned")
            else "failed"
        )
        return AuditDraft(
            category="comm",
            action="escalate.resolved",
            actor_kind=_actor_kind(recorder, run_id),
            outcome=outcome,
            run_id=run_id,
            target_type="interaction",
            target_ref=str(payload.get("escalation_id") or "") or None,
            detail={"status": status, "answer": str(payload.get("answer") or "")[:200]},
        )

    if kind in {"checkpoint_required", "plan_review_required", "team_preview_required"}:
        checkpoint_kind = (
            "plan_review"
            if kind == "plan_review_required"
            else "team_preview"
            if kind == "team_preview_required"
            else "ask_user"
        )
        return AuditDraft(
            category="state",
            action="checkpoint.paused",
            actor_kind="captain",
            outcome="ok",
            target_type="interaction",
            target_ref=str(payload.get("checkpoint_id") or "") or None,
            detail={
                "checkpoint_kind": checkpoint_kind,
                "question": str(payload.get("question") or payload.get("summary") or "")[:200],
            },
        )

    if kind in {"checkpoint_resolved", "plan_review_resolved", "team_preview_resolved"}:
        decision = str(payload.get("decision") or "continue")
        checkpoint_kind = (
            "plan_review"
            if kind == "plan_review_resolved"
            else "team_preview"
            if kind == "team_preview_resolved"
            else "ask_user"
        )
        return AuditDraft(
            category="state",
            action="checkpoint.resumed",
            actor_kind="captain",
            outcome="ok" if decision not in {"stop", "deny"} else "denied",
            target_type="interaction",
            target_ref=str(payload.get("checkpoint_id") or "") or None,
            detail={
                "checkpoint_kind": checkpoint_kind,
                "decision": decision,
                "note": str(payload.get("note") or "")[:200],
            },
        )

    return None


def project_tool_disabled(
    recorder: AuditRecorder,
    *,
    tool_name: str,
    run_id: str,
    failure_count: int,
) -> AuditDraft:
    return AuditDraft(
        category="permission",
        action="permission.tool_disabled",
        actor_kind=_actor_kind(recorder, run_id),
        outcome="ok",
        run_id=run_id,
        target_type="tool",
        target_ref=tool_name,
        detail={"tool_name": tool_name, "failure_count": failure_count},
    )


def project_write_conflict(
    recorder: AuditRecorder,
    *,
    path: str,
    run_id: str,
    owner_run_id: str,
) -> AuditDraft:
    rel = _workspace_rel_path(path)
    return AuditDraft(
        category="permission",
        action="permission.write_conflict",
        actor_kind=_actor_kind(recorder, run_id),
        outcome="denied",
        run_id=run_id,
        target_type="file",
        target_ref=rel,
        detail={"path": rel, "claiming_run_id": owner_run_id},
    )


def project_approval_swept(
    recorder: AuditRecorder,
    *,
    tool_names: list[str],
    swept: list[dict[str, str]],
) -> AuditDraft:
    return AuditDraft(
        category="approval",
        action="approval.swept",
        actor_kind="captain",
        outcome="ok",
        target_type="tool",
        target_ref=",".join(sorted(tool_names))[:512] if tool_names else None,
        detail={
            "tool_names": tool_names,
            "swept_count": len(swept),
            "swept": swept,
        },
    )


def project_run_retry(
    recorder: AuditRecorder,
    *,
    run_id: str,
    attempt: int,
    source: str,
    error: str | None = None,
    execution_id: str | None = None,
) -> AuditDraft:
    detail: dict[str, Any] = {"attempt": attempt, "source": source}
    if error:
        detail["error"] = error[:500]
    return AuditDraft(
        category="state",
        action="run.retry",
        actor_kind=_actor_kind(recorder, run_id),
        outcome="ok",
        execution_id=execution_id,
        run_id=run_id,
        detail=detail,
    )


def project_run_deterministic_failure(
    recorder: AuditRecorder,
    *,
    run_id: str,
    error: str | None = None,
    execution_id: str | None = None,
) -> AuditDraft:
    """确定性失败区分 (BL-6): a FAILED run the scheduler classified as non-retryable
    (prompt 超长 / 鉴权 / 余额) was ACCEPTED as final rather than auto-retried. Recorded
    (后端补记) so the delegated-turn audit trail carries the「未盲目重试确定性失败」decision
    — category ``state`` (a handling record, NOT a second ``failure`` row, so the failure
    tally isn't double-counted)."""
    detail: dict[str, Any] = {"reason": "deterministic"}
    if error:
        detail["error"] = error[:500]
    return AuditDraft(
        category="state",
        action="run.deterministic_failure",
        actor_kind=_actor_kind(recorder, run_id),
        outcome="skipped",
        execution_id=execution_id,
        run_id=run_id,
        detail=detail,
    )


def project_run_redirect_ignored(
    recorder: AuditRecorder,
    *,
    run_id: str,
    feedback: str | None = None,
    execution_id: str | None = None,
) -> AuditDraft:
    """跑一半改方向 · 忽略路径 (run_redirect Step 4): a user redirect (立即改此人) could NOT be
    applied mid-run — the targeted worker had already reached a terminal state, or the redirect
    arrived after its delegate batch ended, so the WaveScheduler never cancelled + cold-re-ran it.
    Recorded (后端记录) so the run detail can surface「改方向未生效」and offer an explicit accept,
    instead of the steer silently vanishing. category ``state`` / outcome ``skipped`` (a handling
    record, not a failure) — the wire projection is untouched (no new SSE event)."""
    detail: dict[str, Any] = {"reason": "not_applied"}
    if feedback:
        detail["feedback"] = feedback[:200]
    return AuditDraft(
        category="state",
        action="run.redirect_ignored",
        actor_kind=_actor_kind(recorder, run_id),
        outcome="skipped",
        execution_id=execution_id,
        run_id=run_id,
        detail=detail,
    )


def project_delegate_plan(
    recorder: AuditRecorder,
    *,
    execution_id: str,
    plan,
    captain_run_id: str | None,
) -> AuditDraft:
    tasks = []
    for node in plan.nodes:
        preview, task_hash = task_preview_and_hash(node.task or "")
        tasks.append(
            {
                "run_id": node.run_id,
                "role": node.role,
                "depends_on": list(node.depends_on or []),
                "tools": list(node.tools) if node.tools is not None else None,
                "task": preview,
                "task_hash": task_hash,
            }
        )
    return AuditDraft(
        category="orchestration",
        action="delegate.plan",
        actor_kind="captain",
        outcome="ok",
        execution_id=execution_id,
        run_id=captain_run_id,
        parent_run_id=captain_run_id,
        detail={"tasks": tasks, "node_count": len(plan.nodes)},
    )


def project_replan(
    recorder: AuditRecorder,
    *,
    execution_id: str,
    binds: list[Any],
    steers: list[Any],
    adds: int,
    stop: bool,
) -> AuditDraft:
    return AuditDraft(
        category="orchestration",
        action="replan.applied",
        actor_kind="captain",
        outcome="ok",
        execution_id=execution_id,
        run_id=recorder.captain_run_id,
        detail={
            "binds": binds,
            "steers": steers,
            "adds": adds,
            "stop": stop,
        },
    )


def project_permission_effective(
    recorder: AuditRecorder,
    *,
    execution_id: str | None,
    run_id: str,
    parent_run_id: str | None,
    declared_tools: list[str] | None,
    effective_tools: list[str] | None,
    depth: int,
) -> AuditDraft:
    return AuditDraft(
        category="permission",
        action="permission.effective",
        actor_kind=_actor_kind(recorder, run_id),
        outcome="ok",
        execution_id=execution_id,
        run_id=run_id,
        parent_run_id=parent_run_id,
        detail={
            "declared_tools": declared_tools,
            "effective_tools": effective_tools,
            "depth": depth,
        },
    )


def project_approval_resolved(
    recorder: AuditRecorder,
    *,
    tool_name: str,
    tool_call_id: str,
    decision: str,
    arguments: dict[str, Any],
    run_id: str | None = None,
) -> AuditDraft:
    if decision == "deny":
        action, outcome = "approval.denied", "denied"
    elif decision in {"approve", "approve_always", "approve_always_files"}:
        action, outcome = "approval.granted", "ok"
    else:
        action, outcome = f"approval.{decision}", "ok"
    target_ref = _file_target_from_arguments(tool_name, arguments)
    # actor_kind stays captain/member/system (DB check); 「谁批」lives in detail.
    return AuditDraft(
        category="approval",
        action=action,
        actor_kind=_actor_kind(recorder, run_id),
        outcome=outcome,
        run_id=run_id,
        target_type="file" if target_ref else "tool",
        target_ref=target_ref or tool_call_id,
        detail={
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "decision": decision,
            "decided_by": "user",
        },
    )


def project_approval_timeout(
    recorder: AuditRecorder,
    *,
    tool_name: str,
    tool_call_id: str,
    run_id: str | None = None,
) -> AuditDraft:
    return AuditDraft(
        category="approval",
        action="approval.timeout",
        actor_kind=_actor_kind(recorder, run_id),
        outcome="denied",
        run_id=run_id,
        target_type="tool",
        target_ref=tool_call_id,
        detail={
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "decided_by": "timeout",
        },
    )


def project_circuit_breaker(
    recorder: AuditRecorder,
    *,
    tool_name: str,
    tool_call_id: str,
    rule_id: str,
    verdict: str,
    reason: str,
    run_id: str | None = None,
) -> AuditDraft:
    """Safety circuit-breaker hit (heuristic last line — not a security boundary)."""
    outcome = "denied" if verdict == "deny" else "escalated"
    return AuditDraft(
        category="permission",
        action="permission.circuit_breaker",
        actor_kind=_actor_kind(recorder, run_id),
        outcome=outcome,
        run_id=run_id,
        target_type="tool",
        target_ref=tool_call_id,
        detail={
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "rule_id": rule_id,
            "verdict": verdict,
            "reason": reason[:400],
        },
    )


def project_permission_axes_changed(
    *,
    previous: dict,
    next_axes: dict,
) -> AuditDraft:
    """Session-level axes switch (outside a turn recorder — written via repo)."""
    return AuditDraft(
        category="permission",
        action="permission.axes_changed",
        actor_kind="system",
        outcome="ok",
        target_type="interaction",
        target_ref="permission_axes",
        detail={
            "previous": previous,
            "permission_axes": next_axes,
            "decided_by": "user",
        },
    )


def project_permission_axes_snapshot(
    recorder: AuditRecorder,
    *,
    permission_axes: str,
) -> AuditDraft:
    """Turn-entry snapshot so the security ledger knows the mode in force."""
    return AuditDraft(
        category="permission",
        action="permission.axes_snapshot",
        actor_kind="system",
        outcome="ok",
        run_id=recorder.captain_run_id,
        target_type="interaction",
        target_ref="permission_axes",
        detail={"permission_axes": permission_axes},
    )
