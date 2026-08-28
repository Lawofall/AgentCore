"""Open-turn deferral for memory consolidation (挂起/在跑回合防误整合).

A conversation parked at a durable checkpoint (e.g. the team_preview 开工卡 — it
legitimately sits idle for minutes waiting on the user) or holding a fresh RUNNING
lease must NOT be consolidated: its window contains a partial assistant snapshot,
and a pass would surface a premature memory card mid-turn. The runner skips
WITHOUT advancing the watermark so the turn's own finalize re-arms a full pass.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from agentcore.config import settings
from agentcore.db.models.runs import TurnLeaseRow
from agentcore.db.repositories import (
    ConversationRepository,
    MessageRepository,
    PausedTurnRepository,
    UserRepository,
)
from agentcore.memory import consolidation
from agentcore.memory.episodic import EpisodeRecord
from agentcore.runtime.leases import TurnLeaseRepository


class _DummyProvider:
    async def close(self) -> None:
        return None


@pytest.fixture
def episodic_calls(monkeypatch, session_factory) -> list[str]:
    """Route the DB-bound runner at the test schema; stub the episodic write edge.

    Returns recorded conversation ids that reached ``append_episode``.
    """
    monkeypatch.setattr(consolidation, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "billing_mode", "platform")
    monkeypatch.setattr(
        consolidation, "build_provider", lambda *a, **k: _DummyProvider()
    )
    calls: list[str] = []

    class _FakeSummarizer:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def summarize(self, messages, *, max_chars: int, actions=None) -> str:
            return "测试摘要"

    async def fake_append(_store, **kwargs):
        calls.append(kwargs["conversation_id"])
        return EpisodeRecord(
            id="ep-test",
            conversation_id=kwargs["conversation_id"],
            summary=kwargs["summary"],
            created_at=datetime.now(UTC).isoformat(),
        )

    async def fake_record(**_k):
        return None

    async def fake_semantic(**_k):
        return False

    async def fake_run_bg(user_id, *, purpose="memory", runner):
        # consolidation.run_background_llm opens the global session factory; pin a
        # fake credential path so this suite stays on the test schema.
        from agentcore.billing.gate import BackgroundLlmResult
        from agentcore.llm.credentials import LLMCredentials

        creds = LLMCredentials(
            api_key="sk-test",
            base_url="https://example.test",
            default_model="flash",
            source="platform",
        )
        value = await runner(creds)
        return BackgroundLlmResult(value=value, credentials=creds)

    monkeypatch.setattr(consolidation, "LLMEpisodicSummarizer", _FakeSummarizer)
    monkeypatch.setattr(consolidation, "run_background_llm", fake_run_bg)
    monkeypatch.setattr(consolidation, "resolve_user_model", lambda _c: "flash")
    monkeypatch.setattr(consolidation, "append_episode", fake_append)
    monkeypatch.setattr(consolidation, "_record_and_publish", fake_record)
    monkeypatch.setattr(consolidation, "run_semantic_for_scope", fake_semantic)
    return calls


async def _seed_turn(session_factory) -> tuple[str, str, str]:
    """User + conversation + one user/assistant message pair; returns their ids."""
    async with session_factory() as session:
        user = await UserRepository(session).create(
            username="mem-guard-u", display_name="mem-guard-u"
        )
        conv = await ConversationRepository(session).create(user_id=user.user_id)
        await MessageRepository(session).create(
            conversation_id=conv.id, role="user", content="搜索并启动模拟庭审辩论"
        )
        msg = await MessageRepository(session).create(
            conversation_id=conv.id, role="assistant", content="案情简介（暂停前半成品）"
        )
        return user.user_id, conv.id, msg.id


async def test_paused_turn_defers_consolidation_without_watermark(
    session_factory, episodic_calls
):
    user_id, conv_id, msg_id = await _seed_turn(session_factory)
    async with session_factory() as session:
        await PausedTurnRepository(session).upsert(
            message_id=msg_id,
            conversation_id=conv_id,
            user_id=user_id,
            frame={"kind": "team_preview"},
        )

    changed = await consolidation.consolidate_conversation(conv_id)
    assert changed is False
    assert episodic_calls == []  # episodic write never reached mid-turn
    async with session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conv_id)
        assert conv.memory_synced_at is None  # watermark NOT advanced — retry later

    # Turn settles (frame claimed on resume / finished) → next pass runs normally.
    async with session_factory() as session:
        await PausedTurnRepository(session).delete(msg_id)
    await consolidation.consolidate_conversation(conv_id)
    assert episodic_calls == [conv_id]
    async with session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conv_id)
        assert conv.memory_synced_at is not None


async def test_fresh_lease_blocks_but_stale_lease_does_not(session_factory):
    user_id, conv_id, msg_id = await _seed_turn(session_factory)
    async with session_factory() as session:
        # No pause frame, no lease → not open.
        assert not await consolidation.conversation_turn_open(session, conv_id)

        # Fresh RUNNING lease → open (live turn in flight).
        await TurnLeaseRepository(session).upsert(
            message_id=msg_id,
            conversation_id=conv_id,
            user_id=user_id,
            owner_id="owner-1",
        )
        assert await consolidation.conversation_turn_open(session, conv_id)

        # Heartbeat past the TTL = crash leftover — must not block consolidation.
        stale = datetime.now(UTC) - timedelta(
            seconds=settings.turn_lease_ttl_seconds + 5
        )
        await session.execute(
            update(TurnLeaseRow)
            .where(TurnLeaseRow.message_id == msg_id)
            .values(heartbeat_at=stale)
        )
        await session.commit()
        assert not await consolidation.conversation_turn_open(session, conv_id)


async def test_paused_usage_without_pause_row_defers_without_watermark(
    session_factory, episodic_calls
):
    """Sidecar pause latches ``usage.paused`` and never writes ``paused_turns``."""
    _user_id, conv_id, msg_id = await _seed_turn(session_factory)
    await _mark_assistant_usage(
        session_factory,
        msg_id,
        {"status": "running", "paused": True, "finish_reason": "paused"},
    )

    async with session_factory() as session:
        assert await consolidation.conversation_turn_open(session, conv_id)

    changed = await consolidation.consolidate_conversation(conv_id)
    assert changed is False
    assert episodic_calls == []
    async with session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conv_id)
        assert conv.memory_synced_at is None


async def _mark_assistant_usage(session_factory, msg_id: str, usage: dict) -> None:
    from sqlalchemy import select

    from agentcore.db.models import Message

    async with session_factory() as session:
        row = (
            await session.execute(select(Message).where(Message.id == msg_id))
        ).scalar_one()
        row.usage = usage
        await session.commit()


async def test_cancelled_turn_skips_episodic_and_advances_watermark(
    session_factory, episodic_calls
):
    """cancelled / incomplete latest turn must not write episodic (swepper + debounce)."""
    _user_id, conv_id, msg_id = await _seed_turn(session_factory)
    await _mark_assistant_usage(
        session_factory,
        msg_id,
        {
            "status": "incomplete",
            "incomplete": True,
            "finish_reason": "cancelled",
        },
    )

    changed = await consolidation.consolidate_conversation(conv_id)
    assert changed is False
    assert episodic_calls == []
    async with session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conv_id)
        assert conv.memory_synced_at is not None  # watermark advanced — no retry loop


async def test_end_turn_still_consolidates(session_factory, episodic_calls):
    """Normal completion path is unchanged."""
    _user_id, conv_id, msg_id = await _seed_turn(session_factory)
    await _mark_assistant_usage(
        session_factory,
        msg_id,
        {"status": "complete", "finish_reason": "end_turn"},
    )

    changed = await consolidation.consolidate_conversation(conv_id)
    assert changed is True
    assert episodic_calls == [conv_id]
