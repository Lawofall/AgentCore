"""Cross-turn empty-delegate / unproductive fingerprint (hint withdrawn).

Structured journal signals only. The one-shot ``<上轮重派>`` block is no longer
injected; this module only classifies prior-turn facts.
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
