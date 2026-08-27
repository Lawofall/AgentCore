"""Batch 7: G6 content_reset reinjection wiring + G8 cancel-salvage pre_pause join."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.config import settings
from agentcore.conversation import turn_persistence
from agentcore.conversation.turn_persistence import (
    compose_salvage_content,
    compose_salvage_journal,
    salvage_incomplete_turn,
)
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.engine import join_segments
from agentcore.runtime.events import EventSink, EventType, content_delta, content_reset
from agentcore.runtime.facts import TurnPausedFact
from agentcore.runtime.pipeline.resume import pipeline as resume_mod
from agentcore.runtime.pipeline.resume.recover_path import RecoveredResume
from agentcore.runtime.pipeline.resume.rehydrate import RehydratedTurnState
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.workspace.protocol import WorkspaceBackend


def _paused_entry(content: str = "挂起前正文") -> dict:
    return TurnPausedFact(
        checkpoint_id="ck1",
        suspension_kind="ask_user",
        content=content,
        reasoning="思考",
    ).to_fact().entry()


def _ask_frame(**kwargs) -> AskUserSuspension:
    defaults = dict(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="ck1",
        tool_call_id="call_ask",
        base_system_prompt="sys",
        user_message="hi",
        transcript=[],
        question="?",
        questions=[],
        journal_entries=[_paused_entry()],
    )
    defaults.update(kwargs)
    return AskUserSuspension(**defaults)


# --- G8 compose_salvage_content -------------------------------------------------


def test_compose_salvage_joins_pre_pause_and_live():
    live = "续跑半段"
    content = compose_salvage_content(live, [_paused_entry("挂起前正文")])
    assert content == join_segments("挂起前正文", live)
    assert "挂起前正文" in content
    assert "续跑半段" in content


def test_compose_salvage_legacy_journal_is_live_only():
    """Old frames without turn_paused → empty base, same as streamed_content alone."""
    legacy = [{"kind": "turn_started", "payload": {"user_message": "hi"}, "ts": None}]
    assert compose_salvage_content("只有 live", legacy) == "只有 live"
    assert compose_salvage_content("只有 live", None) == "只有 live"
    assert compose_salvage_content("只有 live", []) == "只有 live"


def test_compose_salvage_empty_live_keeps_pre_pause():
    assert compose_salvage_content("", [_paused_entry("基底")]) == "基底"


def test_compose_salvage_drops_dispatch_kickoff_when_live():
    kickoff = "方向：派团队 — 用户明示 cite_write_review，直接开委派。"
    live = "修订说明：已按反馈收口文件路径。"
    assert compose_salvage_content(live, [_paused_entry(kickoff)]) == live


# --- G8 compose_salvage_journal -------------------------------------------------


def test_compose_salvage_journal_joins_hang_frame_and_live():
    hang = [
        {"kind": "process_reasoning", "payload": {"text": "想"}, "ts": None},
        {"kind": "checkpoint_resolved", "payload": {"checkpoint_id": "ck1"}, "ts": None},
    ]
    live = [
        {"kind": "checkpoint_resolved", "payload": {"checkpoint_id": "ck1"}, "ts": None},
        {"kind": "run_started", "payload": {"run_id": "w1"}, "ts": None},
    ]
    merged = compose_salvage_journal(live, hang)
    kinds = [e.get("kind") for e in merged]
    assert kinds == ["process_reasoning", "checkpoint_resolved", "run_started"]
    assert [e.get("seq") for e in merged] == [0, 1, 2]


def test_compose_salvage_journal_live_only_when_no_hang_frame():
    live = [{"kind": "run_started", "payload": {"run_id": "w1"}, "ts": None}]
    assert compose_salvage_journal(live, None) == live
    assert compose_salvage_journal(live, []) == live


def test_compose_salvage_journal_hang_only_when_no_live():
    hang = [{"kind": "process_content", "payload": {"text": "旁白"}, "ts": None}]
    assert [e.get("kind") for e in compose_salvage_journal(None, hang)] == [
        "process_content"
    ]
    assert [e.get("kind") for e in compose_salvage_journal([], hang)] == [
        "process_content"
    ]


# --- G8 cloud salvage_incomplete_turn -------------------------------------------


class _Sink:
    def __init__(self, journal: list[dict] | None, content: str = "") -> None:
        self._journal = journal
        self._content = content

    def execution_journal(self) -> list[dict] | None:
        return self._journal

    def streamed_content(self) -> str:
        return self._content

    def interrupt_salvage_content(self) -> str:
        return self._content


@pytest.fixture
def capture(monkeypatch):
    spawned: list = []
    persist_calls: list[dict] = []

    def fake_persist(**kwargs):
        persist_calls.append(kwargs)
        return MagicMock(name="persist_coro")

    def fake_spawn(coro):
        spawned.append(coro)
        return MagicMock(name="task")

    monkeypatch.setattr(turn_persistence, "persist_incomplete_turn", fake_persist)
    monkeypatch.setattr(turn_persistence, "spawn_background", fake_spawn)
    return spawned, persist_calls


def test_salvage_incomplete_joins_pre_pause_from_journal_entries(monkeypatch, capture):
    spawned, persist_calls = capture
    monkeypatch.setattr(settings, "incomplete_turn_persist_enabled", True)
    salvage_incomplete_turn(
        sink=_Sink([{"type": "run_plan", "payload": {}}], content="续跑半段"),
        conversation_id="conv",
        trace_id="trace",
        message_id="m1",
        journal_entries=[_paused_entry("挂起前正文")],
    )
    assert len(spawned) == 1
    assert persist_calls[0]["content"] == join_segments("挂起前正文", "续跑半段")


def test_salvage_incomplete_legacy_entries_unchanged(monkeypatch, capture):
    spawned, persist_calls = capture
    monkeypatch.setattr(settings, "incomplete_turn_persist_enabled", True)
    salvage_incomplete_turn(
        sink=_Sink(None, content="已经写了一半的答案"),
        conversation_id="conv",
        trace_id="trace",
        message_id="m1",
        journal_entries=[{"kind": "turn_started", "payload": {}}],
    )
    assert len(spawned) == 1
    assert persist_calls[0]["content"] == "已经写了一半的答案"


# --- G6 resume pipeline reinjection wiring -------------------------------------


def _patch_resume_terminal(monkeypatch, *, pre_pause: str) -> EventSink:
    """Drive resume to the ask_user terminal path with a fixed recovered pre_pause."""
    sink = EventSink()
    wired = SimpleNamespace(
        bound_execution_id="exec1",
        execution_id_token=None,  # skip current_execution_id.reset in finally
        delegate_tool=MagicMock(),
        debate_tool=MagicMock(),
        chat_tools=MagicMock(),
        base_tool_context=SimpleNamespace(execution_id="exec1"),
        approval_gate=None,
        vision_cost_sink=[],
    )
    llm = MagicMock()
    llm.close = AsyncMock()

    monkeypatch.setattr(resume_mod, "wire_resume_turn", AsyncMock(return_value=wired))
    monkeypatch.setattr(
        resume_mod,
        "bootstrap_resume_display",
        lambda **_k: RehydratedTurnState(
            pre_pause_content=pre_pause if pre_pause else None,
            pre_pause_reasoning="想" if pre_pause else "",
            citations=[],
            from_turn_paused=bool(pre_pause),
            controller_seed=None,
        ),
    )
    monkeypatch.setattr(
        resume_mod,
        "recover_and_rebuild_window",
        AsyncMock(
            return_value=RecoveredResume(
                messages=[LLMMessage(role="user", content="hi")],
                pre_pause=pre_pause,
                settled=SimpleNamespace(terminal_text="收口"),
            )
        ),
    )
    # Real bind_recorder (needs a ContextVar Token for finally reset).
    monkeypatch.setattr(
        resume_mod.pipeline_pkg, "build_turn_router", AsyncMock(return_value=llm)
    )
    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        MagicMock(side_effect=RuntimeError("no db")),
    )
    return sink


async def test_resume_sets_reinjection_and_reset_skips_process_timeline(monkeypatch):
    """G6: after recover, non-empty pre_pause is wired; content_reset reinjects
    display-only (history/SSE) and leaves persist process timeline without the reinject.
    """
    pre_pause = "挂起前正文"
    sink = _patch_resume_terminal(monkeypatch, pre_pause=pre_pause)

    result = await resume_mod.resume_chat_pipeline(
        suspension=_ask_frame(),
        decision=CheckpointDecision.STOP,
        note="收口",
        sink=sink,
        backend=MagicMock(spec=WorkspaceBackend),
    )
    assert result["finish_reason"]  # terminal path completed
    assert sink._content_reset_reinjection == pre_pause + "\n\n"

    # Absorb / rework / soft-gate: content_reset after resume wiring.
    sink.emit(content_delta("将被吸收的同轮问句"))
    sink.emit(content_reset("ask_user"))

    # Client stream: reinjected delta restores pre_pause base.
    history_types = [e.type for e in sink._history]
    assert EventType.CONTENT_RESET in history_types
    reinject = [e for e in sink._history if e.type is EventType.CONTENT_DELTA]
    assert any(e.payload.get("delta") == pre_pause + "\n\n" for e in reinject)

    # Persist process timeline must NOT contain the reinjected step.
    raw = sink.raw_process()
    assert all(s.get("text") != pre_pause + "\n\n" for s in raw)
    assert sink.streamed_content() == ""  # live cleared; reinject is display-only


async def test_resume_empty_pre_pause_does_not_set_reinjection(monkeypatch):
    sink = _patch_resume_terminal(monkeypatch, pre_pause="")

    await resume_mod.resume_chat_pipeline(
        suspension=_ask_frame(journal_entries=[]),
        decision=CheckpointDecision.STOP,
        note="只收口",
        sink=sink,
        backend=MagicMock(spec=WorkspaceBackend),
    )
    assert sink._content_reset_reinjection is None


def test_arm_content_reset_skips_dispatch_kickoff_preamble():
    """派工 kickoff 不 G6 重灌——避免气泡以「方向：派团队」当交付前文。"""
    from agentcore.runtime.pipeline.resume.rehydrate import arm_content_reset_reinjection

    sink = EventSink(message_id="m1", conversation_id="c1")
    arm_content_reset_reinjection(
        sink,
        "方向：派团队 — 用户明示 cite_write_review，直接开委派。",
    )
    assert sink._content_reset_reinjection is None

    arm_content_reset_reinjection(sink, "阶段成果如下。")
    assert sink._content_reset_reinjection == "阶段成果如下。\n\n"
