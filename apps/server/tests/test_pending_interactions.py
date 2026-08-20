"""Pending-interactions journal fold + recovery (提问确认交互统一 P1)."""

from __future__ import annotations

import pytest

from agentcore.runtime.journal.pending_interactions import (
    fold_pending_interactions,
)


def test_fold_pending_opens_on_required_closes_on_resolved() -> None:
    entries = [
        {
            "kind": "approval_required",
            "payload": {
                "approval_id": "a1",
                "conversation_id": "c",
                "tool_call_id": "a1",
                "tool_name": "file_write",
                "arguments": {},
            },
        },
        {
            "kind": "escalation_required",
            "payload": {
                "escalation_id": "e1",
                "run_id": "r1",
                "agent_id": "a",
                "question": "q",
                "assumption": "x",
                "awaiting": "user",
            },
        },
        {
            "kind": "approval_resolved",
            "payload": {"approval_id": "a1", "tool_call_id": "a1", "decision": "approve"},
        },
    ]
    pending = fold_pending_interactions(entries, message_id="msg-1")
    assert len(pending) == 1
    assert pending[0].kind == "escalation"
    assert pending[0].id == "e1"
    assert pending[0].message_id == "msg-1"
    assert pending[0].payload["question"] == "q"


def test_fold_pending_orphaned_closes() -> None:
    entries = [
        {
            "kind": "approval_required",
            "payload": {
                "approval_id": "d1",
                "conversation_id": "c",
                "tool_call_id": "tc1",
                "tool_name": "code_execute",
                "arguments": {},
            },
        },
        {
            "kind": "interaction_orphaned",
            "payload": {"interaction_id": "d1", "kind": "approval"},
        },
    ]
    assert fold_pending_interactions(entries) == []


def test_fold_pending_skips_awaiting_ceo() -> None:
    entries = [
        {
            "kind": "escalation_required",
            "payload": {
                "escalation_id": "e-ceo",
                "run_id": "r",
                "agent_id": "a",
                "question": "q",
                "assumption": "x",
                "awaiting": "ceo",
            },
        },
    ]
    assert fold_pending_interactions(entries) == []


@pytest.mark.asyncio
async def test_interaction_registry_timeout_none_waits() -> None:
    """timeout=None must not raise TimeoutError immediately."""
    import asyncio

    from agentcore.runtime.interaction import InteractionKind, InteractionRegistry

    reg = InteractionRegistry()

    async def resolve_soon() -> None:
        await asyncio.sleep(0.05)
        reg.resolve("id-1", "ok", conversation_id="c")

    task = asyncio.create_task(resolve_soon())
    result = await reg.suspend(
        "id-1",
        "c",
        kind=InteractionKind.APPROVAL,
        payload={},
        timeout=None,
    )
    await task
    assert result == "ok"
