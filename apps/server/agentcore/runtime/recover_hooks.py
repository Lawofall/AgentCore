"""Hooks for crash-lease recover (injection seam for production + tests).

Production installs :func:`agentcore.conversation.crash_delegate.production_crash_delegate_factory`
via ``set_crash_delegate_factory`` in the app lifespan. When the factory is unset
(tests / miswired boot), ``build_crash_delegate_tool`` warns and the sweeper
salvages to ``interrupted``. Unit tests may inject a fake tool to prove
``recover_turn`` seeds ``WaveScheduler``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger

if TYPE_CHECKING:
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.leases.model import TurnLeaseRow
    from agentcore.runtime.turn.state import TurnState
    from agentcore.tools.builtin.graph import GraphTool

logger = get_logger(__name__)

# Tests / future production assign: async (lease, state, *, sink) -> GraphTool | None
_crash_delegate_factory: Any = None


def set_crash_delegate_factory(factory: Any) -> None:
    """Install a test/production factory for crash-lease GraphTool construction."""
    global _crash_delegate_factory
    _crash_delegate_factory = factory


async def build_crash_delegate_tool(
    lease: TurnLeaseRow,
    state: TurnState,
    *,
    sink: EventSink,
) -> GraphTool | None:
    """Return a GraphTool for crash redrive, or ``None`` when the factory is unset.

    Unwired production path must be loud: callers salvage to ``interrupted`` rather
    than silently dropping unfinished DAG work.
    """
    if _crash_delegate_factory is not None:
        return await _crash_delegate_factory(lease, state, sink=sink)
    logger.warning(
        "recover.crash_delegate_unwired",
        message_id=lease.message_id,
        conversation_id=getattr(lease, "conversation_id", None),
        unfinished=len(state.unfinished_run_ids),
        hint=(
            "set_crash_delegate_factory was never installed at startup; "
            "crash redrive will salvage to interrupted"
        ),
    )
    return None
