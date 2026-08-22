"""一会话一张开工卡：orphan 旧 pending team_preview（journal ∪ 进程内 + SSE）。"""

from __future__ import annotations

import pytest

from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.kickoff.orphan import (
    list_journal_pending_team_previews,
    orphan_conversation_team_previews,
    remember_live_team_preview,
    reset_team_preview_orphan_state,
)


@pytest.fixture(autouse=True)
def _reset_orphan_state():
    reset_team_preview_orphan_state()
    yield
    reset_team_preview_orphan_state()


def _patch_empty_journal(monkeypatch: pytest.MonkeyPatch):
    async def _empty(_cid: str):
        return []

    monkeypatch.setattr(
        "agentcore.runtime.kickoff.orphan.list_journal_pending_team_previews",
        _empty,
    )


def _patch_orphan_fact(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    facts: list[dict] = []

    async def _fake_emit(**kwargs):
        facts.append(kwargs)

    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.emit_orphan_fact",
        _fake_emit,
    )
    return facts


async def test_orphan_skips_ask_user(monkeypatch):
    """ask_user pending 不进开工卡 orphan 面（澄清卡 ⊥ 开工卡）。"""
    facts = _patch_orphan_fact(monkeypatch)

    class Repo:
        async def list_recent_turn_ids(self, _cid, limit=40):
            return ["turn-a"]

        async def load(self, _turn_id):
            return [
                {
                    "kind": "checkpoint_required",
                    "payload": {"checkpoint_id": "ask1", "question": "交付形态？"},
                },
                {
                    "kind": "team_preview_required",
                    "payload": {"checkpoint_id": "tp1", "workers": []},
                },
            ]

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr("agentcore.db.base.async_session_factory", lambda: _Sess())
    monkeypatch.setattr(
        "agentcore.db.repositories.TurnJournalRepository",
        lambda _db: Repo(),
    )

    found = await list_journal_pending_team_previews("c-ask")
    assert [item[1] for item in found] == ["tp1"]

    sink = EventSink()
    out = await orphan_conversation_team_previews(
        "c-ask", sink=sink, reason="superseded", exclude_ids={"tp_new"}
    )
    assert out == ["tp1"]
    assert [f["kind"] for f in facts] == ["team_preview"]
    assert "ask1" not in {f["interaction_id"] for f in facts}
    assert not any(
        e.payload.get("kind") == "ask_user"
        for e in sink._history
        if e.type is EventType.INTERACTION_ORPHANED
    )


async def test_orphan_exclude_skips_new_card_id(monkeypatch):
    _patch_empty_journal(monkeypatch)
    facts = _patch_orphan_fact(monkeypatch)
    remember_live_team_preview("c-ex", "tp_old", "m1")
    remember_live_team_preview("c-ex", "tp_new", "m1")
    sink = EventSink()
    out = await orphan_conversation_team_previews(
        "c-ex", sink=sink, reason="superseded", exclude_ids={"tp_new"}
    )
    assert out == ["tp_old"]
    assert [f["interaction_id"] for f in facts] == ["tp_old"]
