"""Account-level attention snapshot for GET /v1/fulfill connect seed.

Authority is ``paused_turns`` for this user plus this process's registry hot
cards. No N-conversation recovery scan, no conversations mapper, no FCM.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agentcore.attention.signal import attention_kind_of, attention_title
from agentcore.db.models import PausedTurnRow
from agentcore.runtime.interaction import (
    default_interaction_registry,
    is_hot_user_pending_kind,
)
from agentcore.runtime.turn.runs import turn_runs


def attention_entry(
    *,
    conversation_id: str,
    turn_id: str,
    interaction_id: str,
    kind: str,
    title: str,
) -> dict[str, Any] | None:
    """One snapshot / incremental entry. Same fields as the realtime signal (no extras)."""
    if not conversation_id or not interaction_id:
        return None
    return {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "interaction_id": interaction_id,
        "kind": kind,
        "title": title,
    }


def entry_from_paused_row(row: PausedTurnRow) -> dict[str, Any] | None:
    """Map a durable pause row to an attention entry; skip non-blocking kinds."""
    frame = row.frame if isinstance(row.frame, dict) else {}
    kind = attention_kind_of(str(frame.get("kind") or ""))
    if kind is None:
        return None
    return attention_entry(
        conversation_id=row.conversation_id,
        turn_id=row.message_id,
        interaction_id=str(frame.get("checkpoint_id") or ""),
        kind=kind.value,
        title=attention_title(kind, {"question": str(frame.get("question") or "")}),
    )


def entries_from_registry_hot_cards(user_id: str) -> list[dict[str, Any]]:
    """In-process hot cards whose live run belongs to this user."""
    if not user_id:
        return []
    live_turn_id = {
        run.conversation_id: (run.sink.message_id or "")
        for run in turn_runs.live_runs()
        if run.user_id == user_id
    }
    if not live_turn_id:
        return []
    out: list[dict[str, Any]] = []
    for req in default_interaction_registry().list_pending():
        if req.conversation_id not in live_turn_id:
            continue
        if not is_hot_user_pending_kind(req.kind.value, req.payload):
            continue
        kind = attention_kind_of(req.kind.value)
        if kind is None:
            continue
        entry = attention_entry(
            conversation_id=req.conversation_id,
            turn_id=live_turn_id[req.conversation_id],
            interaction_id=req.id,
            kind=kind.value,
            title=attention_title(kind, req.payload),
        )
        if entry is not None:
            out.append(entry)
    return out


def merge_attention_entries(
    paused_rows: Sequence[PausedTurnRow],
    *,
    user_id: str,
    extra: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """paused_turns (oldest first) then registry hot cards then ``extra``; first id wins."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in paused_rows:
        entry = entry_from_paused_row(row)
        if entry is None:
            continue
        key = entry["interaction_id"]
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    for entry in entries_from_registry_hot_cards(user_id):
        key = entry["interaction_id"]
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    for entry in extra or ():
        key = str(entry.get("interaction_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        entries.append(dict(entry))
    return entries
