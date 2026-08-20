"""Orphan / fold / audit projector (提问确认交互统一 P1)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentcore.runtime.audit.projector import project_journal_entry
from agentcore.runtime.interaction import InteractionKind, InteractionRegistry
from agentcore.runtime.interaction_orphan import (
    orphan_live_turn_hot_pending,
    orphan_registry_pending,
)
from agentcore.runtime.journal.pending_interactions import fold_pending_interactions


@pytest.mark.asyncio
async def test_orphan_registry_hot_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = InteractionRegistry()
    reg.create("a1", "c1", kind=InteractionKind.APPROVAL, payload={"tool_name": "x"})
    reg.create(
        "e-ceo",
        "c1",
        kind=InteractionKind.ESCALATION,
        payload={"awaiting": "ceo"},
    )
    reg.create(
        "e-user",
        "c1",
        kind=InteractionKind.ESCALATION,
        payload={"awaiting": "user"},
    )

    written: list[tuple[str, str]] = []

    async def fake_emit(**kwargs):
        written.append((kwargs["interaction_id"], kwargs["kind"]))

    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.emit_orphan_fact",
        fake_emit,
    )
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.default_interaction_registry",
        lambda: reg,
    )

    ids = await orphan_registry_pending("c1", turn_id="t1")
    assert set(ids) == {"a1", "e-user"}
    assert reg.get("e-ceo") is not None
    assert reg.get("a1") is None
    assert ("a1", "approval") in written


@pytest.mark.asyncio
async def test_orphan_prefer_direct_without_contextvar_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop 路径：无 ContextVar writer 但有 turn_id → prefer_direct 写出 journal。"""
    reg = InteractionRegistry()
    reg.create("a1", "c-stop", kind=InteractionKind.APPROVAL, payload={"tool_name": "x"})

    direct_calls: list[dict] = []

    async def fake_direct(**kwargs):
        direct_calls.append(kwargs)

    async def fake_prewrite(_event):
        return False

    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.default_interaction_registry",
        lambda: reg,
    )
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.prewrite_settlement_direct",
        fake_direct,
    )
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.prewrite_settlement",
        fake_prewrite,
    )

    ids = await orphan_registry_pending(
        "c-stop", turn_id="msg-stop", prefer_direct=True
    )
    assert ids == ["a1"]
    assert len(direct_calls) == 1
    assert direct_calls[0]["turn_id"] == "msg-stop"
    assert direct_calls[0]["conversation_id"] == "c-stop"
    assert direct_calls[0]["event"].type.value == "interaction_orphaned"
    assert reg.get("a1") is None


@pytest.mark.asyncio
async def test_orphan_live_turn_hot_pending_uses_live_sink_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """regenerate/retry/stop：取活 turn sink.message_id，非路径上的用户 message_id。"""
    calls: list[dict] = []

    async def fake_orphan(cid: str, **kwargs):
        calls.append({"conversation_id": cid, **kwargs})
        return ["a1"]

    live_sink = MagicMock()
    live_sink.message_id = "live-assistant-turn"
    live = MagicMock()
    live.sink = live_sink

    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.orphan_registry_pending",
        fake_orphan,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.runs.turn_runs.get",
        lambda _cid: live,
    )
    monkeypatch.setattr(
        "agentcore.core.log_context.get_log_value",
        lambda _k: "trace-1",
    )

    ids = await orphan_live_turn_hot_pending("conv-x")
    assert ids == ["a1"]
    assert len(calls) == 1
    assert calls[0]["conversation_id"] == "conv-x"
    assert calls[0]["turn_id"] == "live-assistant-turn"
    assert calls[0]["prefer_direct"] is True
    assert calls[0]["sink"] is live_sink
    assert calls[0]["trace_id"] == "trace-1"


@pytest.mark.asyncio
async def test_orphan_live_turn_hot_pending_no_live_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    async def fake_orphan(cid: str, **kwargs):
        calls.append({"conversation_id": cid, **kwargs})
        return []

    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.orphan_registry_pending",
        fake_orphan,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.runs.turn_runs.get",
        lambda _cid: None,
    )
    monkeypatch.setattr(
        "agentcore.core.log_context.get_log_value",
        lambda _k: None,
    )

    await orphan_live_turn_hot_pending("conv-empty")
    assert calls[0]["turn_id"] is None
    assert calls[0]["prefer_direct"] is False
    assert calls[0]["sink"] is None


def test_projector_accepts_timed_out_and_orphaned() -> None:
    recorder = MagicMock()
    recorder.user_id = "u"
    recorder.conversation_id = "c"
    recorder.turn_id = "t"

    for status in ("timed_out", "orphaned"):
        draft = project_journal_entry(
            recorder,
            {
                "kind": "escalation_resolved",
                "payload": {
                    "escalation_id": "e1",
                    "run_id": "r",
                    "agent_id": "a",
                    "status": status,
                    "answer": "",
                },
            },
        )
        assert draft is not None
        assert draft.outcome == "denied"
        assert draft.action == "escalate.resolved"


def test_fold_three_hot_kinds_all_pending() -> None:
    entries = [
        {
            "kind": "approval_required",
            "payload": {
                "approval_id": "a",
                "conversation_id": "c",
                "tool_call_id": "a",
                "tool_name": "t",
                "arguments": {},
            },
        },
        {
            "kind": "escalation_required",
            "payload": {
                "escalation_id": "e",
                "run_id": "r",
                "agent_id": "a",
                "question": "q",
                "assumption": "x",
            },
        },
    ]
    pending = fold_pending_interactions(entries)
    assert {p.kind for p in pending} == {
        "approval",
        "escalation",
    }
