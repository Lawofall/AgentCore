"""Sidecar occupy holds a cloud TurnLease; crash sweeper must not redrive it."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.conversation import local_turn as local_turn_mod
from agentcore.conversation.local_turn import (
    abort_local_turn,
    begin_local_turn,
    heartbeat_local_turn,
    record_local_turn,
)
from agentcore.runtime.leases.service import (
    LOCAL_TURN_LEASE_OWNER_PREFIX,
    is_local_turn_lease,
    local_turn_lease_owner_id,
)

pytestmark = pytest.mark.anyio

_TRACE = "0123456789abcdef0123456789abcdef"
_UMID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_MID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class _FakeSessionCM:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_exc):
        return False


def test_local_turn_lease_owner_is_stable_and_prefixed():
    owner = local_turn_lease_owner_id(_MID)
    assert owner.startswith(LOCAL_TURN_LEASE_OWNER_PREFIX)
    assert owner == local_turn_lease_owner_id(_MID)
    assert is_local_turn_lease(SimpleNamespace(owner_id=owner, meta={}))
    assert is_local_turn_lease(SimpleNamespace(owner_id="cloud-worker", meta={"source": "local"}))
    assert not is_local_turn_lease(SimpleNamespace(owner_id="cloud-worker", meta={}))


async def test_begin_acquires_local_turn_lease(monkeypatch):
    acquired: list[dict] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def create(self, **kw):
            return SimpleNamespace(id=kw["message_id"])

        async def get_by_id(self, message_id, *, conversation_id):
            return None

    store = SimpleNamespace(begin_turn=AsyncMock())
    monkeypatch.setattr(local_turn_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(local_turn_mod, "MessageRepository", Repo)
    monkeypatch.setattr(local_turn_mod, "get_cloud_store", lambda: store)

    async def _acq(**kw):
        acquired.append(kw)

    monkeypatch.setattr(local_turn_mod, "_acquire_local_turn_lease", _acq)

    await begin_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hello",
        user_message_id=_UMID,
        message_id=_MID,
        trace_id=_TRACE,
    )
    assert acquired == [
        {
            "message_id": _MID,
            "conversation_id": "c1",
            "user_id": "u1",
            "trace_id": _TRACE,
        }
    ]


async def test_abort_releases_local_turn_lease(monkeypatch):
    released: list[str | None] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def get_by_id(self, message_id, *, conversation_id):
            return SimpleNamespace(id=_MID, role="assistant", usage={"status": "running"})

    monkeypatch.setattr(local_turn_mod, "async_session_factory", lambda: _FakeSessionCM())
    monkeypatch.setattr(local_turn_mod, "MessageRepository", Repo)
    monkeypatch.setattr(local_turn_mod, "delete_assistant_and_paired_user", AsyncMock())

    async def _rel(message_id):
        released.append(message_id)

    monkeypatch.setattr(local_turn_mod, "_release_local_turn_lease", _rel)

    await abort_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message_id=_UMID,
        message_id=_MID,
    )
    assert released == [_MID]


async def test_heartbeat_uses_local_owner(monkeypatch):
    calls: list[tuple[str, str, str | None]] = []

    async def _hb(message_id, *, owner_id, conversation_id=None, **_k):
        calls.append((message_id, owner_id, conversation_id))
        return True

    monkeypatch.setattr(local_turn_mod, "heartbeat_turn_lease", _hb)
    monkeypatch.setattr(local_turn_mod.settings, "turn_lease_enabled", True)

    ok = await heartbeat_local_turn(conversation_id="c1", message_id=_MID)
    assert ok is True
    assert calls == [(_MID, local_turn_lease_owner_id(_MID), "c1")]


async def test_record_local_turn_releases_lease(monkeypatch):
    released: list[str | None] = []

    async def _persist(**_kw):
        return {"noop": True}

    async def _rel(message_id):
        released.append(message_id)

    monkeypatch.setattr(local_turn_mod, "_persist_local_turn", _persist)
    monkeypatch.setattr(local_turn_mod, "_release_local_turn_lease", _rel)

    result = await record_local_turn(
        conversation_id="c1",
        user_id="u1",
        user_message="hi",
        assistant_content="",
        user_message_id=_UMID,
        message_id=_MID,
        trace_id=_TRACE,
    )
    assert result == {"noop": True}
    assert released == [_MID]


async def test_sweeper_releases_local_lease_without_recover(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from agentcore.runtime.leases import sweeper as sweeper_mod

    message_id = _MID
    expired_row = SimpleNamespace(
        message_id=message_id,
        conversation_id="c1",
        user_id="u1",
        owner_id=local_turn_lease_owner_id(message_id),
        phase="running",
        meta={"source": "local"},
        heartbeat_at=datetime.now(UTC) - timedelta(hours=1),
    )
    released: list[str] = []
    recover_calls: list = []

    async def _fake_recover(lease, state):
        recover_calls.append(lease.message_id)

    class _FakeLeaseRepo:
        def __init__(self, _session):
            pass

        async def list_expired(self, *, before, limit):
            return [expired_row]

        async def claim_expired(self, *a, **k):
            raise AssertionError("local occupy must not be claimed for cloud recover")

        async def release(self, mid, *, owner_id=None):
            released.append(mid)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(sweeper_mod, "TurnLeaseRepository", _FakeLeaseRepo)
    monkeypatch.setattr(sweeper_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(sweeper_mod.settings, "turn_lease_enabled", True)
    monkeypatch.setattr(
        "agentcore.runtime.recover.recover_expired_lease",
        _fake_recover,
    )
    sweeper_mod._recovering_message_ids.clear()
    sweeper_mod._recover_tasks.clear()
    monkeypatch.setattr(
        sweeper_mod.asyncio,
        "create_task",
        MagicMock(side_effect=AssertionError("must not start recover")),
    )

    started = await sweeper_mod.run_turn_lease_sweep()
    assert started == 0
    assert released == [message_id]
    assert recover_calls == []
