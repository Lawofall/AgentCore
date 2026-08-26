"""Coarse ``ToolCallFact`` fields derived from a ``ToolAttempt``."""

from __future__ import annotations

from agentcore.runtime.facts import CROSS_TURN_RETRY_KEY, normalize_cross_turn_retry
from agentcore.runtime.loop_controller import ERROR_CLASS_VALIDATION, ToolAttempt


def tool_call_fact_code(attempt: ToolAttempt) -> str:
    """Coarse failure code for ``ToolCallFact`` (empty when unknown / success)."""
    if attempt.success:
        return ""
    meta = attempt.meta or {}
    raw = meta.get("code")
    code = raw.strip() if isinstance(raw, str) else ""
    tool = (attempt.tool_name or "").strip()
    # Git wall-clock timeout must not collide with exec idle hang buckets.
    if tool == "git" and code == "timeout":
        return "git_timeout"
    if code:
        return code
    if attempt.parse_failure:
        return "schema"
    if meta.get("error_class") == ERROR_CLASS_VALIDATION:
        return "schema"
    return ""


def tool_call_fact_cross_turn_retry(attempt: ToolAttempt) -> str:
    """Copy a stamped ``cross_turn_retry``; never infer from ``error_class`` / code.

    Empty = unknown (omit on the fact). Success always empty.
    """
    if attempt.success:
        return ""
    return normalize_cross_turn_retry((attempt.meta or {}).get(CROSS_TURN_RETRY_KEY))
