"""Integration: turn_journal is cleaned with its owning message / conversation.

Backed by real PostgreSQL via ``session_factory`` (auto-skips when none reachable).
Pins journal cleanup: conversation hard-delete uses ``fk_turn_journal_conversation_id``;
regenerate / single-message delete still drop rows in the repository, because
``turn_id`` is not a foreign key to ``messages``. A cross-conversation id touches
neither the message nor its journal.

A paused turn's frame AND its recorded outcome ride the same cascade: a turn that is
regenerated away must read as「已重新生成」, never answer a「继续」with the decision
its previous life was settled by.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import update

from agentcore.db.models import Conversation, Message
from agentcore.db.repositories import (
    ConversationRepository,
    MessageRepository,
    PausedTurnRepository,
    TurnJournalRepository,
    TurnStreamStateRepository,
)

_ENTRIES = [{"kind": "run_plan", "payload": {}, "ts": "t"}]


async def _ensure_conversation(s, cid: str) -> None:
    if await s.get(Conversation, cid) is None:
        s.add(Conversation(id=cid, user_id=str(uuid4())))
        await s.flush()


async def _seed_turn(s, *, cid: str, mid: str) -> None:
    """One assistant message + its journal row (same id), as a completed turn writes."""
    await _ensure_conversation(s, cid)
    await MessageRepository(s).create(
        conversation_id=cid, role="assistant", content="x", message_id=mid
    )
    await TurnJournalRepository(s).record(
        turn_id=mid, conversation_id=cid, trace_id=None, entries=_ENTRIES
    )


async def test_conversation_hard_delete_clears_turn_journal(session_factory):
    cid = str(uuid4())
    m1, m2 = str(uuid4()), str(uuid4())
    async with session_factory() as s:
        await _seed_turn(s, cid=cid, mid=m1)
        await _seed_turn(s, cid=cid, mid=m2)

    async with session_factory() as s:
        await ConversationRepository(s).hard_delete(cid)

    async with session_factory() as s:
        repo = TurnJournalRepository(s)
        assert await repo.load(m1) == []
        assert await repo.load(m2) == []


async def test_delete_after_clears_only_truncated_turns_journal(session_factory):
    # regenerate / edit-and-resend truncates the tail; only the dropped turns' journal
    # goes — the kept turn's replay stream stays.
    cid = str(uuid4())
    keep, drop = str(uuid4()), str(uuid4())
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with session_factory() as s:
        await _seed_turn(s, cid=cid, mid=keep)
        await _seed_turn(s, cid=cid, mid=drop)
        # Pin created_at: keep at t0, drop one minute later (strictly after t0).
        await s.execute(update(Message).where(Message.id == keep).values(created_at=t0))
        await s.execute(
            update(Message).where(Message.id == drop).values(created_at=t0 + timedelta(minutes=1))
        )
        await s.commit()

    async with session_factory() as s:
        removed = await MessageRepository(s).delete_after(cid, after_created_at=t0)
    assert removed == 1  # only `drop`

    async with session_factory() as s:
        repo = TurnJournalRepository(s)
        assert await repo.load(keep) == _ENTRIES  # kept turn's journal survives
        assert await repo.load(drop) == []  # truncated turn's journal gone


async def _seed_paused(s, *, cid: str, mid: str, uid: str | None = None) -> None:
    await PausedTurnRepository(s).upsert(
        message_id=mid,
        conversation_id=cid,
        user_id=uid or str(uuid4()),
        frame={"kind": "plan_review", "checkpoint_id": "ck1", "message_id": mid},
    )


async def _seed_settled(s, *, cid: str, mid: str) -> None:
    """A pause that was already answered — frame gone, the winner's conclusion left."""
    await _seed_paused(s, cid=cid, mid=mid)
    await PausedTurnRepository(s).claim(mid, conversation_id=cid, decision="continue")


async def test_delete_by_id_clears_the_settled_conclusion(session_factory):
    """回合被删掉，卡的结论也随之走——否则重新生成后那张卡会答出上一轮的决策。"""
    cid, mid = str(uuid4()), str(uuid4())
    async with session_factory() as s:
        await _seed_turn(s, cid=cid, mid=mid)
        await _seed_settled(s, cid=cid, mid=mid)
        assert await PausedTurnRepository(s).get_outcome(mid, conversation_id=cid) is not None

    async with session_factory() as s:
        assert await MessageRepository(s).delete_by_id(mid, conversation_id=cid) is True

    async with session_factory() as s:
        assert await PausedTurnRepository(s).get_outcome(mid, conversation_id=cid) is None


async def test_delete_after_clears_only_truncated_conclusions(session_factory):
    cid = str(uuid4())
    keep, drop = str(uuid4()), str(uuid4())
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with session_factory() as s:
        await _seed_turn(s, cid=cid, mid=keep)
        await _seed_turn(s, cid=cid, mid=drop)
        await _seed_settled(s, cid=cid, mid=keep)
        await _seed_settled(s, cid=cid, mid=drop)
        await s.execute(update(Message).where(Message.id == keep).values(created_at=t0))
        await s.execute(
            update(Message).where(Message.id == drop).values(created_at=t0 + timedelta(minutes=1))
        )
        await s.commit()

    async with session_factory() as s:
        assert await MessageRepository(s).delete_after(cid, after_created_at=t0) == 1

    async with session_factory() as s:
        repo = PausedTurnRepository(s)
        assert await repo.get_outcome(keep, conversation_id=cid) is not None
        assert await repo.get_outcome(drop, conversation_id=cid) is None


async def test_delete_by_id_clears_paused_turn(session_factory):
    cid, mid = str(uuid4()), str(uuid4())
    async with session_factory() as s:
        await _seed_turn(s, cid=cid, mid=mid)
        await _seed_paused(s, cid=cid, mid=mid)

    async with session_factory() as s:
        hit = await MessageRepository(s).delete_by_id(mid, conversation_id=cid)
    assert hit is True

    async with session_factory() as s:
        assert await PausedTurnRepository(s).get(mid) is None


async def test_delete_after_clears_only_truncated_paused_turns(session_factory):
    cid = str(uuid4())
    keep, drop = str(uuid4()), str(uuid4())
    uid = str(uuid4())
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with session_factory() as s:
        await _seed_turn(s, cid=cid, mid=keep)
        await _seed_turn(s, cid=cid, mid=drop)
        await _seed_paused(s, cid=cid, mid=keep, uid=uid)
        await _seed_paused(s, cid=cid, mid=drop, uid=uid)
        await s.execute(update(Message).where(Message.id == keep).values(created_at=t0))
        await s.execute(
            update(Message).where(Message.id == drop).values(created_at=t0 + timedelta(minutes=1))
        )
        await s.commit()

    async with session_factory() as s:
        removed = await MessageRepository(s).delete_after(cid, after_created_at=t0)
    assert removed == 1

    async with session_factory() as s:
        repo = PausedTurnRepository(s)
        assert await repo.get(keep) is not None
        assert await repo.get(drop) is None


async def _seed_stream(s, *, mid: str, text: str = "partial") -> None:
    await TurnStreamStateRepository(s).upsert(
        turn_id=mid, channel="captain:content", text=text, generation=0
    )


async def test_conversation_hard_delete_clears_turn_stream_state(session_factory):
    cid = str(uuid4())
    m1, m2 = str(uuid4()), str(uuid4())
    async with session_factory() as s:
        await _seed_turn(s, cid=cid, mid=m1)
        await _seed_turn(s, cid=cid, mid=m2)
        await _seed_stream(s, mid=m1)
        await _seed_stream(s, mid=m2)

    async with session_factory() as s:
        await ConversationRepository(s).hard_delete(cid)

    async with session_factory() as s:
        repo = TurnStreamStateRepository(s)
        assert await repo.list_for_turn(m1) == []
        assert await repo.list_for_turn(m2) == []


async def test_delete_after_clears_only_truncated_stream_state(session_factory):
    cid = str(uuid4())
    keep, drop = str(uuid4()), str(uuid4())
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with session_factory() as s:
        await _seed_turn(s, cid=cid, mid=keep)
        await _seed_turn(s, cid=cid, mid=drop)
        await _seed_stream(s, mid=keep, text="keep")
        await _seed_stream(s, mid=drop, text="drop")
        await s.execute(update(Message).where(Message.id == keep).values(created_at=t0))
        await s.execute(
            update(Message).where(Message.id == drop).values(created_at=t0 + timedelta(minutes=1))
        )
        await s.commit()

    async with session_factory() as s:
        assert await MessageRepository(s).delete_after(cid, after_created_at=t0) == 1

    async with session_factory() as s:
        repo = TurnStreamStateRepository(s)
        kept = await repo.list_for_turn(keep)
        assert len(kept) == 1 and kept[0].text == "keep"
        assert await repo.list_for_turn(drop) == []


async def test_delete_by_id_clears_stream_state_and_is_idor_safe(session_factory):
    cid, other_cid = str(uuid4()), str(uuid4())
    mid = str(uuid4())
    async with session_factory() as s:
        await _seed_turn(s, cid=cid, mid=mid)
        await _seed_stream(s, mid=mid)

    async with session_factory() as s:
        hit = await MessageRepository(s).delete_by_id(mid, conversation_id=other_cid)
    assert hit is False
    async with session_factory() as s:
        assert len(await TurnStreamStateRepository(s).list_for_turn(mid)) == 1

    async with session_factory() as s:
        hit = await MessageRepository(s).delete_by_id(mid, conversation_id=cid)
    assert hit is True
    async with session_factory() as s:
        assert await TurnStreamStateRepository(s).list_for_turn(mid) == []


async def test_delete_by_id_clears_journal_and_is_idor_safe(session_factory):
    cid, other_cid = str(uuid4()), str(uuid4())
    mid = str(uuid4())
    async with session_factory() as s:
        await _seed_turn(s, cid=cid, mid=mid)

    # Wrong conversation → neither the message nor its journal is touched (IDOR-safe).
    async with session_factory() as s:
        hit = await MessageRepository(s).delete_by_id(mid, conversation_id=other_cid)
    assert hit is False
    async with session_factory() as s:
        assert await TurnJournalRepository(s).load(mid) == _ENTRIES

    # Right conversation → message + journal both removed.
    async with session_factory() as s:
        hit = await MessageRepository(s).delete_by_id(mid, conversation_id=cid)
    assert hit is True
    async with session_factory() as s:
        assert await TurnJournalRepository(s).load(mid) == []
