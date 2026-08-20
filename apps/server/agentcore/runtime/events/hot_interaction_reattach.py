"""Re-emit still-open hot-path ``*_required`` frames on SSE attach / recovery.

APPROVAL / user-facing ESCALATION are journaled (DURABLE), but attach historically
only re-hung EPHEMERAL CLIENT_TOOL. After a refresh, a journal recovery race or
empty live queue can leave the UI without an answerable card while the in-process
:class:`InteractionRegistry` still holds open Futures. Replaying ``list_pending``
hot kinds closes that gap.

Done / cancelled / discarded entries are absent from ``list_pending`` and are
not re-sent. ``awaiting=ceo`` escalations stay CEO-only (not user-answerable).
Cold-path kinds and CLIENT_TOOL are skipped here (CLIENT_TOOL has its own
reattach module).
"""

from __future__ import annotations

from agentcore.runtime.events.interaction import (
    approval_required,
    escalation_required,
)
from agentcore.runtime.events.types import SSEEvent
from agentcore.runtime.interaction import InteractionKind, InteractionRequest
from agentcore.runtime.journal.pending_interactions import PendingInteraction

_HOT_REATTACH_KINDS = frozenset(
    {
        InteractionKind.APPROVAL,
        InteractionKind.ESCALATION,
    }
)


def build_hot_interaction_required(req: InteractionRequest) -> SSEEvent | None:
    """Rebuild the wire ``*_required`` SSE for one still-open hot interaction."""
    if req.kind not in _HOT_REATTACH_KINDS:
        return None
    if req.future.done():
        return None

    payload = dict(req.payload or {})

    if req.kind is InteractionKind.APPROVAL:
        tool_call_id = str(payload.get("tool_call_id") or req.id)
        arguments = payload.get("arguments")
        return approval_required(
            approval_id=req.id,
            conversation_id=req.conversation_id,
            tool_call_id=tool_call_id,
            tool_name=str(payload.get("tool_name") or ""),
            arguments=dict(arguments) if isinstance(arguments, dict) else {},
        )

    # ESCALATION — skip CEO-arbitration cards (not user-answerable).
    awaiting = payload.get("awaiting")
    if awaiting == "ceo":
        return None
    who = awaiting if awaiting in ("user", "ceo") else "user"
    questions = payload.get("questions")
    ownership = payload.get("ownership_paths")
    ceiling = payload.get("timeout_seconds")
    return escalation_required(
        str(payload.get("run_id") or ""),
        str(payload.get("agent_id") or ""),
        escalation_id=req.id,
        question=str(payload.get("question") or ""),
        assumption=str(payload.get("assumption") or ""),
        questions=list(questions) if isinstance(questions, list) else None,
        kind=str(payload.get("kind") or "normal"),
        awaiting=who,
        browser_login=True if payload.get("browser_login") is True else None,
        ownership_paths=list(ownership) if isinstance(ownership, list) else None,
        lock_owner_run_id=(
            str(payload["lock_owner_run_id"])
            if isinstance(payload.get("lock_owner_run_id"), str)
            else None
        ),
        timeout_seconds=(
            float(ceiling) if isinstance(ceiling, (int, float)) and ceiling > 0 else None
        ),
    )


def pending_hot_interaction_events(conversation_id: str) -> list[SSEEvent]:
    """Open hot-path ``*_required`` frames for one conversation (attach re-hang)."""
    from agentcore.runtime.interaction import default_interaction_registry

    out: list[SSEEvent] = []
    for req in default_interaction_registry().list_pending(conversation_id):
        event = build_hot_interaction_required(req)
        if event is not None:
            out.append(event)
    return out


def registry_hot_pending(
    conversation_id: str,
    *,
    message_id: str = "",
) -> list[PendingInteraction]:
    """Still-open hot cards from the in-process registry (recovery merge source).

    Payload matches the wire ``*_required`` body (approval fills
    ``approval_id`` / ``conversation_id``). ``message_id`` is the live-run sink
    id when known, else ``""``.
    """
    from agentcore.runtime.interaction import default_interaction_registry

    out: list[PendingInteraction] = []
    for req in default_interaction_registry().list_pending(conversation_id):
        event = build_hot_interaction_required(req)
        if event is None:
            continue
        out.append(
            PendingInteraction(
                kind=req.kind.value,
                id=req.id,
                message_id=message_id,
                payload=dict(event.payload),
            )
        )
    return out
