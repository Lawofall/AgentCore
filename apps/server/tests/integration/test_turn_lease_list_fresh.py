"""``list_fresh_for_user`` excludes soft-deleted and already-gone conversations."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agentcore.db.models import Conversation
from agentcore.runtime.leases.repo import TurnLeaseRepository


async def test_list_fresh_for_user_skips_soft_deleted_and_missing(session_factory):
    """软删的会话不进 turn-activity snapshot。父行不在时，租约写不进去。"""
    from agentcore.fulfill.user_signal import turn_activity_snapshot_frame

    u1 = str(uuid4())
    c_live, c_del = str(uuid4()), str(uuid4())
    m_live, m_del = str(uuid4()), str(uuid4())
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    async with session_factory() as s:
        s.add(Conversation(id=c_live, user_id=u1, title="live"))
        s.add(
            Conversation(
                id=c_del, user_id=u1, title="deleted", deleted_at=datetime.now(UTC)
            )
        )
        await s.commit()
        repo = TurnLeaseRepository(s)
        await repo.upsert(
            message_id=m_live, conversation_id=c_live, user_id=u1, owner_id="o1"
        )
        await repo.upsert(
            message_id=m_del, conversation_id=c_del, user_id=u1, owner_id="o1"
        )

    async with session_factory() as s:
        rows = await TurnLeaseRepository(s).list_fresh_for_user(u1, after=cutoff)
    ids = [r.conversation_id for r in rows]
    assert ids == [c_live]
    snap = turn_activity_snapshot_frame(ids)
    assert snap["payload"]["running"] == [c_live]
