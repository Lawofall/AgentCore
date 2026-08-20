"""Same-batch interaction cards: one SUSPEND must not skip sibling results.

cid 9cd54cf1-3cb1-416c-8af6-c08bc7417e02: two parallel ask_user cards; settling
the first wrote the engine sibling-skipped placeholder onto the second, so the
authorization card vanished and the model had to re-issue it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.facts import (
    FactKind,
    LlmCallFact,
    RoundBoundaryFact,
    TurnFactLog,
    TurnStartedFact,
    current_fact_log,
)
from agentcore.runtime.pipeline.resume.settle import (
    append_resumed_tool_results,
    next_pending_ask_user_suspension,
    persist_resumed_tool_results,
    unclosed_tool_call_ids,
)
from agentcore.runtime.suspension import AskUserSuspension, captain_transcript
from agentcore.runtime.suspension.capture import persist_suspension_capture

_SKIPPED = "（该并行工具调用在本回合暂停时未保留结果，已跳过。）"


def _ask_calls() -> list[ToolCall]:
    return [
        ToolCall(
            id="ask_a",
            function=ToolCallFunction(
                name="ask_user", arguments='{"question":"先确认范围？"}'
            ),
        ),
        ToolCall(
            id="ask_b",
            function=ToolCallFunction(
                name="ask_user", arguments='{"question":"区外目录写入授权"}'
            ),
        ),
    ]


def _assistant_with_asks() -> LLMMessage:
    return LLMMessage(role="assistant", content=None, tool_calls=_ask_calls())


def _frame(*, checkpoint_id: str = "cp-a", tool_call_id: str = "ask_a") -> AskUserSuspension:
    return AskUserSuspension(
        message_id="msg-1",
        conversation_id="conv-1",
        user_id="user-1",
        captain_run_id="cap",
        checkpoint_id=checkpoint_id,
        tool_call_id=tool_call_id,
        base_system_prompt="sys",
        user_message="hello",
        question="先确认范围？",
        transcript=[_assistant_with_asks()],
        journal_entries=[
            {
                "kind": "checkpoint_required",
                "payload": {
                    "checkpoint_id": "cp-a",
                    "question": "先确认范围？",
                    "assumptions": [],
                    "questions": [],
                },
            },
            {
                "kind": "checkpoint_required",
                "payload": {
                    "checkpoint_id": "cp-b",
                    "question": "区外目录写入授权",
                    "assumptions": [],
                    "questions": [],
                },
            },
        ],
    )


def test_append_parallel_asks_does_not_skip_sibling():
    messages = [_assistant_with_asks()]
    append_resumed_tool_results(messages, "ask_a", "用户确认了范围。")
    tool_msgs = [m for m in messages if m.role == "tool"]
    assert [m.tool_call_id for m in tool_msgs] == ["ask_a"]
    assert tool_msgs[0].content == "用户确认了范围。"
    assert _SKIPPED not in "\n".join(str(m.content) for m in tool_msgs)
    assert unclosed_tool_call_ids(messages) == ["ask_b"]


def test_persist_parallel_asks_does_not_write_sibling_placeholder():
    fact_log = TurnFactLog()
    token = current_fact_log.set(fact_log)
    sink = EventSink()
    try:
        persist_resumed_tool_results(
            [_assistant_with_asks()],
            tool_call_id="ask_a",
            output="用户确认了范围。",
            run_id="cap",
            sink=sink,
            tool_name="ask_user",
        )
        entries = fact_log.entries()
    finally:
        current_fact_log.reset(token)

    call_facts = [
        e
        for e in entries
        if (e.get("kind") or "") == FactKind.TOOL_CALL.value
    ]
    assert len(call_facts) == 1
    payload = call_facts[0].get("payload") or {}
    assert payload.get("tool_call_id") == "ask_a"
    assert payload.get("result") == "用户确认了范围。"
    assert _SKIPPED not in str(payload.get("result") or "")

    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert len(ends) == 1
    assert ends[0].payload.get("tool_call_id") == "ask_a"
    assert _SKIPPED not in str(ends[0].payload.get("output") or "")


def test_next_pending_ask_user_keeps_sibling_card():
    suspension = _frame()
    messages = [_assistant_with_asks()]
    append_resumed_tool_results(messages, "ask_a", "用户确认了范围。")
    sibling = next_pending_ask_user_suspension(
        suspension, messages, list(suspension.journal_entries)
    )
    assert sibling is not None
    assert sibling.checkpoint_id == "cp-b"
    assert sibling.tool_call_id == "ask_b"
    assert sibling.question == "区外目录写入授权"


def _pause_journal() -> list[dict]:
    return [
        TurnStartedFact(system_prompt="sys", user_message="hello", model_profile="m")
        .to_fact()
        .entry(),
        RoundBoundaryFact(round_idx=0, run_id="cap", role="captain").to_fact().entry(),
        LlmCallFact(
            run_id="cap",
            round_idx=0,
            tool_calls=[
                {
                    "id": "ask_a",
                    "type": "function",
                    "function": {"name": "ask_user", "arguments": '{"question":"范围？"}'},
                },
                {
                    "id": "ask_b",
                    "type": "function",
                    "function": {
                        "name": "ask_user",
                        "arguments": '{"question":"区外目录写入授权"}',
                    },
                },
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
        {
            "kind": "checkpoint_required",
            "payload": {
                "checkpoint_id": "cp-a",
                "question": "先确认范围？",
                "assumptions": [],
                "questions": [],
            },
        },
        {
            "kind": "checkpoint_required",
            "payload": {
                "checkpoint_id": "cp-b",
                "question": "区外目录写入授权",
                "assumptions": [],
                "questions": [],
            },
        },
    ]


@pytest.mark.asyncio
async def test_recover_window_re_pauses_on_sibling_ask_user(monkeypatch):
    from agentcore.runtime.pipeline.resume import recover_path as rp
    from agentcore.runtime.recover import SettledSuspension

    journal = _pause_journal()
    suspension = _frame()
    suspension.journal_entries = journal
    window = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="hello"),
        _assistant_with_asks(),
    ]
    monkeypatch.setattr(rp, "resumed_captain_window", lambda _s, _h: list(window))
    monkeypatch.setattr(
        rp,
        "recover_turn",
        AsyncMock(
            return_value=SettledSuspension("用户确认了范围。", None, ToolEffect.CONTINUE)
        ),
    )
    saved: list[AskUserSuspension] = []

    async def saver(frame: AskUserSuspension) -> None:
        saved.append(frame)

    fact_log = TurnFactLog(inherited_entries=list(journal))
    token = current_fact_log.set(fact_log)
    try:
        recovered = await rp.recover_and_rebuild_window(
            suspension=suspension,
            decision=CheckpointDecision.CONTINUE,
            note="",
            selected=[],
            history=None,
            sink=EventSink(),
            delegate_tool=AsyncMock(),
            debate_tool=None,
            execution_id="e1",
            captain_run_id="cap",
            suspension_saver=saver,
        )
    finally:
        current_fact_log.reset(token)

    assert recovered.settled.effect is ToolEffect.SUSPEND
    assert unclosed_tool_call_ids(recovered.messages) == ["ask_b"]
    assert _SKIPPED not in "\n".join(
        str(m.content) for m in recovered.messages if m.role == "tool"
    )
    assert len(saved) == 1
    assert saved[0].checkpoint_id == "cp-b"
    assert saved[0].tool_call_id == "ask_b"
    ask_a_facts = [
        e
        for e in fact_log.entries()
        if (e.get("kind") or "") == FactKind.TOOL_CALL.value
        and (e.get("payload") or {}).get("tool_call_id") == "ask_a"
    ]
    assert ask_a_facts
    assert ask_a_facts[0]["payload"]["result"] == "用户确认了范围。"
    sibling_facts = [
        e
        for e in fact_log.entries()
        if (e.get("kind") or "") == FactKind.TOOL_CALL.value
        and (e.get("payload") or {}).get("tool_call_id") == "ask_b"
    ]
    assert sibling_facts == []


@pytest.mark.asyncio
async def test_recover_window_suspends_when_unclosed_without_sibling(monkeypatch):
    from agentcore.runtime.pipeline.resume import recover_path as rp
    from agentcore.runtime.recover import SettledSuspension

    journal = [
        e
        for e in _pause_journal()
        if (e.get("payload") or {}).get("checkpoint_id") != "cp-b"
    ]
    suspension = _frame()
    suspension.journal_entries = journal
    window = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="hello"),
        LLMMessage(
            role="assistant",
            content="先确认两件事。",
            tool_calls=_ask_calls(),
        ),
    ]
    monkeypatch.setattr(rp, "resumed_captain_window", lambda _s, _h: list(window))
    monkeypatch.setattr(
        rp,
        "recover_turn",
        AsyncMock(
            return_value=SettledSuspension("用户确认了范围。", None, ToolEffect.CONTINUE)
        ),
    )
    saved: list[AskUserSuspension] = []

    async def saver(frame: AskUserSuspension) -> None:
        saved.append(frame)

    fact_log = TurnFactLog(inherited_entries=list(journal))
    token = current_fact_log.set(fact_log)
    try:
        recovered = await rp.recover_and_rebuild_window(
            suspension=suspension,
            decision=CheckpointDecision.CONTINUE,
            note="",
            selected=[],
            history=None,
            sink=EventSink(),
            delegate_tool=AsyncMock(),
            debate_tool=None,
            execution_id="e1",
            captain_run_id="cap",
            suspension_saver=saver,
        )
    finally:
        current_fact_log.reset(token)

    assert recovered.settled.effect is ToolEffect.SUSPEND
    assert unclosed_tool_call_ids(recovered.messages) == ["ask_b"]
    assert _SKIPPED not in "\n".join(
        str(m.content) for m in recovered.messages if m.role == "tool"
    )
    assert saved == []
    assert [m.content for m in recovered.messages if m.role == "user"] == ["hello"]


def test_fold_after_one_settle_leaves_sibling_pending():
    from agentcore.runtime.journal import window_from_journal

    journal = _pause_journal()
    fact_log = TurnFactLog(inherited_entries=list(journal))
    token = current_fact_log.set(fact_log)
    sink = EventSink()
    try:
        persist_resumed_tool_results(
            [_assistant_with_asks()],
            tool_call_id="ask_a",
            output="用户确认了范围。",
            run_id="cap",
            sink=sink,
            tool_name="ask_user",
        )
        folded = window_from_journal(fact_log.entries())
    finally:
        current_fact_log.reset(token)

    assert folded is not None
    assert unclosed_tool_call_ids(folded) == ["ask_b"]
    tool_contents = [m.content for m in folded if m.role == "tool"]
    assert "用户确认了范围。" in tool_contents
    assert _SKIPPED not in "".join(str(c) for c in tool_contents)


def _required(checkpoint_id: str, question: str) -> SimpleNamespace:
    return SimpleNamespace(
        type=SimpleNamespace(value="checkpoint_required"),
        payload={"checkpoint_id": checkpoint_id, "question": question},
        timestamp="t1",
    )


@pytest.mark.asyncio
async def test_parallel_capture_keeps_both_required_cards():
    transcript = [_assistant_with_asks()]
    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(system_prompt="sys", user_message="hi", model_profile="m").to_fact()
    )
    ct_token = captain_transcript.set(transcript)
    fl_token = current_fact_log.set(log)
    saved: list[AskUserSuspension] = []

    def build_a(capture):
        return AskUserSuspension(
            message_id="msg-par",
            conversation_id="c1",
            user_id="u1",
            captain_run_id="cap",
            checkpoint_id="cp-a",
            tool_call_id="ask_a",
            base_system_prompt="sys",
            user_message="hi",
            question="先确认范围？",
            journal_entries=capture.journal_entries,
            transcript=capture.transcript,
        )

    def build_b(capture):
        return AskUserSuspension(
            message_id="msg-par",
            conversation_id="c1",
            user_id="u1",
            captain_run_id="cap",
            checkpoint_id="cp-b",
            tool_call_id="ask_b",
            base_system_prompt="sys",
            user_message="hi",
            question="区外目录写入授权",
            journal_entries=capture.journal_entries,
            transcript=capture.transcript,
        )

    async def saver(frame: AskUserSuspension) -> None:
        saved.append(frame)

    try:
        import asyncio

        await asyncio.gather(
            persist_suspension_capture(
                checkpoint_id="cp-a",
                required_event=_required("cp-a", "先确认范围？"),
                build_frame=build_a,
                saver=saver,
                suspension_kind="ask_user",
                message_id="msg-par",
            ),
            persist_suspension_capture(
                checkpoint_id="cp-b",
                required_event=_required("cp-b", "区外目录写入授权"),
                build_frame=build_b,
                saver=saver,
                suspension_kind="ask_user",
                message_id="msg-par",
            ),
        )
    finally:
        current_fact_log.reset(fl_token)
        captain_transcript.reset(ct_token)

    assert len(saved) == 2
    last = saved[-1]
    required_ids = {
        (e.get("payload") or {}).get("checkpoint_id")
        for e in last.journal_entries
        if (e.get("kind") or "") == "checkpoint_required"
    }
    assert required_ids == {"cp-a", "cp-b"}
