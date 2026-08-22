"""Integration: permission.axes_changed persists via telemetry pool."""

from uuid import uuid4

import pytest

from agentcore.runtime.audit.permission_events import record_permission_axes_change


@pytest.mark.asyncio
async def test_record_permission_axes_change_persists(session_factory, monkeypatch):
    monkeypatch.setattr(
        "agentcore.runtime.audit.permission_events.telemetry_session_factory",
        session_factory,
    )
    user_id = str(uuid4())
    conversation_id = str(uuid4())
    previous = {"file_write": "session", "command": "ask", "host": "off"}
    next_axes = {"file_write": "session", "command": "auto", "host": "session"}
    await record_permission_axes_change(
        user_id=user_id,
        conversation_id=conversation_id,
        previous=previous,
        next_axes=next_axes,
    )
    async with session_factory() as session:
        from agentcore.db.repositories import AgentAuditEventRepository

        rows = await AgentAuditEventRepository(session).list_for_conversation(
            conversation_id=conversation_id
        )
    assert len(rows) == 1
    assert rows[0].action == "permission.axes_changed"
    assert rows[0].category == "permission"
    assert rows[0].detail["previous"] == previous
    assert rows[0].detail["permission_axes"] == next_axes
    assert rows[0].detail["decided_by"] == "user"
