"""Local-mode worker gating tests."""

import asyncio

from agentcore.runtime.graph.session.session import (
    active_coordination,
    clear_active_coordination,
)
from agentcore.runtime.events import EventSink, EventType
from tests.delegate.conftest import (
    Provider,
    capture_gate,
    ctx,
    gate,
    local_ctx,
    tool,
    tool_with_gate,
)


async def _await_solo_drive() -> None:
    """Solo 默认进协调：须等后台 drive 跑完，gate / lifecycle 才落定。"""
    session = active_coordination("e")
    if session is not None and session.drive_task is not None:
        await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination("e")
