"""Shared suspension capture skeleton + cloud resume restore ratchet."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.core.types import AutonomyPolicy, recipe_to_axes
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.events import FinishReason
from agentcore.runtime.facts import TurnFactLog, TurnStartedFact, current_fact_log
from agentcore.runtime.suspension import AskUserSuspension, captain_transcript
from agentcore.runtime.suspension.capture import SuspensionCapture, persist_suspension_capture
from agentcore.runtime.suspension.persistence import restore_paused_turn


def _ask_user_suspension() -> AskUserSuspension:
    return AskUserSuspension(
        message_id="msg-1",
        conversation_id="conv-1",
        user_id="user-1",
        captain_run_id="run-1",
        checkpoint_id="cp-1",
        tool_call_id="tc-1",
        base_system_prompt="sys",
        user_message="hello",
        question="pick one",
        trace_id="trace-1",
    )


@pytest.mark.asyncio
async def test_persist_suspension_capture_snapshots_fact_log_and_saves() -> None:
    required = SimpleNamespace(
        type=SimpleNamespace(value="checkpoint_required"),
        payload={"checkpoint_id": "cp-1"},
        timestamp="t1",
    )
    transcript = [LLMMessage(role="user", content="hi")]
    # Bind an ambient fact log so the capture snapshots a real §8.3 stream (the 唯一权威载体):
    # the turn_started head + the about-to-emit checkpoint_required card appended as trailing.
    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(system_prompt="sys", user_message="hi", model_profile="m").to_fact()
    )
    ct_token = captain_transcript.set(transcript)
    fl_token = current_fact_log.set(log)
    saved: list[AskUserSuspension] = []

    def build_frame(capture: SuspensionCapture) -> AskUserSuspension:
        assert capture.transcript == transcript
        # journal_entries is the sole authoritative carrier; the display ``journal`` derives.
        assert capture.journal_entries[0]["kind"] == "turn_started"
        assert capture.journal_entries[-2]["kind"] == "checkpoint_required"
        assert capture.journal_entries[-1]["kind"] == "turn_paused"
        return AskUserSuspension(
            message_id="msg-1",
            conversation_id="conv-1",
            user_id="user-1",
            captain_run_id="run-1",
            checkpoint_id="cp-1",
            tool_call_id="tc-1",
            base_system_prompt="sys",
            user_message="hello",
            question="q",
            journal_entries=capture.journal_entries,
            transcript=capture.transcript,
            history=capture.history,
            trace_id=capture.trace_id,
        )

    async def saver(frame: AskUserSuspension) -> None:
        saved.append(frame)

    try:
        ok = await persist_suspension_capture(
            checkpoint_id="cp-1",
            required_event=required,
            build_frame=build_frame,
            saver=saver,
            suspension_kind="ask_user",
        )
    finally:
        current_fact_log.reset(fl_token)
        captain_transcript.reset(ct_token)

    assert ok is True
    assert len(saved) == 1
    # The display resume seed is DERIVED from journal_entries (P0-B Phase 3): the execution
    # turn_started fact drops, the surface checkpoint_required card survives.
    assert [e["type"] for e in saved[0].journal] == ["checkpoint_required"]
    assert saved[0].journal[0]["payload"] == {"checkpoint_id": "cp-1"}


@pytest.mark.asyncio
async def test_persist_suspension_capture_skips_without_transcript() -> None:
    required = SimpleNamespace(
        type=SimpleNamespace(value="checkpoint_required"),
        payload={},
        timestamp=None,
    )
    token = captain_transcript.set(None)
    try:
        ok = await persist_suspension_capture(
            checkpoint_id="cp-1",
            required_event=required,
            build_frame=lambda _c: _ask_user_suspension(),
            saver=AsyncMock(),
            suspension_kind="ask_user",
        )
    finally:
        captain_transcript.reset(token)
    assert ok is False


@pytest.mark.asyncio
async def test_persist_suspension_capture_raises_on_saver_failure() -> None:
    from agentcore.runtime.suspension.capture import SuspensionPersistError

    required = SimpleNamespace(
        type=SimpleNamespace(value="checkpoint_required"),
        payload={},
        timestamp=None,
    )
    transcript = [LLMMessage(role="user", content="hi")]
    token = captain_transcript.set(transcript)

    async def boom(_frame: AskUserSuspension) -> None:
        raise RuntimeError("disk full")

    try:
        with pytest.raises(SuspensionPersistError, match="disk full"):
            await persist_suspension_capture(
                checkpoint_id="cp-1",
                required_event=required,
                build_frame=lambda _c: _ask_user_suspension(),
                saver=boom,
                suspension_kind="ask_user",
            )
    finally:
        captain_transcript.reset(token)


@pytest.mark.asyncio
async def test_claim_paused_turn_restores_frame_on_hydrate_failure() -> None:
    """Claim succeeded but hydrate fails ⇒ restore frame + raise (not silent None/404)."""
    from agentcore.runtime.suspension import persistence as persist_mod

    frame = {
        "kind": "ask_user",
        "message_id": "msg-1",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "captain_run_id": "run-1",
        "checkpoint_id": "cp-1",
        "tool_call_id": "tc-1",
        "base_system_prompt": "sys",
        "user_message": "hello",
        "question": "q",
    }
    row = SimpleNamespace(
        message_id="msg-1",
        conversation_id="conv-1",
        user_id="user-1",
        frame=frame,
        trace_id="trace-1",
    )

    with (
        patch.object(persist_mod, "async_session_factory") as factory,
        patch.object(persist_mod, "PausedTurnRepository") as repo_cls,
        patch.object(persist_mod, "TurnJournalRepository") as journal_cls,
        patch.object(
            persist_mod,
            "suspension_from_json",
            side_effect=ValueError("bad frame"),
        ),
        patch.object(persist_mod, "_upsert_paused_frame", AsyncMock()) as upsert,
    ):
        session = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        repo_cls.return_value.get = AsyncMock(return_value=row)
        repo_cls.return_value.claim = AsyncMock(return_value=row)
        journal_cls.return_value.load_after = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="bad frame"):
            await persist_mod.claim_paused_turn(
                "msg-1", conversation_id="conv-1", decision="continue"
            )

    upsert.assert_awaited_once()
    assert upsert.await_args.kwargs["message_id"] == "msg-1"
    assert upsert.await_args.kwargs["frame"] == frame


@pytest.mark.asyncio
async def test_claim_paused_turn_rewrites_loser_prewrite_to_winner_decision() -> None:
    """两端不同决策抢帧：claim 赢家把 journal ``*_resolved`` 改成自己的。

    旧行为只写 outcomes、不改 journal，重开折 journal 会看到落败方的按钮。
    """
    from agentcore.runtime.suspension import persistence as persist_mod

    frame = {
        "kind": "ask_user",
        "message_id": "msg-1",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "captain_run_id": "run-1",
        "checkpoint_id": "cp-1",
        "tool_call_id": "tc-1",
        "base_system_prompt": "sys",
        "user_message": "hello",
        "question": "q",
    }
    row = SimpleNamespace(
        message_id="msg-1",
        conversation_id="conv-1",
        user_id="user-1",
        frame=frame,
        trace_id="trace-1",
    )
    loser = {
        "kind": "checkpoint_resolved",
        "payload": {"checkpoint_id": "cp-1", "decision": "stop", "note": "from-a"},
        "ts": "t1",
    }

    with (
        patch.object(persist_mod, "async_session_factory") as factory,
        patch.object(persist_mod, "PausedTurnRepository") as repo_cls,
        patch.object(persist_mod, "TurnJournalRepository") as journal_cls,
        patch.object(persist_mod, "_set_message_pause_latch", AsyncMock()),
        patch.object(persist_mod, "_signal_frame_resolved", AsyncMock()),
    ):
        session = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        repo_cls.return_value.get = AsyncMock(return_value=row)
        repo_cls.return_value.claim = AsyncMock(return_value=row)
        journal = journal_cls.return_value
        journal.load_after = AsyncMock(
            return_value=[{"seq": 0, **loser}]
        )
        journal.record = AsyncMock()
        journal.replace_live = AsyncMock()

        claimed = await persist_mod.claim_paused_turn(
            "msg-1", conversation_id="conv-1", decision="continue"
        )

    assert claimed is not None
    assert claimed.journal_entries[-1]["payload"]["decision"] == "continue"
    journal.replace_live.assert_awaited_once()
    journal.record.assert_not_awaited()
    written = journal.replace_live.await_args.kwargs["entries"]
    assert len(written) == 1
    assert written[0]["payload"]["decision"] == "continue"
    assert written[0]["payload"]["note"] == "from-a"


@pytest.mark.asyncio
async def test_claim_paused_turn_does_not_rewrite_already_matching_journal() -> None:
    from agentcore.runtime.suspension import persistence as persist_mod

    frame = {
        "kind": "ask_user",
        "message_id": "msg-1",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "captain_run_id": "run-1",
        "checkpoint_id": "cp-1",
        "tool_call_id": "tc-1",
        "base_system_prompt": "sys",
        "user_message": "hello",
        "question": "q",
    }
    row = SimpleNamespace(
        message_id="msg-1",
        conversation_id="conv-1",
        user_id="user-1",
        frame=frame,
        trace_id="trace-1",
    )
    matching = {
        "kind": "checkpoint_resolved",
        "payload": {"checkpoint_id": "cp-1", "decision": "continue"},
        "ts": "t1",
    }

    with (
        patch.object(persist_mod, "async_session_factory") as factory,
        patch.object(persist_mod, "PausedTurnRepository") as repo_cls,
        patch.object(persist_mod, "TurnJournalRepository") as journal_cls,
        patch.object(persist_mod, "_set_message_pause_latch", AsyncMock()),
        patch.object(persist_mod, "_signal_frame_resolved", AsyncMock()),
    ):
        session = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        repo_cls.return_value.get = AsyncMock(return_value=row)
        repo_cls.return_value.claim = AsyncMock(return_value=row)
        journal = journal_cls.return_value
        journal.load_after = AsyncMock(
            return_value=[{"seq": 0, **matching}]
        )
        journal.record = AsyncMock()
        journal.replace_live = AsyncMock()

        claimed = await persist_mod.claim_paused_turn(
            "msg-1", conversation_id="conv-1", decision="continue"
        )

    assert claimed is not None
    assert claimed.journal_entries == [matching]
    journal.record.assert_not_awaited()
    journal.replace_live.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_paused_turn_upserts_frame_without_notify() -> None:
    suspension = _ask_user_suspension()
    with patch(
        "agentcore.runtime.suspension.persistence.async_session_factory"
    ) as factory:
        session = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        with patch(
            "agentcore.runtime.suspension.persistence.PausedTurnRepository"
        ) as repo_cls, patch(
            "agentcore.runtime.suspension.persistence._notify_pause",
            AsyncMock(),
        ) as notify:
            repo_cls.return_value.upsert = AsyncMock()
            await restore_paused_turn(suspension)

    repo_cls.return_value.upsert.assert_awaited_once_with(
        message_id="msg-1",
        conversation_id="conv-1",
        user_id="user-1",
        frame=suspension.to_json(),
        trace_id="trace-1",
    )
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_chat_does_not_restore_after_settlement_on_error() -> None:
    """D1: pipeline error after settlement prewrite must not resurrect the decision card.

    The cloud route prewrites settlement + claims the frame before dispatch, so a
    post-settlement failure is interrupted_after_decision, not a retryable frame restore.
    """
    from agentcore.conversation import turns as turns_mod
    from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
    from agentcore.runtime.events import EventSink

    suspension = _ask_user_suspension()
    sink = EventSink()
    conv = MagicMock()
    conv.folder_id = None

    with (
        patch.object(turns_mod, "async_session_factory") as factory,
        patch.object(turns_mod, "ConversationRepository") as conv_repo_cls,
        patch.object(turns_mod, "BoardRepository") as board_repo_cls,
        patch.object(turns_mod, "resolve_local_binding", AsyncMock(return_value=None)),
        patch.object(turns_mod, "resolve_profile_set", AsyncMock(return_value=None)),
        patch.object(
            turns_mod,
            "resolve_permission_axes",
            AsyncMock(return_value=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)),
        ),
        patch.object(turns_mod, "load_chat_context", AsyncMock(return_value=[])),
        patch.object(turns_mod, "build_turn_backend", return_value=MagicMock()),
        patch.object(turns_mod, "session_callbacks", return_value=(AsyncMock(), AsyncMock())),
        patch.object(turns_mod, "suspension_callbacks", return_value=(AsyncMock(), AsyncMock())),
        patch.object(
            turns_mod,
            "resume_chat_pipeline",
            AsyncMock(
                return_value={
                    "message_id": "msg-1",
                    "content": "",
                    "error": "boom",
                    "finish_reason": FinishReason.ERROR,
                    "cost_runs": [],
                }
            ),
        ),
        patch.object(turns_mod, "persist_turn_result", AsyncMock()),
        patch.object(turns_mod, "restore_paused_turn", AsyncMock()) as restore,
    ):
        session = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        board_repo_cls.return_value.get_by_conversation_id = AsyncMock(return_value=None)

        await turns_mod.resume_chat(
            suspension=suspension,
            response=CheckpointResponse(decision=CheckpointDecision.CONTINUE),
            sink=sink,
        )

    restore.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_chat_does_not_restore_on_success() -> None:
    from agentcore.conversation import turns as turns_mod
    from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
    from agentcore.runtime.events import EventSink

    suspension = _ask_user_suspension()
    sink = EventSink()
    conv = MagicMock()
    conv.folder_id = None

    with (
        patch.object(turns_mod, "async_session_factory") as factory,
        patch.object(turns_mod, "ConversationRepository") as conv_repo_cls,
        patch.object(turns_mod, "BoardRepository") as board_repo_cls,
        patch.object(turns_mod, "resolve_local_binding", AsyncMock(return_value=None)),
        patch.object(turns_mod, "resolve_profile_set", AsyncMock(return_value=None)),
        patch.object(
            turns_mod,
            "resolve_permission_axes",
            AsyncMock(return_value=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)),
        ),
        patch.object(turns_mod, "load_chat_context", AsyncMock(return_value=[])),
        patch.object(turns_mod, "build_turn_backend", return_value=MagicMock()),
        patch.object(turns_mod, "session_callbacks", return_value=(AsyncMock(), AsyncMock())),
        patch.object(turns_mod, "suspension_callbacks", return_value=(AsyncMock(), AsyncMock())),
        patch.object(
            turns_mod,
            "resume_chat_pipeline",
            AsyncMock(
                return_value={
                    "message_id": "msg-1",
                    "content": "done",
                    "finish_reason": FinishReason.END_TURN,
                    "cost_runs": [],
                }
            ),
        ),
        patch.object(turns_mod, "persist_turn_result", AsyncMock()),
        patch.object(turns_mod, "restore_paused_turn", AsyncMock()) as restore,
    ):
        session = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        board_repo_cls.return_value.get_by_conversation_id = AsyncMock(return_value=None)

        await turns_mod.resume_chat(
            suspension=suspension,
            response=CheckpointResponse(decision=CheckpointDecision.CONTINUE),
            sink=sink,
        )

    restore.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_chat_does_not_restore_after_settlement_on_cancel() -> None:
    """D1: /stop mid-continuation (CancelledError) must not resurrect the decision card.

    Regression for「授权开赛 → 辩论续播中点停止 → 开工卡复活」: the cloud route prewrites
    settlement + claims the frame before dispatch, so a cancel after that is
    interrupted_after_decision, never a frame restore.
    """
    from agentcore.conversation import turns as turns_mod
    from agentcore.runtime.checkpoints import CheckpointDecision, CheckpointResponse
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.turn.runs import TurnRun, turn_runs

    suspension = _ask_user_suspension()
    sink = EventSink()
    conv = MagicMock()
    conv.folder_id = None
    conversation_id = suspension.conversation_id

    async def _cancel(**_kwargs: object) -> None:
        raise asyncio.CancelledError

    # Explicit user /stop marks the run so resume cancel closes terminal (not orphan).
    async def _noop() -> None:
        await asyncio.sleep(3600)

    stop_task = asyncio.create_task(_noop())
    turn_runs._runs[conversation_id] = TurnRun(
        run_id="r-stop",
        conversation_id=conversation_id,
        task=stop_task,
        sink=sink,
        user_stopped=True,
    )

    with (
        patch.object(turns_mod, "async_session_factory") as factory,
        patch.object(turns_mod, "ConversationRepository") as conv_repo_cls,
        patch.object(turns_mod, "BoardRepository") as board_repo_cls,
        patch.object(turns_mod, "resolve_local_binding", AsyncMock(return_value=None)),
        patch.object(turns_mod, "resolve_profile_set", AsyncMock(return_value=None)),
        patch.object(
            turns_mod,
            "resolve_permission_axes",
            AsyncMock(return_value=recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)),
        ),
        patch.object(turns_mod, "load_chat_context", AsyncMock(return_value=[])),
        patch.object(turns_mod, "build_turn_backend", return_value=MagicMock()),
        patch.object(turns_mod, "session_callbacks", return_value=(AsyncMock(), AsyncMock())),
        patch.object(turns_mod, "suspension_callbacks", return_value=(AsyncMock(), AsyncMock())),
        patch.object(turns_mod, "resume_chat_pipeline", _cancel),
        patch.object(turns_mod, "close_user_stop_turn", AsyncMock(return_value=True)) as close_stop,
        patch.object(turns_mod, "persist_turn_result", AsyncMock()),
        patch.object(turns_mod, "restore_paused_turn", AsyncMock()) as restore,
    ):
        session = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        board_repo_cls.return_value.get_by_conversation_id = AsyncMock(return_value=None)

        try:
            with pytest.raises(asyncio.CancelledError):
                await turns_mod.resume_chat(
                    suspension=suspension,
                    response=CheckpointResponse(decision=CheckpointDecision.CONTINUE),
                    sink=sink,
                )
        finally:
            stop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_task
            turn_runs._runs.pop(conversation_id, None)

    close_stop.assert_awaited_once()
    restore.assert_not_awaited()
