"""Observability for context-pipeline caps (lengths / counts only; no bodies)."""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger

logger = get_logger(__name__)


def log_context_capped(
    *,
    site: str,
    original_chars: int | None = None,
    final_chars: int | None = None,
    original_count: int | None = None,
    final_count: int | None = None,
    execution_id: str | None = None,
    **extra: Any,
) -> None:
    """Info-level: a production-path cap actually cut something.

    Event name is a literal so ``sync_log_event_registry`` can see it.
    """
    fields: dict[str, Any] = {"site": site}
    if original_chars is not None:
        fields["original_chars"] = original_chars
    if final_chars is not None:
        fields["final_chars"] = final_chars
    if original_count is not None:
        fields["original_count"] = original_count
    if final_count is not None:
        fields["final_count"] = final_count
    eid = execution_id or _peek_execution_id()
    if eid:
        fields["execution_id"] = eid
    for key, value in extra.items():
        if value is not None:
            fields[key] = value
    logger.info("delegate.context_capped", **fields)


def _peek_execution_id() -> str | None:
    from agentcore.runtime.graph.session.session import current_execution_id

    value = current_execution_id.get()
    return value.strip() if isinstance(value, str) and value.strip() else None
