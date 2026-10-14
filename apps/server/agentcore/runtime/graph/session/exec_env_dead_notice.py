"""Force a quiet user-visible sentence when local exec env is sticky-dead.

Mirrors ``channel_dead_notice``: model steers alone leave users without
「本机暂时跑不了命令」. Stamp the live coordination session and emit one
``content_delta`` when a host sink is still open.
"""

from __future__ import annotations

import contextlib

from agentcore.core.logging import get_logger
from agentcore.workspace.limits import exec_env_dead_user_visible

logger = get_logger(__name__)


def mark_and_emit_exec_env_dead_user_notice(
    *, execution_id: str | None = None, reason_code: str | None = None
) -> None:
    """Stamp session + one-shot host ``content_delta`` (never raises).

    ``reason_code`` is the exec-env probe verdict (``exec_env_no_interpreter`` /
    ``exec_env_probe_timeout`` / ``exec_env_spawn_denied``); it picks the honest
    sentence and is remembered for the harvest fallback. Anything else (idle
    hang, unclassified probe) falls back to the cause-free line.
    """
    try:
        from agentcore.runtime.graph.session.session import active_coordination
        from agentcore.runtime.events import content_delta

        session = active_coordination(execution_id)
        if session is None:
            return
        session.exec_env_dead = True
        code = (reason_code or "").strip() or None
        if code:
            session.exec_env_dead_reason = code
        if session.exec_env_dead_user_notice_emitted:
            return
        session.exec_env_dead_user_notice_emitted = True
        sink = session.event_sink
        if sink is None or getattr(sink, "_closed", False):
            return
        with contextlib.suppress(Exception):
            sink.emit(content_delta(exec_env_dead_user_visible(code) + "\n\n"))
        logger.info(
            "coordination.exec_env_dead_user_notice",
            execution_id=session.execution_id,
            code=code,
        )
    except Exception:  # noqa: BLE001 — notice must never break the tool path
        logger.warning(
            "coordination.exec_env_dead_user_notice_failed",
            execution_id=execution_id,
            exc_info=True,
        )
