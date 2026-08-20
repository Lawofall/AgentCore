"""Replay identity ≠ recording identity (回放身份与录制身份显式分离).

A tape faithfully keeps the ids of the run it was recorded from, but every replay is
a NEW execution. User-interaction identities (checkpoint / approval / ask / escalation
/ authorization) are keyed GLOBALLY on clients — the desktop InteractionStore's
cross-conversation ``byId`` map ("resolved" tombstones never resurrect; a pending id
keeps its first payload) and ``pausedTurns.removeByCheckpoint`` both match on the raw
id — so re-emitting a recorded id makes the SECOND replay's kickoff card silently
swallowed (开工卡永不出现). Every ``*_required`` id must therefore be reminted per
replay, deterministically from ``(this turn's message_id, recorded id)`` so the send
and resume legs of one turn agree and re-folds stay stable.

When to remint is a **consumer-dimension** policy (提案 §6 问题 6)：SINK (B) 默认
调用本模块；FOLD (A / ``#/preview``) 永不重铸。调用点在
:func:`agentcore.replay.prepare.prepare_replay_source`，不在 player 内联。

``run_id`` / ``execution_id`` / ``tool_call_id`` intentionally stay AS RECORDED: every
store scopes them per message (no cross-replay keying), and their strings carry
structure (``debate_<exec>_r1_<side>``) that projection code parses — reminting them
would break debate-beat folds for zero collision benefit.
"""

from __future__ import annotations

import uuid
from typing import Any

_REPLAY_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "agentcore:demo-tape:replay-identity")

# Top-level payload keys carrying user-interaction identities (see module docstring).
INTERACTION_ID_KEYS = frozenset(
    {
        "checkpoint_id",
        "approval_id",
        "escalation_id",
        "authorization_id",
        "interaction_id",
    }
)


def replay_interaction_id(recorded_id: str, *, message_id: str) -> str:
    """This replay's id for a recorded interaction id (deterministic per turn)."""
    return str(uuid.uuid5(_REPLAY_ID_NAMESPACE, f"{message_id}:{recorded_id}"))


def remint_interaction_ids(
    events: list[dict[str, Any]], *, message_id: str
) -> list[dict[str, Any]]:
    """Copy of ``events`` with every top-level interaction-id payload key reminted.

    Events without interaction ids pass through by reference (the play loop copies
    each payload before emitting anyway); touched events get a fresh payload dict so
    the loaded tape document is never mutated.
    """
    out: list[dict[str, Any]] = []
    for ev in events:
        payload = ev.get("payload")
        if not isinstance(payload, dict):
            out.append(ev)
            continue
        keys = [k for k in INTERACTION_ID_KEYS if payload.get(k)]
        if not keys:
            out.append(ev)
            continue
        minted = dict(payload)
        for k in keys:
            minted[k] = replay_interaction_id(str(payload[k]), message_id=message_id)
        out.append({**ev, "payload": minted})
    return out
