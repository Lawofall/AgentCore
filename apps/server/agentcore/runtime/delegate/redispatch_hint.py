"""Cross-turn one-shot soft hint: prior empty-delegate / unproductive fingerprint.

When the previous turn ended with structured failure (``finish_reason=unproductive``
or a failed ``delegate`` whose declaration gate is ``empty``), the next fresh CEO
turn gets a single ignorable nudge to re-issue top-level non-empty ``tasks`` —
history replays no tool I/O, so the fingerprint must ride the volatile prompt tail.

Hard rules (intercept-discipline):
- Structured signals only — never scan user「继续」/ long free text for intent.
- One-shot soft hint; no cumulative counters; no hard reject; no Flash→Pro.
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.delegate.playbook_declaration import (
    declaration_reject_gate,
)
from agentcore.runtime.engine.tool_exec import TOOL_FAILED_MARKER
from agentcore.runtime.events.types import FinishReason
from agentcore.runtime.facts import FactKind
from agentcore.runtime.journal.entries import KIND_TURN_END

_REDISPATCH_HINT = (
    "<上轮重派>\n"
    "【上轮委派未落地】上轮出现空委派或无产出收口（结构化指纹）。"
    "本提示一次性、可忽略，不挡原请求。"
    "再发一次非空 `tasks` 的 `delegate`。\n"
    "</上轮重派>"
)


def _clean_tool_result(result: str) -> str:
    """Strip the model-facing failure trailer so gate classifiers see the raw error."""
    return (result or "").replace(TOOL_FAILED_MARKER, "").strip()


def prior_turn_has_redispatch_fingerprint(entries: list[dict[str, Any]] | None) -> bool:
    """True when journal facts carry unproductive finish or empty-gate failed delegate.

    Two stable conditions only — no free-text heuristics on user or assistant prose.
    """
    if not entries:
        return False
    for entry in entries:
        kind = entry.get("kind") or ""
        raw_payload = entry.get("payload")
        payload: dict[str, Any] = (
            raw_payload if isinstance(raw_payload, dict) else {}
        )
        if kind == KIND_TURN_END:
            if payload.get("finish_reason") == FinishReason.UNPRODUCTIVE.value:
                return True
            continue
        if kind != FactKind.TOOL_CALL.value:
            continue
        if payload.get("name") != "delegate":
            continue
        if payload.get("success") is not False:
            continue
        cleaned = _clean_tool_result(str(payload.get("result") or ""))
        if declaration_reject_gate(cleaned) == "empty":
            return True
    return False


def render_redispatch_hint() -> str:
    """The verbatim soft-hint block (always the same wording when injected)."""
    return _REDISPATCH_HINT.strip()


async def _load_latest_prior_journal(
    *,
    conversation_id: str,
    exclude_turn_id: str | None,
) -> list[dict[str, Any]]:
    """Newest other turn's journal entries for ``conversation_id``, or ``[]``."""
    cid = (conversation_id or "").strip()
    if not cid:
        return []
    exclude = (exclude_turn_id or "").strip()
    try:
        from sqlalchemy import text

        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import TurnJournalRepository

        async with async_session_factory() as session:
            exclusion = "AND turn_id != :ex" if exclude else ""
            result = await session.execute(
                text(
                    f"""
                    SELECT turn_id
                    FROM turn_journal
                    WHERE conversation_id = :cid
                      {exclusion}
                    ORDER BY created_at DESC, seq DESC
                    LIMIT 1
                    """
                ),
                {"cid": cid, "ex": exclude} if exclude else {"cid": cid},
            )
            row = result.first()
            if not row or not row[0]:
                return []
            turn_id = str(row[0])
            repo = TurnJournalRepository(session)
            return await repo.load(turn_id)
    except Exception:  # noqa: BLE001 — soft hint must never break the turn
        return []


async def build_prior_failure_redispatch_hint(
    *,
    conversation_id: str,
    exclude_message_id: str | None = None,
) -> str:
    """``<上轮重派>`` block when the prior turn fingerprints, else ``\"\"``.

    ``exclude_message_id`` drops the in-flight assistant turn (same as recent-graph /
    delivery reinject). Does not read or branch on the current user message.
    """
    entries = await _load_latest_prior_journal(
        conversation_id=conversation_id,
        exclude_turn_id=exclude_message_id,
    )
    if not prior_turn_has_redispatch_fingerprint(entries):
        return ""
    return render_redispatch_hint()
