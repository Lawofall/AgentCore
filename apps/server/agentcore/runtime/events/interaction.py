"""User-interaction SSE event factories (approval / checkpoint / plan_review / escalation)."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.checkpoints import AskCheckpointIntent
from agentcore.runtime.events.types import EventType, SSEEvent


def approval_required(
    *,
    approval_id: str,
    conversation_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> SSEEvent:
    return SSEEvent(
        type=EventType.APPROVAL_REQUIRED,
        payload={
            "approval_id": approval_id,
            "conversation_id": conversation_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": arguments,
        },
    )


def approval_resolved(*, approval_id: str, tool_call_id: str, decision: str) -> SSEEvent:
    return SSEEvent(
        type=EventType.APPROVAL_RESOLVED,
        payload={
            "approval_id": approval_id,
            "tool_call_id": tool_call_id,
            "decision": decision,
        },
    )


def checkpoint_required(
    *,
    checkpoint_id: str,
    conversation_id: str,
    question: str,
    assumptions: list[dict[str, Any]] | None = None,
    questions: list[dict[str, Any]] | None = None,
    intent: AskCheckpointIntent | None = None,
    browser_login: bool | None = None,
) -> SSEEvent:
    payload: dict[str, Any] = {
        "checkpoint_id": checkpoint_id,
        "conversation_id": conversation_id,
        "question": question,
        "assumptions": assumptions or [],
        "questions": questions or [],
    }
    if intent is not None:
        payload["intent"] = intent
    if browser_login is True:
        payload["browser_login"] = True
    return SSEEvent(type=EventType.CHECKPOINT_REQUIRED, payload=payload)


def checkpoint_resolved(
    *, checkpoint_id: str, decision: str, note: str = "", selected: list[str] | None = None
) -> SSEEvent:
    return SSEEvent(
        type=EventType.CHECKPOINT_RESOLVED,
        payload={
            "checkpoint_id": checkpoint_id,
            "decision": decision,
            "note": note,
            "selected": selected or [],
        },
    )


def plan_review_required(
    *,
    checkpoint_id: str,
    conversation_id: str,
    steps: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    ceo_review: dict[str, Any] | None = None,
) -> SSEEvent:
    payload: dict[str, Any] = {
        "checkpoint_id": checkpoint_id,
        "conversation_id": conversation_id,
        "steps": steps,
        "pending": pending,
    }
    if ceo_review is not None:
        payload["ceo_review"] = ceo_review
    return SSEEvent(
        type=EventType.PLAN_REVIEW_REQUIRED,
        payload=payload,
    )


def plan_review_resolved(*, checkpoint_id: str, decision: str, note: str = "") -> SSEEvent:
    return SSEEvent(
        type=EventType.PLAN_REVIEW_RESOLVED,
        payload={
            "checkpoint_id": checkpoint_id,
            "decision": decision,
            "note": note,
        },
    )


def stage_card_required(
    *,
    stage_card_id: str,
    conversation_id: str,
    motion: str,
    sides: list[dict[str, Any]],
    form: str,
    rationale: str,
    fact_pointers: list[str] | None = None,
    thorough: bool = True,
    max_rounds: int = 5,
    note: str | None = None,
    host_execution_id: str | None = None,
    synthesizer_run_id: str | None = None,
    host_message_id: str | None = None,
) -> SSEEvent:
    """阶段推进卡登记（批 B）：幕 1 收尾后耐久展示，不挂起回合。

    可选宿主三元组（机制直传，旧客户端忽略）：开辩时锚定幕 1 图，免再查。
    """
    payload: dict[str, Any] = {
        "stage_card_id": stage_card_id,
        "conversation_id": conversation_id,
        "motion": motion,
        "sides": list(sides or []),
        "form": form,
        "rationale": rationale,
        "fact_pointers": list(fact_pointers or []),
        "thorough": thorough,
        "max_rounds": max_rounds,
    }
    if note is not None:
        payload["note"] = note
    for key, val in (
        ("host_execution_id", host_execution_id),
        ("synthesizer_run_id", synthesizer_run_id),
        ("host_message_id", host_message_id),
    ):
        text = (val or "").strip() if isinstance(val, str) else ""
        if text:
            payload[key] = text
    return SSEEvent(type=EventType.STAGE_CARD_REQUIRED, payload=payload)


def stage_card_resolved(
    *,
    stage_card_id: str,
    decision: str,
    note: str = "",
    motion_override: str | None = None,
) -> SSEEvent:
    payload: dict[str, Any] = {
        "stage_card_id": stage_card_id,
        "decision": decision,
        "note": note or "",
    }
    if motion_override is not None:
        payload["motion_override"] = motion_override
    return SSEEvent(type=EventType.STAGE_CARD_RESOLVED, payload=payload)


def escalation_required(
    run_id: str,
    agent_id: str,
    *,
    escalation_id: str,
    question: str,
    assumption: str,
    questions: list[dict[str, Any]] | None = None,
    kind: str = "normal",
    awaiting: str = "user",
    browser_login: bool | None = None,
    ownership_paths: list[str] | None = None,
    lock_owner_run_id: str | None = None,
    timeout_seconds: float | None = None,
) -> SSEEvent:
    """``question`` is the worker's headline ask; ``questions`` is the optional
    structured-fork list (同 ask_user 的 questions) the card renders as choice/text so
    the user one-taps a decision instead of free-typing. Journaled, so the structured
    prompt replays inline on reload. ``kind`` is the escalate taxonomy
    (normal / scope / dep), orthogonal to blocking. ``awaiting`` is ``user`` (经典可答卡)
    or ``ceo`` (协调模式等主管仲裁，初始不作为用户可答卡).
    ``browser_login`` (narrow D16 exception): when true, the pending escalate allows
    user browser takeover while the turn is still running. Absent/false on old streams.
    ``ownership_paths`` / ``lock_owner_run_id``: write-lock conflict 结构化裁决（移交写权）。
    ``timeout_seconds``: the wall-clock ceiling this suspend actually got. ABSENT is the
    default deployment (D2 ``checkpoint_timeout_seconds=None``) = waits indefinitely, so a
    client must NOT promise「未答则按假设继续」unless this field carries a value.
    """
    who = awaiting if awaiting in ("user", "ceo") else "user"
    payload: dict[str, Any] = {
        "escalation_id": escalation_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "question": question,
        "assumption": assumption,
        "questions": questions or [],
        "kind": kind if kind in ("normal", "scope", "dep") else "normal",
        "awaiting": who,
    }
    # Absent-forward-compat: only emit when explicitly true (old clients ignore unknown;
    # generators omit false so old journals stay bit-identical).
    if browser_login is True:
        payload["browser_login"] = True
    paths = [p for p in (ownership_paths or []) if isinstance(p, str) and p.strip()]
    if paths:
        payload["ownership_paths"] = paths
    lock = (lock_owner_run_id or "").strip()
    if lock:
        payload["lock_owner_run_id"] = lock
    # Only a real ceiling travels: absent ⇒ 无限期等待, which is what the card must say.
    if isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0:
        payload["timeout_seconds"] = float(timeout_seconds)
    return SSEEvent(
        type=EventType.ESCALATION_REQUIRED,
        payload=payload,
    )


def escalation_resolved(
    run_id: str,
    agent_id: str,
    *,
    escalation_id: str,
    status: str,
    answer: str,
    arbitrated_by: str | None = None,
    via_user: bool | None = None,
) -> SSEEvent:
    # Wire status is resolved | assumed | timed_out | orphaned.
    if status not in ("resolved", "assumed", "timed_out", "orphaned"):
        status = "timed_out"
    payload: dict[str, Any] = {
        "escalation_id": escalation_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "status": status,
        "answer": answer,
    }
    if arbitrated_by in ("user", "ceo"):
        payload["arbitrated_by"] = arbitrated_by
    if via_user is not None and arbitrated_by == "ceo":
        payload["via_user"] = bool(via_user)
    return SSEEvent(
        type=EventType.ESCALATION_RESOLVED,
        payload=payload,
    )


def interaction_orphaned(
    *, interaction_id: str, kind: str, reason: str | None = None
) -> SSEEvent:
    """pending 交互失效。``kind`` ∈ 热路 kind / stage_card / team_preview。"""
    payload: dict[str, Any] = {"interaction_id": interaction_id, "kind": kind}
    text = (reason or "").strip()
    if text:
        payload["reason"] = text
    return SSEEvent(
        type=EventType.INTERACTION_ORPHANED,
        payload=payload,
    )
