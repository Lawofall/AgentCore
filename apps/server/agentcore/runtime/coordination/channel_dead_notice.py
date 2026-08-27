"""Force a user-visible honest sentence when the local workspace channel dies.

Retire / circuit-breaker soft steers are model-facing only. Detached harvest and
silent CEO wait both left users without the channel-dead user-visible line. This module stamps
the live coordination session and, when a host sink is still open, emits a short
``content_delta`` once.
"""

from __future__ import annotations

import contextlib

from agentcore.core.logging import get_logger
from agentcore.workspace.limits import CHANNEL_DEAD_USER_VISIBLE

logger = get_logger(__name__)


def mark_and_emit_channel_dead_user_notice(*, execution_id: str | None = None) -> None:
    """Stamp session + one-shot host ``content_delta`` (never raises)."""
    try:
        from agentcore.runtime.coordination.session import active_coordination
        from agentcore.runtime.events import content_delta

        session = active_coordination(execution_id)
        if session is None:
            return
        session.workspace_channel_dead = True
        if session.channel_dead_user_notice_emitted:
            return
        session.channel_dead_user_notice_emitted = True
        sink = session.event_sink
        if sink is None or getattr(sink, "_closed", False):
            return
        with contextlib.suppress(Exception):
            sink.emit(content_delta(CHANNEL_DEAD_USER_VISIBLE + "\n\n"))
        logger.info(
            "coordination.channel_dead_user_notice",
            execution_id=session.execution_id,
        )
    except Exception:  # noqa: BLE001 — notice must never break the tool path
        logger.warning(
            "coordination.channel_dead_user_notice_failed",
            execution_id=execution_id,
            exc_info=True,
        )
